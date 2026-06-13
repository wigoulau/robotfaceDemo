"""
关节控制类
抽象各个身体部位的控制（张嘴/闭嘴/微笑/眨眼等），统一处理平滑后，
同时发给舵机硬件（经 ServoInterface 校准）和模拟界面（FaceDisplay）。

用法:
    jc = JointController(servo_iface, face_display, interpolator)
    jc.mouth_pose({"A": ...}, rms=0.5)   # 说话嘴型
    jc.mouth_reset()                      # 闭嘴
    jc.blink()                            # 眨眼
    jc.start()                            # 启动硬件 tick 线程
"""

import time
import threading

from servo_interpolator import ServoInterpolator
from servo_calibration import SERVO_CALIBRATION
from servo_constants import (
    S2_NECK, S3_JAW, S4_SMILE_R, S5_SMILE_L, S6_LIP_UP,
    S7_EYE_UD_R, S8_BLINK_R, S9_EYE_LR_R,
    S10_EYE_UD_L, S11_BLINK_L, S12_EYE_LR_L,
    S13_BROW, S14_TONGUE,
)
from logger import get_logger

log = get_logger(__name__)


# ==========================
# 关节名 → 舵机ID映射
# ==========================
JOINT_MAP = {
    "neck":     S2_NECK,
    "jaw":      S3_JAW,
    "smile_r":  S4_SMILE_R,
    "smile_l":  S5_SMILE_L,
    "lip_up":   S6_LIP_UP,
    "eye_ud_r": S7_EYE_UD_R,
    "blink_r":  S8_BLINK_R,
    "eye_lr_r": S9_EYE_LR_R,
    "eye_ud_l": S10_EYE_UD_L,
    "blink_l":  S11_BLINK_L,
    "eye_lr_l": S12_EYE_LR_L,
    "brow":     S13_BROW,
    "tongue":   S14_TONGUE,
}

# 反向映射: 舵机ID → 关节名
SERVO_TO_JOINT = {v: k for k, v in JOINT_MAP.items()}


# ==========================
# 各动作类型对应的 MCU duration（ms）
# duration 控制控制板内部平滑速度，即舵机移动时长
# ==========================
DURATION_PRESETS = {
    "mouth":       80,   # 嘴型：更新频繁，短 duration 跟得上
    "expression": 300,   # 表情/眼球：柔和自然
    "blink_close": 100,  # 快速闭眼
    "blink_open":  100,  # 慢慢睁开
    "blink":       200,  # 眨眼通用
    "slider":      500,  # 滑杆手动：用户直接操控，要流畅
    "reset":       400,  # 复位：温和回归
}

# 硬件预设→面部显示预设映射（ServoInterpolator 不认识 blink_close/blink_open）
_HW_TO_DISPLAY_PRESET = {
    "blink_close": "blink",
    "blink_open":  "blink",
}
# ==========================
# 预定义嘴型（原始 PWM，校准前）
# ==========================
VISEME_POSES = {
    "A": {"jaw": 900, "smile_r": 2000, "smile_l": 2400, "lip_up": 1650},   # 中张嘴
    "O": {"jaw": 1050, "smile_r": 1900, "smile_l": 2250, "lip_up": 1550},   # 小张嘴
    "E": {"jaw": 1150, "smile_r": 1800, "smile_l": 2100, "lip_up": 1600},   # 微张嘴
    "U": {"jaw": 1300, "smile_r": 1700, "smile_l": 2000, "lip_up": 1500},   # 接近闭合
}

# 嘴巴闭合状态
MOUTH_CLOSED = {"jaw": 1300, "smile_r": 1700, "smile_l": 2000, "lip_up": 1500}

# 眼睛睁开状态
EYES_OPEN = {
    "blink_r": 1400, "blink_l": 1600,
}

# 眼睛闭合状态
EYES_CLOSED = {
    "blink_r": 2000, "blink_l": 1100,
}


def _joint_to_servo_pose(joint_pose):
    """将 {关节名: pwm} 转为 {舵机ID: pwm}"""
    return {JOINT_MAP[k]: v for k, v in joint_pose.items() if k in JOINT_MAP}


