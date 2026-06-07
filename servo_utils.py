from servo_calibration import SERVO_CALIBRATION


def clamp(v, vmin, vmax):
    return max(vmin, min(v, vmax))


def percent_to_pwm(
        servo_id,
        percent):
    """
    percent:
        0~100
    """

    cfg = SERVO_CALIBRATION[servo_id]

    mn = cfg["min"]
    mx = cfg["max"]

    if cfg["reverse"]:
        percent = 100 - percent

    pwm = mn + (
        (mx - mn)
        * percent
        / 100.0
    )

    return int(pwm)


def normalized_to_pwm(
        servo_id,
        value):
    """
    value:
        0~1
    """

    value = clamp(
        value,
        0.0,
        1.0
    )

    return percent_to_pwm(
        servo_id,
        value * 100
    )