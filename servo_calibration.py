"""
舵机校准配置

PWM范围:
500 ~ 2500

中位:
1500

根据实际安装情况调整
"""

from servo_constants import (
    S2_NECK,
    S3_JAW, S4_SMILE_R, S5_SMILE_L, S6_LIP_UP,
    S7_EYE_UD_R, S8_BLINK_R, S9_EYE_LR_R,
    S10_EYE_UD_L, S11_BLINK_L, S12_EYE_LR_L,
    S13_BROW, S14_TONGUE,
)

# min： 左/小/上
# 默认情况下，舵机的旋转方向是顺时针，即从左到右。但是有些舵机的旋转方向是逆时针，即从右到左。为了统一处理，我们使用reverse参数来表示舵机是否反向。
# max： 右/大/下
# reverse： 是否反向

SERVO_CALIBRATION = {

    # --------------------------------
    # 头部
    # --------------------------------

    S2_NECK: {
        "name": "neck",
        "min": 1000,
        "center": 1500,
        "max": 2000,
        "reverse": False
    },

    # --------------------------------
    # 嘴巴
    # --------------------------------

    S3_JAW: {
        "name": "jaw",
        "min": 900,     # 闭嘴
        "center": 1100,
        "max": 1300,    # 张嘴最大
        "reverse": True
    },

    S4_SMILE_R: {
        "name": "smile_r",
        "min": 1700,        # 微笑最小
        "center": 2100,
        "max": 2100,        # 微笑最大，凹最深
        "reverse": False
    },

    S5_SMILE_L: {
        "name": "smile_l",
        "min": 2000,        # 微笑最小
        "center": 2500,
        "max": 2500,        # 微笑最大，凹最深
        "reverse": True
    },

    S6_LIP_UP: {
        "name": "lip_up",
        "min": 1200,
        "center": 1500,
        "max": 1900,
        "reverse": False
    },

    # --------------------------------
    # 右眼
    # --------------------------------

    S7_EYE_UD_R: {
        "name": "eye_up_r",
        "min": 1900,        # 眼球最上
        "center": 1650,     # 眼球中间
        "max": 1400,        # 眼球最下
        "reverse": False
    },

    S8_BLINK_R: {
        "name": "blink_r",
        "min": 1800,        # 闭眼
        "center": 1300,     # 正常
        "max": 1100,        # 睁眼最大
        "reverse": True
    },

    S9_EYE_LR_R: {
        "name": "eye_lr_r",
        "min": 1300,        # 眼球最左
        "center": 1450,     # 眼球中间
        "max": 1650,        # 眼球最右
        "reverse": False
    },

    # --------------------------------
    # 左眼
    # --------------------------------

    S10_EYE_UD_L: {
        "name": "eye_up_l",
        "min": 1100,        # 眼球最上
        "center": 1350,     # 眼球中间
        "max": 1600,        # 眼球最下
        "reverse": True
    },

    S11_BLINK_L: {
        "name": "blink_l",
        "min": 1100,        # 闭眼
        "center": 1600,     # 正常
        "max": 1900,        # 睁眼最大
        "reverse": False
    },

    S12_EYE_LR_L: {
        "name": "eye_lr_l",
        "min": 1300,        # 眼球最左
        "center": 1550,     # 眼球中间
        "max": 1800,        # 眼球最右
        "reverse": True
    },

    # --------------------------------
    # 眉毛
    # --------------------------------

    S13_BROW: {
        "name": "brow",
        "min": 1800,        # 正常
        "center": 1800,     # 正常
        "max": 2100,        # 眉毛最大 
        "reverse": False
    },

    # --------------------------------
    # 舌头
    # --------------------------------

    S14_TONGUE: {
        "name": "tongue",
        "min": 1200,
        "center": 1500,
        "max": 1900,
        "reverse": False
    }
}