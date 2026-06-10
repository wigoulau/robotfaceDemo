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
# 预定义嘴型（原始PWM，校准前）
# ==========================
VISEME_POSES = {
    "A": {"jaw": 1200, "smile_r": 1500, "smile_l": 1500, "lip_up": 1650},   # 中张嘴
    "O": {"jaw": 1300, "smile_r": 1350, "smile_l": 1350, "lip_up": 1550},   # 小张嘴
    "E": {"jaw": 1380, "smile_r": 1850, "smile_l": 1850, "lip_up": 1600},   # 微张嘴
    "U": {"jaw": 1420, "smile_r": 1250, "smile_l": 1250, "lip_up": 1500},   # 接近闭合
}

# 嘴巴闭合状态
MOUTH_CLOSED = {"jaw": 1450, "smile_r": 1500, "smile_l": 1500, "lip_up": 1500}

# 眼睛睁开状态
EYES_OPEN = {
    "blink_r": 2000, "blink_l": 2000,
    "eye_lr_r": 1500, "eye_lr_l": 1500,
    "eye_ud_r": 1500, "eye_ud_l": 1500,
}

# 眼睛闭合状态
EYES_CLOSED = {
    "blink_r": 1100, "blink_l": 1100,
}


def _joint_to_servo_pose(joint_pose):
    """将 {关节名: pwm} 转为 {舵机ID: pwm}"""
    return {JOINT_MAP[k]: v for k, v in joint_pose.items() if k in JOINT_MAP}


def _servo_to_joint_pose(servo_pose):
    """将 {舵机ID: pwm} 转为 {关节名: pwm}"""
    return {SERVO_TO_JOINT.get(k, f"servo_{k}"): v for k, v in servo_pose.items()}


class JointController:
    """关节控制器：高层抽象 + 平滑 + 硬件/模拟双输出"""

    HW_TICK_INTERVAL = 0.025  # 25ms ≈ 40fps

    def __init__(self, servo_iface=None, face_display=None, interpolator=None):
        """
        servo_iface: ServoInterface 实例（硬件输出，可为 None）
        face_display: FaceDisplay 实例（模拟显示输出，可为 None）
        interpolator: ServoInterpolator 实例（可为 None，内部会创建）
        """
        self._servo = servo_iface      # ServoInterface
        self._face = face_display      # FaceDisplay

        # 插值器：硬件舵机独立实例（与 face_display 的插值器分开，tick 频率不同）
        if interpolator is None:
            interpolator = ServoInterpolator(mode="lerp", tick_interval=0.016)
        self._interp = interpolator    # 面部/上层用插值器

        # 硬件插值器（tick 间隔匹配硬件发送频率）
        self._hw_interp = ServoInterpolator(mode="lerp", tick_interval=self.HW_TICK_INTERVAL)

        # 硬件 tick 线程
        self._tick_stop = threading.Event()
        self._tick_thread = None

    # ---- 生命周期 ----

    def start(self):
        """启动硬件舵机插值 tick 线程（即使未连接也安全启动）"""
        if self._tick_thread is not None:
            return  # 已启动
        self._tick_stop.clear()
        self._tick_thread = threading.Thread(target=self._hw_tick_loop, daemon=True)
        self._tick_thread.start()
        log.info("硬件舵机 tick 线程已启动 (%.0fms)", self.HW_TICK_INTERVAL * 1000)
    def stop(self):
        """停止硬件 tick 线程"""
        self._tick_stop.set()
        if self._tick_thread:
            self._tick_thread.join(timeout=1)
            self._tick_thread = None
        log.info("硬件舵机 tick 线程已停止")

    def _hw_tick_loop(self):
        """以固定频率驱动硬件舵机插值"""
        while not self._tick_stop.is_set():
            updates = self._hw_interp.tick()
            if self._servo and updates:
                for sid, pwm in updates.items():
                    self._servo.send_pwm(sid, int(pwm))
            time.sleep(self.HW_TICK_INTERVAL)

    # ---- 底层：设置舵机目标 ----

    def set_servo_target(self, servo_id, pwm, preset="mouth"):
        """
        设置单个舵机目标，走插值器平滑过渡。
        同时驱动硬件和面部显示。
        """
        # 硬件插值器
        if self._servo and self._servo.is_connected():
            self._hw_interp.set_target(servo_id, pwm, preset=preset)

        # 面部显示插值器
        if self._face:
            self._face.set_target(servo_id, pwm, preset=preset)

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
        if self._servo and self._servo.is_connected():
            self._hw_interp.set_instant(servo_id, pwm)
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

    def eyes_open(self, preset="blink"):
        """睁眼"""
        log.debug("睁眼")
        self.apply_joint_pose(EYES_OPEN, preset=preset)

    def eyes_close(self, speed=25):
        """闭眼"""
        log.debug("闭眼")
        for name, pwm in EYES_CLOSED.items():
            self.set_joint_target(name, pwm, preset="blink")

    def eye_saccade(self, lr_offset=0, ud_offset=0):
        """
        眼球转动。
        lr_offset: 左右偏移量 -150~+150（负=左, 正=右）
        ud_offset: 上下偏移量 -80~+80（负=上, 正=下）
        """
        self.set_joint_target("eye_lr_r", 1500 + lr_offset, preset="expression")
        self.set_joint_target("eye_lr_l", 1500 + lr_offset, preset="expression")
        self.set_joint_target("eye_ud_r", 1500 + ud_offset, preset="expression")
        self.set_joint_target("eye_ud_l", 1500 + ud_offset, preset="expression")

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
            "jaw": 1450, "smile_r": 1500, "smile_l": 1500, "lip_up": 1500,
            "blink_r": 2000, "blink_l": 2000,
            "eye_lr_r": 1500, "eye_lr_l": 1500,
            "eye_ud_r": 1500, "eye_ud_l": 1500,
            "neck": 1500, "brow": 1500, "tongue": 1500,
        }
        self.apply_joint_pose(defaults, preset="reset")


# ==============================
# 快速测试
# ==============================
if __name__ == "__main__":
    log.info("=== JointController 测试 ===")

    jc = JointController()  # 无硬件、无面部

    log.info("测试: 张嘴 A viseme rms=0.8")
    jc.mouth_viseme("A", rms=0.8)

    log.info("测试: 闭嘴复位")
    jc.mouth_reset()

    log.info("测试: 微笑 both amount=0.8")
    jc.smile(amount=0.8)

    log.info("测试: 眨眼闭眼")
    jc.eyes_close()

    log.info("测试: 复位所有")
    jc.reset_all()

    log.info("测试完成")
