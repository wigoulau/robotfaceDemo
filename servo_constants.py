"""
舵机 ID 命名常量
根据 head.png 中定义的舵机布局，所有舵机 ID 统一使用语义化命名。

用法:
    from servo_constants import *
    或
    from servo_constants import S3_JAW, S4_SMILE_R
"""

# ============================
# 摆臂（头部俯仰/点头）
# ============================
S0_RIGHT_ARM  = 0   # 右摆臂
S1_LEFT_ARM   = 1   # 左摆臂

# ============================
# 脖子
# ============================
S2_NECK       = 2   # 脖子转动

# ============================
# 嘴巴（4 关节）
# ============================
S3_JAW        = 3   # 嘴巴/下巴（张嘴闭合）
S4_SMILE_R    = 4   # 右微笑（嘴角右拉）
S5_SMILE_L    = 5   # 左微笑（嘴角左拉）
S6_LIP_UP     = 6   # 嘴唇上扬

# ============================
# 右眼（3 关节）
# ============================
S7_EYE_UD_R   = 7   # 右上下（右眼上下转动）
S8_BLINK_R    = 8   # 右眨眼（右眼睑闭合）
S9_EYE_LR_R   = 9   # 右眼左（右眼左右转动）

# ============================
# 左眼（3 关节）
# ============================
S10_EYE_UD_L  = 10  # 左上下（左眼上下转动）
S11_BLINK_L   = 11  # 左眨眼（左眼睑闭合）
S12_EYE_LR_L  = 12  # 左眼左（左眼左右转动）

# ============================
# 眉毛
# ============================
S13_BROW      = 13  # 眉毛

# ============================
# 舌头
# ============================
S14_TONGUE    = 14  # 舌头


# ============================
# 分组映射（便于批量操作）
# ============================

# 所有嘴巴舵机
MOUTH_SERVOS = (S3_JAW, S4_SMILE_R, S5_SMILE_L, S6_LIP_UP)

# 所有眼睛舵机
EYE_SERVOS = (
    S7_EYE_UD_R, S8_BLINK_R, S9_EYE_LR_R,
    S10_EYE_UD_L, S11_BLINK_L, S12_EYE_LR_L,
)

# 右眼舵机
EYE_R_SERVOS = (S7_EYE_UD_R, S8_BLINK_R, S9_EYE_LR_R)

# 左眼舵机
EYE_L_SERVOS = (S10_EYE_UD_L, S11_BLINK_L, S12_EYE_LR_L)

# 眨眼舵机
BLINK_SERVOS = (S8_BLINK_R, S11_BLINK_L)

# 眼球 LR 舵机
EYE_LR_SERVOS = (S9_EYE_LR_R, S12_EYE_LR_L)

# 眼球 UD 舵机
EYE_UD_SERVOS = (S7_EYE_UD_R, S10_EYE_UD_L)

# 微笑舵机
SMILE_SERVOS = (S4_SMILE_R, S5_SMILE_L)

# 所有舵机（按 ID 排序）
ALL_SERVOS = tuple(range(15))


# ============================
# 中文名称映射（调试用）
# ============================
SERVO_NAMES_CN = {
    S0_RIGHT_ARM:  "右摆臂",
    S1_LEFT_ARM:   "左摆臂",
    S2_NECK:       "脖子转动",
    S3_JAW:        "嘴巴",
    S4_SMILE_R:    "右微笑",
    S5_SMILE_L:    "左微笑",
    S6_LIP_UP:     "嘴唇上扬",
    S7_EYE_UD_R:   "右上下",
    S8_BLINK_R:    "右眨眼",
    S9_EYE_LR_R:   "右眼左",
    S10_EYE_UD_L:  "左上下",
    S11_BLINK_L:   "左眨眼",
    S12_EYE_LR_L:  "左眼左",
    S13_BROW:      "眉毛",
    S14_TONGUE:    "舌头",
}
