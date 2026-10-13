from enum import Enum, unique

@unique
class InpaintMode(Enum):
    """
    图像重绘算法枚举
    """
    STTN_AUTO = "sttn-auto"
    STTN_DET = "sttn-det"
    LAMA = "lama"
    OPENCV = "opencv"
    MINIMAX = "minimax"
    # 智能路由：按区间帧差自动选 LAMA（静态区间，快）或 MiniMax（运动区间，质量优先）
    # 注意：必须放在枚举末尾——GUI下拉框与日志按枚举序与 ini [InpaintMode] 段索引对齐
    AUTO = "auto"

@unique
class SubtitleDetectMode(Enum):
    """
    字幕检测算法枚举
    """
    PP_OCRv5_MOBILE = "PP_OCRv5_MOBILE"
    PP_OCRv5_SERVER = "PP_OCRv5_SERVER"