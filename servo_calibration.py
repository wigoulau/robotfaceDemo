"""
舵机校准配置

PWM范围:
500 ~ 2500

中位:
1500

根据实际安装情况调整
"""

SERVO_CALIBRATION = {

    # --------------------------------
    # 头部
    # --------------------------------

    2: {
        "name": "neck",
        "min": 1000,
        "center": 1500,
        "max": 2000,
        "reverse": False
    },

    # --------------------------------
    # 嘴巴
    # --------------------------------

    3: {
        "name": "jaw",
        "min": 1000,
        "center": 1450,
        "max": 1450,
        "reverse": True
    },

    4: {
        "name": "smile_r",
        "min": 1200,
        "center": 1500,
        "max": 1900,
        "reverse": False
    },

    5: {
        "name": "smile_l",
        "min": 1200,
        "center": 1500,
        "max": 1900,
        "reverse": True
    },

    6: {
        "name": "lip_up",
        "min": 1200,
        "center": 1500,
        "max": 1900,
        "reverse": False
    },

    # --------------------------------
    # 右眼
    # --------------------------------

    7: {
        "name": "eye_up_r",
        "min": 1200,
        "center": 1500,
        "max": 1800,
        "reverse": False
    },

    8: {
        "name": "blink_r",
        "min": 1000,
        "center": 1800,
        "max": 2000,
        "reverse": False
    },

    9: {
        "name": "eye_lr_r",
        "min": 1200,
        "center": 1500,
        "max": 1800,
        "reverse": False
    },

    # --------------------------------
    # 左眼
    # --------------------------------

    10: {
        "name": "eye_up_l",
        "min": 1200,
        "center": 1500,
        "max": 1800,
        "reverse": True
    },

    11: {
        "name": "blink_l",
        "min": 1000,
        "center": 1800,
        "max": 2000,
        "reverse": True
    },

    12: {
        "name": "eye_lr_l",
        "min": 1200,
        "center": 1500,
        "max": 1800,
        "reverse": True
    },

    # --------------------------------
    # 眉毛
    # --------------------------------

    13: {
        "name": "brow",
        "min": 1200,
        "center": 1500,
        "max": 1900,
        "reverse": False
    },

    # --------------------------------
    # 舌头
    # --------------------------------

    14: {
        "name": "tongue",
        "min": 1200,
        "center": 1500,
        "max": 1900,
        "reverse": False
    }
}