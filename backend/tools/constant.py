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

@unique
class SubtitleDetectMode(Enum):
    """
    字幕检测算法枚举
    """
    PP_OCRv5_MOBILE = "PP_OCRv5_MOBILE"
    PP_OCRv5_SERVER = "PP_OCRv5_SERVER"

@unique
class EncoderMode(Enum):
    """
    视频 H.264 编码器枚举（值即 ffmpeg 编码器名）
    注意：顺序即 GUI 下拉框与 ini [VideoEncoder] 段的索引序，新增项加末尾
    """
    AUTO = "auto"          # 按本机实测探测：NVENC→QSV→AMF→libx264 保底
    NVENC = "h264_nvenc"   # NVIDIA 显卡
    QSV = "h264_qsv"       # Intel 核显/独显
    AMF = "h264_amf"       # AMD 显卡
    X264 = "libx264"       # CPU 编码（保底，永远可用）


@unique
class DecoderMode(Enum):
    """
    视频解码器枚举（值即配置存储值）
    注意：顺序即 GUI 下拉框与 ini [VideoDecoder] 段的索引序
    """
    AUTO = "auto"   # 实测探测 NVDEC，可用且分辨率 ≥720p 用硬解，否则 CPU 软解
    CUDA = "cuda"   # 强制 NVDEC 硬解（探测失败自动回退 CPU）
    CPU = "cpu"     # cv2 CPU 软解（原行为）