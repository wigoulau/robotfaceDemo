"""
舵机平滑插值器
支持两种模式：
  - lerp: 指数衰减线性插值（快速、稳定）
  - spring: 弹簧物理模型（有轻微回弹，更自然）

用法：
    interp = ServoInterpolator(mode="lerp")   # 或 mode="spring"
    interp.set_target(servo_id, pwm, speed=12)
    # 每 16ms 调用一次
    updates = interp.tick()  # {servo_id: pwm_int}
"""


class ServoInterpolator:
    """舵机平滑插值器，支持 lerp 和 spring 两种模式"""

    # 默认速度配置（按动作类型）
    SPEED_PRESETS = {
        "mouth": 12,      # 说话嘴型：快速响应
        "expression": 4,  # 表情动作：柔和过渡
        "blink": 15,      # 眨眼：快速到位
        "slider": 15,     # 滑杆拖动：即时感
        "reset": 5,       # 复位：平滑回归
    }

    # 弹簧模式默认参数
    SPRING_STIFFNESS = 250.0   # 刚度：越高越快到位
    SPRING_DAMPING = 18.0      # 阻尼：越高越少回弹

    def __init__(self, mode="lerp", tick_interval=0.016):
        """
        mode: "lerp" 或 "spring"
        tick_interval: tick 间隔（秒），默认 16ms ≈ 60fps
        """
        assert mode in ("lerp", "spring"), f"不支持的模式: {mode}"
        self.mode = mode
        self.dt = tick_interval

        # 每个舵机的状态
        self._current = {}    # servo_id → 当前PWM (float)
        self._target = {}     # servo_id → 目标PWM (int)
        self._speed = {}      # servo_id → lerp 速度 (每秒进度倍数)
        self._velocity = {}   # servo_id → spring 速度 (PWM/秒)

    # ---- 公共接口 ----

    def set_target(self, servo_id, pwm, speed=None, preset="mouth"):
        """
        设置目标 PWM 值。
        speed: 直接指定速度（优先）
        preset: 使用预设速度 ("mouth"/"expression"/"blink"/"slider"/"reset")
        """
        if speed is None:
            speed = self.SPEED_PRESETS.get(preset, 10)

        self._target[servo_id] = int(pwm)
        self._speed[servo_id] = speed

        # 首次设置时直接到位（避免初始跳变）
        if servo_id not in self._current:
            self._current[servo_id] = float(pwm)
            self._velocity[servo_id] = 0.0

    def set_instant(self, servo_id, pwm):
        """即时设置（无插值），用于首次初始化或紧急复位"""
        self._current[servo_id] = float(pwm)
        self._target[servo_id] = int(pwm)
        self._velocity[servo_id] = 0.0

    def get_current(self, servo_id):
        """获取当前插值后的 PWM 值"""
        return int(self._current.get(servo_id, 0))

    def tick(self):
        """
        每 tick_interval 调用一次。
        返回: {servo_id: int_pwm} 需要更新的舵机
        """
        updates = {}

        for sid, target in self._target.items():
            cur = self._current.get(sid, float(target))
            diff = target - cur

            # 死区：差值太小直接到位
            if abs(diff) < 1.5:
                if abs(diff) > 0.01:
                    self._current[sid] = float(target)
                    self._velocity[sid] = 0.0
                    updates[sid] = target
                continue

            if self.mode == "lerp":
                new_val = self._tick_lerp(sid, cur, diff)
            else:
                new_val = self._tick_spring(sid, cur, diff)

            self._current[sid] = new_val
            updates[sid] = int(round(new_val))

        return updates

    # ---- 内部实现 ----

    def _tick_lerp(self, sid, cur, diff):
        """指数衰减插值：每 tick 向目标靠近 speed*dt 比例"""
        speed = self._speed.get(sid, 10)
        # 指数衰减系数：speed=10 时约 170ms 到位，speed=5 时约 350ms
        factor = min(1.0, speed * self.dt)
        return cur + diff * factor

    def _tick_spring(self, sid, cur, diff):
        """
        简化弹簧模型：
        acceleration = stiffness * (target - current) - damping * velocity
        velocity += acceleration * dt
        current += velocity * dt
        """
        stiffness = self.SPRING_STIFFNESS
        damping = self.SPRING_DAMPING

        # 根据 speed 预设动态调整刚度
        speed = self._speed.get(sid, 10)
        # speed 越高，stiffness 越高（快速到位）
        eff_stiffness = stiffness * (speed / 10.0)

        vel = self._velocity.get(sid, 0.0)
        acc = eff_stiffness * diff - damping * vel
        vel += acc * self.dt
        self._velocity[sid] = vel

        return cur + vel * self.dt


# ==============================
# 快速测试
# ==============================
if __name__ == "__main__":
    import time

    print("=== Lerp 模式测试 (speed=10) ===")
    interp = ServoInterpolator(mode="lerp")
    interp.set_target(3, 1500, preset="mouth")  # 初始到位
    interp.set_target(3, 1200, speed=10)        # 目标 1200

    for i in range(20):
        updates = interp.tick()
        if updates:
            print(f"  tick {i:2d}: {updates}")
        time.sleep(0.016)

    print("\n=== Spring 模式测试 (speed=10) ===")
    interp2 = ServoInterpolator(mode="spring")
    interp2.set_target(3, 1500, preset="mouth")
    interp2.set_target(3, 1200, speed=10)

    for i in range(30):
        updates = interp2.tick()
        if updates:
            print(f"  tick {i:2d}: {updates}")
        time.sleep(0.016)