def _servo_to_joint_pose(servo_pose):
    """将 {舵机ID: pwm} 转为 {关节名: pwm}"""
    return {SERVO_TO_JOINT.get(k, f"servo_{k}"): v for k, v in servo_pose.items()}


class JointController:
    """关节控制器：高层抽象 + 硬件批量发送 + 模拟显示双输出

    硬件路径：_pending 字典 + tick 线程 → send_batch（MCU duration 控速度）
    显示路径：ServoInterpolator 插值 → face_display（纯 UI 平滑）
    """

    def __init__(self, servo_iface=None, face_display=None, interpolator=None,
                 tick_interval_ms=40):
        """
        servo_iface: ServoInterface 实例（硬件输出，可为 None）
        face_display: FaceDisplay 实例（模拟显示输出，可为 None）
        interpolator: ServoInterpolator 实例（以共享给 face_display，可为 None）
        tick_interval_ms: 硬件 tick 间隔（ms），默认 40ms = 25fps
        """
        self._servo = servo_iface
        self._face = face_display

        if interpolator is None:
            interpolator = ServoInterpolator(mode="lerp", tick_interval=0.016)
        self._interp = interpolator

        self._tick_interval = tick_interval_ms / 1000.0

        # 硬件批量发送内标：{servo_id: (pwm, duration_ms)}
        self._pending: dict = {}
        # 每个舵机最近一次发出的 PWM 位置（用于线性分步计算）
        self._last_pwm: dict = {}
        self._lock = threading.Lock()

        # 硬件 tick 线程
        self._tick_stop = threading.Event()
        self._tick_thread = None

    # ---- 生命周期 ----

    def start(self):
        """启动硬件舵机批量 tick 线程（即使未连接也安全启动）"""
        if self._tick_thread is not None:
            return
        self._tick_stop.clear()
        self._tick_thread = threading.Thread(target=self._hw_tick_loop, daemon=True)
        self._tick_thread.start()
        log.info("硬件舵机批量 tick 线程已启动 (%.0fms)", self._tick_interval * 1000)

    def stop(self):
        """停止硬件 tick 线程"""
        self._tick_stop.set()
        if self._tick_thread:
            self._tick_thread.join(timeout=1)
            self._tick_thread = None
        log.info("硬件舵机 tick 线程已停止")

    def _hw_tick_loop(self):
        """固定频率驱动舵机，对 dur > tick_interval 的命令做线性分步：

        每 tick 按 tick_ms/dur_ms 比例推进一步（dur=tick_interval），
        剩余行程放回 _pending 等待下轮，实现软件侧匀速插值。
        若 _pending 中途被新目标覆盖，下轮将从当前位置平滑过渡到新目标。
        """
        tick_ms = int(round(self._tick_interval * 1000))

        while not self._tick_stop.is_set():
            commands = []
            carry = {}  # 本轮未走完的剩余行程，tick 结束后放回 _pending

            with self._lock:
                for sid in list(self._pending.keys()):
                    target_pwm, dur_ms = self._pending.pop(sid)
                    last_pwm = self._last_pwm.get(sid, target_pwm)

                    if dur_ms > tick_ms:
                        # 线性步进：本 tick 走 tick_ms/dur_ms 比例的距离
                        frac = tick_ms / dur_ms
                        step_pwm = int(round(last_pwm + (target_pwm - last_pwm) * frac))
                        commands.append((sid, step_pwm, tick_ms))
                        self._last_pwm[sid] = step_pwm
                        carry[sid] = (target_pwm, dur_ms - tick_ms)
                    else:
                        # 最后一步（dur <= tick_ms），直接发到目标位置
                        commands.append((sid, target_pwm, max(1, int(dur_ms))))
                        self._last_pwm[sid] = target_pwm
                    log.debug("[%s] %d -> %d (%.0fms), dur_ms=%d",
                              SERVO_TO_JOINT.get(sid, f"servo_{sid}"),
                              last_pwm, step_pwm, tick_ms, dur_ms)

                # 将剩余行程放回 _pending（若期间有新目标写入则以新目标为准）
                for sid, val in carry.items():
                    if sid not in self._pending:  # 未被新 set_servo_target 覆盖
                        self._pending[sid] = val

            # log.debug("[HW] %d commands", len(commands))
            if commands and self._servo:
                self._servo.send_batch(commands)
            time.sleep(self._tick_interval)

    # ---- 底层：设置舵机目标 ----

    def set_servo_target(self, servo_id, pwm, preset="mouth", duration_ms=None):
        """
        设置单个舵机目标。
        硬件路径：写入 _pending，tick 批量发送，duration 由 MCU 控制平滑速度。
        显示路径：继续经 ServoInterpolator 平滑过渡。
        """
        if duration_ms is None:
            duration_ms = DURATION_PRESETS.get(preset, 200)

        # 硬件：写入 pending，由 tick 批量发出
        with self._lock:
            self._pending[servo_id] = (pwm, duration_ms)

        # 显示：映射到 ServoInterpolator 支持的预设
        if self._face:
            display_preset = _HW_TO_DISPLAY_PRESET.get(preset, preset)
            self._face.set_target(servo_id, pwm, preset=display_preset)

    def set_joint_target(self, joint_name, pwm, preset="mouth"):
        """按关节名设置目标"""
        sid = JOINT_MAP.get(joint_name)
        if sid is not None:
            self.set_servo_target(sid, pwm, preset=preset)
        else:
            log.warning("未知关节: %s", joint_name)

    def apply_pose(self, servo_pose, preset="mouth"):
        """批量设置舵机目标 {servo_id: pwm}"""
        for sid, pwm in servo_pose.items():
            self.set_servo_target(sid, pwm, preset=preset)

    def apply_joint_pose(self, joint_pose, preset="mouth"):
        """批量设置关节目标 {joint_name: pwm}"""
        for name, pwm in joint_pose.items():
            self.set_joint_target(name, pwm, preset=preset)

    def set_instant(self, servo_id, pwm):
        """即时设置（无插值），同时驱动硬件和面部"""
        with self._lock:
            self._pending[servo_id] = (pwm, 0)
        if self._face:
            self._face.set_instant(servo_id, pwm)

    # ---- 嘴巴 ----

    def mouth_viseme(self, viseme, rms=0.5):
        """
        根据音素和音量设置嘴型。
        viseme: "A"/"O"/"E"/"U"
        rms: 0~1 音量（影响张嘴幅度）
        """
        base = VISEME_POSES.get(viseme, VISEME_POSES["A"])
        jaw_center = MOUTH_CLOSED["jaw"]  # 1450 = 闭合
        jaw_target = base["jaw"]

        # rms 越大张嘴越大
        factor = 0.3 + rms * 0.7
        jaw = int(jaw_center + (jaw_target - jaw_center) * factor)

        pose = dict(base)
        pose["jaw"] = jaw
        self.apply_joint_pose(pose, preset="mouth")

    def mouth_reset(self):
        """复位嘴巴到闭合状态"""
        log.debug("嘴巴复位")
        self.apply_joint_pose(MOUTH_CLOSED, preset="reset")

    def mouth_open(self, amount=1.0):
        """
        张嘴。amount: 0=闭合, 1=全开。
        """
        jaw_pwm = int(1450 - amount * 450)  # 1450闭合 → 1000全开
        self.set_joint_target("jaw", jaw_pwm, preset="mouth")

    def mouth_close(self):
        """闭嘴"""
        self.set_joint_target("jaw", MOUTH_CLOSED["jaw"], preset="reset")

    def smile(self, amount=0.5, side="both"):
        """
        微笑控制。
        amount: 0=不笑, 1=最大微笑
        side: "left"/"right"/"both"
        """
        pwm = int(1500 + (amount - 0.5) * 400)
        if side in ("left", "both"):
            self.set_joint_target("smile_l", pwm, preset="expression")
        if side in ("right", "both"):
            self.set_joint_target("smile_r", pwm, preset="expression")

    # ---- 眼睛 ----

    def eyes_open(self, preset="blink_open") -> float:
        """睁眼，返回建议等待时长（秒）"""
        log.debug("睁眼")
        self.apply_joint_pose(EYES_OPEN, preset=preset)
        dur = DURATION_PRESETS.get(preset, DURATION_PRESETS["blink_open"])
        return self._tick_interval + dur / 1000.0

    def eyes_close(self) -> float:
        """闭眼，返回建议等待时长（秒）"""
        log.debug("闭眼")
        for name, pwm in EYES_CLOSED.items():
            self.set_joint_target(name, pwm, preset="blink_close")
        dur = DURATION_PRESETS["blink_close"]
        return self._tick_interval + dur / 1000.0

    def eye_saccade(self, lr_offset=0, ud_offset=0) -> float:
        """
        眼球转动。返回建议等待时长（秒）。
        lr_offset: 左右偏移量 -150~+150
        ud_offset: 上下偏移量 -80~+80
        """
        self.set_joint_target("eye_lr_r", 1450 + lr_offset, preset="expression")
        self.set_joint_target("eye_lr_l", 1550 + lr_offset, preset="expression")
        self.set_joint_target("eye_ud_r", 1650 - ud_offset, preset="expression")
        self.set_joint_target("eye_ud_l", 1350 + ud_offset, preset="expression")
        dur = DURATION_PRESETS["expression"]
        return self._tick_interval + dur / 1000.0

    # ---- 其他 ----

    def neck_set(self, angle=0.0):
        """
        脖子旋转。angle: -1~1（负=左, 正=右）
        """
        pwm = int(1500 + angle * 500)
        self.set_joint_target("neck", pwm, preset="expression")

    def brow_set(self, value=0.5):
        """
        眉毛。value: 0=下, 0.5=中, 1=上
        """
        pwm = int(1200 + value * 700)
        self.set_joint_target("brow", pwm, preset="expression")

    def tongue_set(self, amount=0.0):
        """
        舌头。amount: 0=收起, 1=全伸
        """
        pwm = int(1200 + amount * 700)
        self.set_joint_target("tongue", pwm, preset="expression")

    def reset_all(self):
        """复位所有关节到默认位置"""
        log.info("复位所有关节")
        defaults = {
            "jaw": 1300, "smile_r": 1700, "smile_l": 2000, "lip_up": 1500,
            "blink_r": 1400, "blink_l": 1600,
            "eye_lr_r": 1450, "eye_lr_l": 1550,
            "eye_ud_r": 1650, "eye_ud_l": 1350,
            "neck": 1500, "brow": 1500, "tongue": 1500,
        }
        self.apply_joint_pose(defaults, preset="reset")


