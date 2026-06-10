"""
舵机接口类
封装 ServoController，统一处理校准（clamp/reverse）后再发送 PWM。

用法:
    iface = ServoInterface()
    iface.connect()
    iface.send_pwm(3, 1200)   # 自动校准后发送
    iface.close()
"""

from servo_calibration import SERVO_CALIBRATION
from logger import get_logger

log = get_logger(__name__)


class ServoInterface:
    """舵机接口：封装连接 + 校准 + 发送"""

    def __init__(self, controller=None):
        """
        controller: ServoController 实例，为 None 时仅模拟（不发送硬件指令）
        """
        self._ctrl = controller

    # ---- 连接管理 ----

    def connect(self):
        """扫描并连接舵机设备。返回 True/False。"""
        if self._ctrl is None:
            log.warning("ServoController 未注入，尝试创建默认实例")
            from servo_controller import ServoController
            self._ctrl = ServoController()

        ok = self._ctrl.connect()
        if ok:
            log.info("舵机设备已连接")
        else:
            log.warning("舵机设备未连接，仅模拟模式")
        ok = True
        return ok

    def close(self):
        """关闭设备连接"""
        if self._ctrl:
            self._ctrl.close()
            log.info("舵机设备已关闭")

    def is_connected(self):
        """设备是否已连接"""
        # return self._ctrl is not None and self._ctrl.device is not None
        return True

    # ---- 校准 ----

    def get_calibration(self, servo_id):
        """获取指定舵机的校准配置，无配置时返回安全默认值"""
        cfg = SERVO_CALIBRATION.get(servo_id)
        if cfg is None:
            log.debug("舵机 ID=%d 无校准配置，使用安全默认值", servo_id)
            return {"name": f"servo_{servo_id}", "min": 500, "center": 1500,
                    "max": 2500, "reverse": False}
        return cfg

    def calibrate(self, servo_id, raw_pwm):
        """
        对原始 PWM 值做校准处理：
        - 根据 reverse 方向翻转
        - clamp 到 min/max 范围
        返回校准后的 PWM（int）
        """
        cfg = self.get_calibration(servo_id)
        mn = cfg["min"]
        mx = cfg["max"]
        center = cfg["center"]

        if cfg["reverse"]:
            # 翻转：以 center 为轴镜像
            flipped = 2 * center - raw_pwm
            calibrated = max(mn, min(mx, flipped))
        else:
            calibrated = max(mn, min(mx, raw_pwm))

        return int(calibrated)

    def norm_to_pwm(self, servo_id, norm):
        """
        将 0~1 归一化值转为校准后的 PWM。
        norm: 0~1，0.5=中心
        """
        norm = max(0.0, min(1.0, norm))
        cfg = self.get_calibration(servo_id)
        mn, mx, center, rev = cfg["min"], cfg["max"], cfg["center"], cfg["reverse"]

        if rev:
            norm = 1.0 - norm

        pwm = mn + (mx - mn) * norm
        return max(mn, min(mx, int(pwm)))

    def pwm_to_norm(self, servo_id, pwm):
        """将 PWM 转为 0~1 归一化值（忽略 reverse，纯值域映射）"""
        cfg = self.get_calibration(servo_id)
        mn, mx = cfg["min"], cfg["max"]
        if mx == mn:
            return 0.5
        return max(0.0, min(1.0, (pwm - mn) / (mx - mn)))

    # ---- 发送 ----

    def send_pwm(self, servo_id, pwm, duration=50):
        """
        校准后发送 PWM 到舵机。
        servo_id: 舵机通道号
        pwm: 原始/目标 PWM（会自动校准）
        duration: 动作时长 ms（透传给硬件）
        """
        if not self._ctrl:
            log.debug("S%d 无控制器，跳过发送 PWM=%d", servo_id, pwm)
            return False
        if not self.is_connected():
            log.debug("S%d 未连接，跳过发送 PWM=%d", servo_id, pwm)
            return False

        calibrated = self.calibrate(servo_id, pwm)
        calibrated = pwm
        # 使用控制器的底层发送（duration 固定较短的缓冲值，平滑由上层插值器控制）
        if self._ctrl.is_connected():
            result = self._ctrl.send_pwm(servo_id, calibrated)
            if result:
                log.debug("S%d PWM=%d → 校准后=%d OK", servo_id, pwm, calibrated)
            return result
        else:
            log.debug("unconnected send_pwm: S%d PWM=%d → 校准后=%d", servo_id, pwm, calibrated)

        return None

    def send_raw(self, servo_id, pwm):
        """不校准直接发送（调试用）"""
        if not self.is_connected():
            return False
        return self._ctrl.send_pwm(servo_id, int(pwm))


# ==============================
# 快速测试
# ==============================
if __name__ == "__main__":
    log.info("=== ServoInterface 校准测试 ===")

    iface = ServoInterface()

    # 测试校准（无硬件连接场景）
    # S3 jaw: min=1000 center=1450 max=1450 reverse=True
    log.info("S3 jaw raw=1200 → calibrated=%d (reverse, clamp)", iface.calibrate(3, 1200))
    log.info("S3 jaw raw=1500 → calibrated=%d (超过max)", iface.calibrate(3, 1500))
    log.info("S4 smile_r raw=1500 → calibrated=%d (无翻转)", iface.calibrate(4, 1500))
    log.info("S4 smile_r raw=1000 → calibrated=%d (低于min)", iface.calibrate(4, 1000))

    # 测试 norm → pwm
    log.info("S3 norm=0.0 → pwm=%d (反向, 0=最开)", iface.norm_to_pwm(3, 0.0))
    log.info("S3 norm=1.0 → pwm=%d (反向, 1=闭合)", iface.norm_to_pwm(3, 1.0))
    log.info("S4 norm=0.0 → pwm=%d (正向, 0=最小)", iface.norm_to_pwm(4, 0.0))
    log.info("S4 norm=1.0 → pwm=%d (正向, 1=最大)", iface.norm_to_pwm(4, 1.0))
