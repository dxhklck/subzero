
import os
from pathlib import Path
from qfluentwidgets import (qconfig, ConfigItem, QConfig, OptionsValidator, BoolValidator, OptionsConfigItem, 
                            EnumSerializer, RangeValidator, RangeConfigItem, ConfigValidator)
from backend.tools.constant import InpaintMode, SubtitleDetectMode
import configparser

# 项目版本号
VERSION = "1.0.0"
# 净影 (SubZero) —— 自维护版本，仓库地址待创建后填入
PROJECT_HOME_URL = "https://github.com/dxhklck/subzero"
PROJECT_ISSUES_URL = PROJECT_HOME_URL + "/issues"
PROJECT_RELEASES_URL = PROJECT_HOME_URL + "/releases"
PROJECT_UPDATE_URLS = [
    "https://api.github.com/repos/dxhklck/subzero/releases/latest",
]

# 硬件加速选项开关
HARDWARD_ACCELERATION_OPTION = True

class Config(QConfig):
    # 应用主题：light / dark / auto（跟随系统），主色固定青绿
    appTheme = OptionsConfigItem("Window", "AppTheme", "auto", OptionsValidator(["light", "dark", "auto"]), restart=False)

    # 窗口位置和大小
    windowX = ConfigItem("Window", "X", None)
    windowY = ConfigItem("Window", "Y", None)
    windowW = ConfigItem("Window", "Width", 1688)
    windowH = ConfigItem("Window", "Height", 896)

    # 使用一个配置项存储所有选区
    # 默认值为一个选区，格式为："ymin,ymax,xmin,xmax;ymin,ymax,xmin,xmax;..."，分号分隔不同选区
    subtitleSelectionAreas = ConfigItem("Main", "SubtitleSelectionAreas", "0.88,0.99,0.15,0.85")

    """
    MODE可选算法类型
    - InpaintMode.STTN_AUTO 智能擦除版
    - InpaintMode.STTN_DET 带字幕检测版, 无智能擦除
    - InpaintMode.LAMA 算法：对于动画类视频效果好，速度一般，不可以跳过字幕检测
    - InpaintMode.PROPAINTER 算法： 需要消耗大量显存，速度较慢，对运动非常剧烈的视频效果较好
    """
    # 【设置inpaint算法】
    inpaintMode = OptionsConfigItem("Main", "InpaintMode", InpaintMode.STTN_AUTO, OptionsValidator(InpaintMode), EnumSerializer(InpaintMode))
    
    subtitleDetectMode =  OptionsConfigItem("Main", "SubtitleDetectMode", SubtitleDetectMode.PP_OCRv5_SERVER, OptionsValidator(SubtitleDetectMode), EnumSerializer(SubtitleDetectMode))

    # 【设置像素点偏差】
    # 用于判断是不是非字幕区域(一般认为字幕文本框的长度是要大于宽度的，如果字幕框的高大于宽，且大于的幅度超过指定像素点大小，则认为是错误检测)
    subtitleYXAxisDifferencePixel = RangeConfigItem("Main", "SubtitleYXAxisDifferencePixel", 10, RangeValidator(0, 300))
    # 用于放大mask大小，防止自动检测的文本框过小，inpaint阶段出现文字边，有残留
    subtitleAreaDeviationPixel = RangeConfigItem("Main", "SubtitleAreaDeviationPixel", 10, RangeValidator(1, 300))
    # 同于判断两个文本框是否为同一行字幕，高度差距指定像素点以内认为是同一行
    subtitleAreaYAxisDifferencePixel = RangeConfigItem("Main", "SubtitleAreaYAxisDifferencePixel", 20, RangeValidator(0, 300))
    # 用于判断两个字幕文本的矩形框是否相似，如果X轴和Y轴偏差都在指定阈值内，则认为时同一个文本框
    subtitleAreaPixelToleranceYPixel = RangeConfigItem("Main", "SubtitleAreaPixelToleranceYPixel", 20, RangeValidator(0, 300))
    subtitleAreaPixelToleranceXPixel = RangeConfigItem("Main", "SubtitleAreaPixelToleranceXPixel", 20, RangeValidator(0, 300))
    subtitleTimelineBackwardFrameCount = RangeConfigItem("Main", "SubtitleTimelineBackwardFrameCount", 3, RangeValidator(0, 300))
    subtitleTimelineForwardFrameCount = RangeConfigItem("Main", "subtitleTimelineForwardFrameCount", 3, RangeValidator(0, 300))
    # 字幕检测采样步长：0 = 按视频帧率自适应（默认，30fps以上每3帧、60fps以上每4帧）；>0 = 固定每N帧采样一次
    # 字幕通常持续0.5秒以上，采样步长放宽可线性减少OCR调用次数
    subtitleDetectSampleStep = RangeConfigItem("Main", "SubtitleDetectSampleStep", 0, RangeValidator(0, 30))
    # 字幕检测前的降采样上限（长边像素）：0 = 原分辨率；帧长边超过该值时先等比缩小再送OCR，检测框按比例映射回原分辨率
    # 注意：PP-OCRv5 检测模型内部本就把长边钳制到960（inference.yml DetResizeForTest），因此：
    #   >=960 等于无操作（零收益零风险）；设为720 可让模型跳过内部缩放，OCR 实测快约40%
    #   （1080p 33ms→19.5ms；本项目测试视频9.5px高小字在640下仍检出无损，但过小有漏检风险）
    subtitleDetectProcessSize = RangeConfigItem("Main", "SubtitleDetectProcessSize", 720, RangeValidator(0, 4096))
    # 以下参数仅适用STTN算法时，才生效
    """
    1. STTN_SKIP_DETECTION
    含义：是否使用跳过检测
    效果：设置为True跳过字幕检测，会省去很大时间，但是可能误伤无字幕的视频帧或者会导致去除的字幕漏了

    2. STTN_NEIGHBOR_STRIDE
    含义：相邻帧数步长, 如果需要为第50帧填充缺失的区域，STTN_NEIGHBOR_STRIDE=5，那么算法会使用第45帧、第40帧等作为参照。
    效果：用于控制参考帧选择的密度，较大的步长意味着使用更少、更分散的参考帧，较小的步长意味着使用更多、更集中的参考帧。

    3. STTN_REFERENCE_LENGTH
    含义：参数帧数量，STTN算法会查看每个待修复帧的前后若干帧来获得用于修复的上下文信息
    效果：调大会增加显存占用，处理效果变好，但是处理速度变慢

    4. STTN_MAX_LOAD_NUM
    含义：STTN算法每次最多加载的视频帧数量
    效果：设置越大速度越慢，但效果越好
    注意：要保证STTN_MAX_LOAD_NUM大于STTN_NEIGHBOR_STRIDE和STTN_REFERENCE_LENGTH
    """
    # STTN 三参数推荐组合（默认值即为推荐值，1080p 长视频性价比最优）：
    #   NeighborStride=5  邻居帧窗口半宽，每次推理处理 2×5+1=11 帧；调大提速但时序变粗
    #   ReferenceLength=10 参考帧采样间隔（每隔10帧取1帧做长程参考）；调大质量略升、速度略降。
    #     ⚠ 实际批大小 = max(MaxLoadNum, NeighborStride×ReferenceLength)——ReferenceLength=100
    #     会把批量顶到 3000 帧，被 VRAM 自动钳制前先撑爆内存，建议 10~30
    #   MaxLoadNum=50    每个处理块的帧数；sttn_auto_inpaint 已有 VRAM 自适应钳制，OOM 风险低
    sttnNeighborStride = RangeConfigItem("Sttn", "NeighborStride", 5, RangeValidator(1, 100))
    # 参考帧数量
    sttnReferenceLength = RangeConfigItem("Sttn", "ReferenceLength", 10, RangeValidator(1, 100))
    # 设置STTN算法最大同时处理的帧数量
    sttnMaxLoadNum = RangeConfigItem("Sttn", "MaxLoadNum", 50, RangeValidator(1, 300))
    getSttnMaxLoadNum = lambda self: max(self.sttnMaxLoadNum.value, self.sttnNeighborStride.value * self.sttnReferenceLength.value)
    
    # 以下参数仅适用PROPAINTER算法时，才生效
    # 【根据自己的GPU显存大小设置】最大同时处理的图片数量，设置越大处理效果越好，但是要求显存越高
    # 1280x720p视频设置80需要25G显存，设置50需要19G显存
    # 720x480p视频设置80需要8G显存，设置50需要7G显存
    propainterMaxLoadNum = RangeConfigItem("ProPainter", "MaxLoadNum", 70, RangeValidator(1, 300))

    # 以下参数仅适用MINIMAX算法时，才生效
    # MiniMax-Remover（Wan2.1-1.3B DiT）每次送入的帧数，必须为 4k+1（81=4*20+1）
    # 主要是速度参数：块越大固定开销（VAE编解码/调度）摊越薄。A/B 实测（0.30MP条带）：
    # 61帧比25帧快13%而峰值仅多0.3GB——块大小对显存峰值影响很小（峰值由条带分辨率主导）。
    # _fit_chunk_to_vram 按条带分辨率校验显存：典型条带原样保留，超预算时收缩或提示降 ProcessWidth
    minimaxMaxLoadNum = RangeConfigItem("MiniMax", "MaxLoadNum", 41, RangeValidator(5, 201))
    # 去噪步数：A/B 实测（720p全帧）12→8 步仅慢 13% 时省 13%、与12步画质肉眼无差异（最大像素差18/255）；
    # 480p 等小分辨率下步数近似线性影响速度。8 为画质/速度平衡点，追求极限画质可设 12
    minimaxNumInferenceSteps = RangeConfigItem("MiniMax", "NumInferenceSteps", 8, RangeValidator(1, 30))
    # 掩膜边界扩张迭代次数：越大残影越少但误伤画面越多；0 = 不扩张
    minimaxIterations = RangeConfigItem("MiniMax", "Iterations", 6, RangeValidator(0, 20))
    # 处理分辨率上限（宽度像素，0 = 按视频原分辨率推理）。视频宽超过该值时，
    # 先等比降采样到该宽度推理、输出再放大回原分辨率。高分辨率视频（1080p/4K）
    # 设 960~1280 可提速 2~9 倍，重建区清晰度略降；480p 视频无影响。
    minimaxProcessWidth = RangeConfigItem("MiniMax", "ProcessWidth", 0, RangeValidator(0, 4096))
    # 条带裁剪修复：mask bbox 向四周外扩的上下文像素。越大背景参考越充分、耗时越多；
    # 0 = 不外扩（仅修 mask 紧贴范围，不推荐）；条带最小高度固定 160px
    minimaxStripPadding = RangeConfigItem("MiniMax", "StripPadding", 128, RangeValidator(0, 256))
    # 块间重叠帧数：视频按 chunk(4k+1帧) 分块推理，块边界处潜空间不连续可能出现闪烁/接缝；
    # 相邻块重叠这么多帧并线性加权混合可消除接缝；0 = 关闭（省推理时间）
    # 开销：总推理帧数变为 chunk/(chunk-重叠)，81帧块重叠8约多花11%时间
    minimaxBlockOverlap = RangeConfigItem("MiniMax", "BlockOverlap", 8, RangeValidator(0, 32))
    # VAE 解码精度：True = 解码器升 fp32（消除 fp16 VAE 在平坦背景的色带/条带，多占约1G显存）；
    # False = 全 fp16（显存紧张或追求极限速度时关闭）。编码始终走 fp16 省显存。
    minimaxVaeDecodeFp32 = ConfigItem("MiniMax", "VaeDecodeFp32", True, BoolValidator())

    # 是否使用硬件加速
    hardwareAcceleration = ConfigItem("Main", "HardwareAcceleration", HARDWARD_ACCELERATION_OPTION, BoolValidator())
    
    # 启动时检查应用更新
    checkUpdateOnStartup = ConfigItem("Main", "CheckUpdateOnStartup", True, BoolValidator())

    # 视频保存目录
    saveDirectory = ConfigItem("Main", "SaveDirectory", "", ConfigValidator())

CONFIG_FILE = 'config/config.json'
config = Config()
qconfig.load(CONFIG_FILE, config)

# 向后兼容：旧的 SubtitleDetectMode 枚举值为中文，迁移为新值
_detect_mode_value = config.subtitleDetectMode.value
if isinstance(_detect_mode_value, str) and _detect_mode_value in ("快速", "Fast"):
    config.set(config.subtitleDetectMode, SubtitleDetectMode.PP_OCRv5_MOBILE)
elif isinstance(_detect_mode_value, str) and _detect_mode_value in ("精准", "Precise"):
    config.set(config.subtitleDetectMode, SubtitleDetectMode.PP_OCRv5_SERVER)

# 界面文案（仅简体中文，多语言已移除）
tr = configparser.ConfigParser()

TRANSLATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'interface', "ch.ini")
tr.read(TRANSLATION_FILE, encoding='utf-8')

# 项目的base目录
BASE_DIR = str(Path(os.path.abspath(__file__)).parent)

os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'
# 模型权重均为本地文件，跳过 Paddle 每次启动的联网连通性探测（每次省约30秒）
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'