# ==============================
# 快速测试
# ==============================
def test_interactive():
    """交互式测试菜单"""
    from servo_interface import ServoInterface
    from face_display import FaceDisplay
    
    print("\n=== JointController 交互式测试 ===")
    print("正在初始化硬件...")
    
    # 初始化硬件
    servo_iface = ServoInterface()
    face_display = FaceDisplay()
    
    # 创建控制器
    jc = JointController(
        servo_iface=servo_iface,
        face_display=face_display,
        interpolator=None
    )
    
    # 启动硬件 tick 线程
    jc.start()
    
    # 连接舵机
    print("正在连接舵机...")
    connected = servo_iface.connect()
    if connected:
        print("✓ 舵机连接成功")
    else:
        print("✗ 舵机未连接,仅运行模拟模式")
    
    # 显示菜单
    menu = """
=== 测试菜单 ===
1.  复位所有关节 (reset_all)
2.  嘴巴复位 (mouth_reset)
3.  嘴巴张开 (mouth_open amount=1.0)
4.  嘴巴闭合 (mouth_close)
5/A.  嘴型 A (mouth_viseme A rms=0.8)
6/O.  嘴型 O (mouth_viseme O rms=0.8)
7/E.  嘴型 E (mouth_viseme E rms=0.8)
8/U.  嘴型 U (mouth_viseme U rms=0.8)
9.  微笑 (smile amount=0.8)
10. 左微笑 (smile side=left)
11. 右微笑 (smile side=right)
12. 睁眼 (eyes_open)
13. 闭眼 (eyes_close)
14. 眼球左移 (eye_saccade lr=-150)
15. 眼球右移 (eye_saccade lr=150)
16. 眼球上移 (eye_saccade ud=-200)
17. 眼球下移 (eye_saccade ud=200)
18. 脖子左转 (neck_set angle=-0.5)
19. 脖子右转 (neck_set angle=0.5)
20. 脖子居中 (neck_set angle=0)
21. 眉毛上 (brow_set value=1.0)
22. 眉毛中 (brow_set value=0.5)
23. 眉毛下 (brow_set value=0.0)
24. 舌头伸出 (tongue_set amount=1.0)
25. 舌头收起 (tongue_set amount=0.0)
0.  退出
"""
    
    try:
        while True:
            print(menu)
            choice = input("请选择动作 (0-25): ").strip()
            
            if choice == "0":
                print("退出测试")
                break
            elif choice == "1":
                log.info("执行: 复位所有关节")
                jc.reset_all()
            elif choice == "2":
                log.info("执行: 嘴巴复位")
                jc.mouth_reset()
            elif choice == "3":
                log.info("执行: 嘴巴张开")
                jc.mouth_open(amount=1.0)
            elif choice == "4":
                log.info("执行: 嘴巴闭合")
                jc.mouth_close()
            elif choice == "5" or choice.upper() == "A":
                log.info("执行: 嘴型 A")
                jc.mouth_viseme("A", rms=0.8)
            elif choice == "6" or choice.upper() == "O":
                log.info("执行: 嘴型 O")
                jc.mouth_viseme("O", rms=0.8)
            elif choice == "7" or choice.upper() == "E":
                log.info("执行: 嘴型 E")
                jc.mouth_viseme("E", rms=0.8)
            elif choice == "8" or choice.upper() == "U":
                log.info("执行: 嘴型 U")
                jc.mouth_viseme("U", rms=0.8)
            elif choice == "9":
                log.info("执行: 微笑")
                jc.smile(amount=0.8)
            elif choice == "10":
                log.info("执行: 左微笑")
                jc.smile(amount=0.8, side="left")
            elif choice == "11":
                log.info("执行: 右微笑")
                jc.smile(amount=0.8, side="right")
            elif choice == "12":
                log.info("执行: 睁眼")
                jc.eyes_open()
            elif choice == "13":
                log.info("执行: 闭眼")
                jc.eyes_close()
            elif choice == "14":
                log.info("执行: 眼球左移")
                jc.eye_saccade(lr_offset=-150)
            elif choice == "15":
                log.info("执行: 眼球右移")
                jc.eye_saccade(lr_offset=150)
            elif choice == "16":
                log.info("执行: 眼球上移")
                jc.eye_saccade(ud_offset=-200)
            elif choice == "17":
                log.info("执行: 眼球下移")
                jc.eye_saccade(ud_offset=200)
            elif choice == "18":
                log.info("执行: 脖子左转")
                jc.neck_set(angle=-0.5)
            elif choice == "19":
                log.info("执行: 脖子右转")
                jc.neck_set(angle=0.5)
            elif choice == "20":
                log.info("执行: 脖子居中")
                jc.neck_set(angle=0.0)
            elif choice == "21":
                log.info("执行: 眉毛上")
                jc.brow_set(value=1.0)
            elif choice == "22":
                log.info("执行: 眉毛中")
                jc.brow_set(value=0.5)
            elif choice == "23":
                log.info("执行: 眉毛下")
                jc.brow_set(value=0.0)
            elif choice == "24":
                log.info("执行: 舌头伸出")
                jc.tongue_set(amount=1.0)
            elif choice == "25":
                log.info("执行: 舌头收起")
                jc.tongue_set(amount=0.0)
            else:
                print("无效选择,请输入 0-25")
            
            time.sleep(0.1)  # 防止输入太快
    
    finally:
        print("\n正在清理...")
        jc.stop()
        servo_iface.close()
        print("测试结束")


if __name__ == "__main__":
    test_interactive()
