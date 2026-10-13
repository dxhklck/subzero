
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

    # 界面语言设置（切换后需重启生效：界面文案在软件启动时一次性加载并固化到各控件）
    intefaceTexts = {
        '简体中文': 'ch',
        '繁體中文': 'chinese_cht',
        'English': 'en',
        '한국어': 'ko',
        '日本語': 'japan',
        'Tiếng Việt': 'vi',
        'Español': 'es'
    }
    # 注意：默认值必须在选项列表内（旧版默认值为 'ChineseSimplified' 不在列表中，
    # 是语言切换失效的根因之一）
    interface = OptionsConfigItem("Window", "Interface", "ch", OptionsValidator(intefaceTexts.values()), restart = True)

    # 窗口位置和大小
    windowX = ConfigItem("Window", "X", None)
    windowY = ConfigItem("Window", "Y", None)
    windowW = ConfigItem("Window", "Width", 1688)
    windowH = ConfigItem("Window", "Height", 896)

    # 使用一个配置项存储所有选区
    # 默认值为一个选区，格式为："ymin,ymax,xmin,xmax;ymin,ymax,xmin,xmax;..."，分号分隔不同选区
    subtitleSelectionAreas = ConfigItem("Main", "SubtitleSelectionAreas", "0.88,0.99,0.15,0.85")

    # 专业模式：高级设置页是否显示专家参数（容差像素/STTN细节/MiniMax次要项）
    expertMode = ConfigItem("Window", "ExpertMode", False, BoolValidator())

    """
    MODE可选算法类型
    - InpaintMode.STTN_AUTO 智能擦除版
    - InpaintMode.STTN_DET 带字幕检测版, 无智能擦除
    - InpaintMode.LAMA 算法：对于动画类视频效果好，速度一般，不可以跳过字幕检测
    - InpaintMode.AUTO 智能路由：按区间运动量自动选 LAMA（静态）或 MiniMax（运动）
    """
    # 【设置inpaint算法】
    inpaintMode = OptionsConfigItem("Main", "InpaintMode", InpaintMode.STTN_AUTO, OptionsValidator(InpaintMode), EnumSerializer(InpaintMode))
    
    subtitleDetectMode =  OptionsConfigItem("Main", "SubtitleDetectMode", SubtitleDetectMode.PP_OCRv5_SERVER, OptionsValidator(SubtitleDetectMode), EnumSerializer(SubtitleDetectMode))

    # 【设置像素点偏差】
    # 用于判断是不是非字幕区域(一般认为字幕文本框的长度是要大于宽度的，如果字幕框的高大于宽，且大于的幅度超过指定像素点大小，则认为是错误检测)
    subtitleYXAxisDifferencePixel = RangeConfigItem("Main", "SubtitleYXAxisDifferencePixel", 10, RangeValidator(0, 100))
    # 用于放大mask大小，防止自动检测的文本框过小，inpaint阶段出现文字边，有残留
    subtitleAreaDeviationPixel = RangeConfigItem("Main", "SubtitleAreaDeviationPixel", 20, RangeValidator(1, 80))
    # 同于判断两个文本框是否为同一行字幕，高度差距指定像素点以内认为是同一行
    subtitleAreaYAxisDifferencePixel = RangeConfigItem("Main", "SubtitleAreaYAxisDifferencePixel", 20, RangeValidator(0, 100))
    # 用于判断两个字幕文本的矩形框是否相似，如果X轴和Y轴偏差都在指定阈值内，则认为时同一个文本框
    subtitleAreaPixelToleranceYPixel = RangeConfigItem("Main", "SubtitleAreaPixelToleranceYPixel", 20, RangeValidator(0, 100))
    subtitleAreaPixelToleranceXPixel = RangeConfigItem("Main", "SubtitleAreaPixelToleranceXPixel", 20, RangeValidator(0, 100))
    subtitleTimelineBackwardFrameCount = RangeConfigItem("Main", "SubtitleTimelineBackwardFrameCount", 5, RangeValidator(0, 30))
    subtitleTimelineForwardFrameCount = RangeConfigItem("Main", "subtitleTimelineForwardFrameCount", 5, RangeValidator(0, 30))
    # 字幕检测采样步长：0 = 按视频帧率自适应（默认，30fps以上每3帧、60fps以上每4帧）；>0 = 固定每N帧采样一次
    # 字幕通常持续0.5秒以上，采样步长放宽可线性减少OCR调用次数
    subtitleDetectSampleStep = RangeConfigItem("Main", "SubtitleDetectSampleStep", 0, RangeValidator(0, 10))
    # 字幕检测前的降采样上限（长边像素）：0 = 原分辨率；帧长边超过该值时先等比缩小再送OCR，检测框按比例映射回原分辨率
    # 注意：PP-OCRv5 检测模型内部本就把长边钳制到960（inference.yml DetResizeForTest），因此：
    #   >=960 等于无操作（零收益零风险）；设为720 可让模型跳过内部缩放，OCR 实测快约40%
    #   （1080p 33ms→19.5ms；本项目测试视频9.5px高小字在640下仍检出无损，但过小有漏检风险）
    # 上限 960：模型内部本就把长边钳到 960，>=960 的任何值结果完全相同，再大没有意义
    subtitleDetectProcessSize = RangeConfigItem("Main", "SubtitleDetectProcessSize", 720, RangeValidator(0, 960))
    # 智能路由（AUTO模式）静态阈值：区间内采样帧的平均帧差（0-255灰度标度，160px长边灰度图上计算）
    # 低于该值判为静态画面走 LAMA（0.09s/帧，静态背景≈无损），不低于则走 MiniMax（按MiniMax参数组）
    # 默认1.0由 test2/test4 实测帧差分布定标（质量优先：只路由铁定静态的区间）；
    # 想更激进可调大（3.0约一半区间走LAMA，5.0约九成——运动区间用LAMA会糊，按素材质量容忍度权衡）
    autoRouteStaticThreshold = RangeConfigItem("Main", "AutoRouteStaticThreshold", 1, RangeValidator(0, 10))
    # 二遍残留兜底：第一遍修复完成后对输出重跑检测，检出与原修复区域重叠的残留字幕并二修。
    # 漏检从「永久残留」变成「可救」；无残留时仅多花一次检测时间。误检防护：只修与第一遍
    # 修复区域重叠的检出，画面里天然像文字的纹理（波纹/栅栏/立面）不会被误修
    residueCheck = ConfigItem("Main", "ResidueCheck", True, BoolValidator())
    # 长视频分段时长（分钟）：0=禁用分段；视频时长 ≤ 该值整片处理，
    # 超过则按此值切段逐段执行，支持中断续跑（checkpoint 保存在
    # 输出目录 <视频名>_no_sub_segments/，重跑同名任务自动续接）
    segmentMinutes = RangeConfigItem("Main", "SegmentMinutes", 30, RangeValidator(0, 240))
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
    sttnNeighborStride = RangeConfigItem("Sttn", "NeighborStride", 5, RangeValidator(1, 20))
    # 参考帧数量
    sttnReferenceLength = RangeConfigItem("Sttn", "ReferenceLength", 10, RangeValidator(1, 30))
    # 设置STTN算法最大同时处理的帧数量
    sttnMaxLoadNum = RangeConfigItem("Sttn", "MaxLoadNum", 50, RangeValidator(1, 200))
    getSttnMaxLoadNum = lambda self: max(self.sttnMaxLoadNum.value, self.sttnNeighborStride.value * self.sttnReferenceLength.value)

    # 以下参数仅适用MINIMAX算法时，才生效
    # MiniMax-Remover（Wan2.1-1.3B DiT）每次送入的帧数，必须为 4k+1（81=4*20+1）
    # 主要是速度参数：块越大固定开销（VAE编解码/调度）摊越薄。A/B 实测（0.30MP条带）：
    # 61帧比25帧快13%而峰值仅多0.3GB——块大小对显存峰值影响很小（峰值由条带分辨率主导）。
    # _fit_chunk_to_vram 按条带分辨率校验显存：典型条带原样保留，超预算时收缩或提示降 ProcessWidth
    minimaxMaxLoadNum = RangeConfigItem("MiniMax", "MaxLoadNum", 41, RangeValidator(5, 121))
    # 去噪步数：A/B 实测（720p全帧）12→8 步仅慢 13% 时省 13%、与12步画质肉眼无差异（最大像素差18/255）；
    # 480p 等小分辨率下步数近似线性影响速度。8 为画质/速度平衡点，追求极限画质可设 12
    minimaxNumInferenceSteps = RangeConfigItem("MiniMax", "NumInferenceSteps", 8, RangeValidator(1, 12))
    # 掩膜边界扩张迭代次数：越大残影越少但误伤画面越多；0 = 不扩张
    minimaxIterations = RangeConfigItem("MiniMax", "Iterations", 6, RangeValidator(0, 12))
    # 处理分辨率上限（宽度像素，0 = 按视频原分辨率推理）。视频宽超过该值时，
    # 先等比降采样到该宽度推理、输出再放大回原分辨率。高分辨率视频（1080p/4K）
    # 设 960~1280 可提速 2~9 倍，重建区清晰度略降；480p 视频无影响。
    # 上限 2160：条带裁剪下只有 4K 宽 mask 透传时条带长边才会到 2160，再大用不上
    minimaxProcessWidth = RangeConfigItem("MiniMax", "ProcessWidth", 0, RangeValidator(0, 2160))
    # 条带裁剪修复：mask bbox 向四周外扩的上下文像素。越大背景参考越充分、耗时越多；
    # 0 = 不外扩（仅修 mask 紧贴范围，不推荐）；条带最小高度固定 160px
    minimaxStripPadding = RangeConfigItem("MiniMax", "StripPadding", 128, RangeValidator(0, 256))
    # 块间重叠帧数：视频按 chunk(4k+1帧) 分块推理，块边界处潜空间不连续可能出现闪烁/接缝；
    # 相邻块重叠这么多帧并线性加权混合可消除接缝；0 = 关闭（省推理时间）
    # 开销：总推理帧数变为 chunk/(chunk-重叠)，81帧块重叠8约多花11%时间
    minimaxBlockOverlap = RangeConfigItem("MiniMax", "BlockOverlap", 8, RangeValidator(0, 20))
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

# 修复旧版可能遗留的无效语言值（旧默认值 'ChineseSimplified' 不在选项列表中）：
# 该值会使 GUI 构建设置页语言下拉时 optionToText[无效值] 抛 KeyError
try:
    _iface_stored = qconfig.get(config.interface)
except Exception:
    _iface_stored = None
if _iface_stored not in Config.intefaceTexts.values():
    config.set(config.interface, 'ch')

# 向后兼容：旧的 SubtitleDetectMode 枚举值为中文，迁移为新值
_detect_mode_value = config.subtitleDetectMode.value
if isinstance(_detect_mode_value, str) and _detect_mode_value in ("快速", "Fast"):
    config.set(config.subtitleDetectMode, SubtitleDetectMode.PP_OCRv5_MOBILE)
elif isinstance(_detect_mode_value, str) and _detect_mode_value in ("精准", "Precise"):
    config.set(config.subtitleDetectMode, SubtitleDetectMode.PP_OCRv5_SERVER)

# 界面文案：按界面语言配置加载对应 ini；语言文件中缺失的键回退到简体中文，
# 保证新增文案漏翻译时其他语言也能正常启动（不会 KeyError）
tr = configparser.ConfigParser()

_INTERFACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'interface')
tr.read(os.path.join(_INTERFACE_DIR, 'ch.ini'), encoding='utf-8')

_interface_lang = 'ch'
try:
    _v = config.interface.value
    if _v in Config.intefaceTexts.values():
        _interface_lang = _v
except Exception:
    pass
if _interface_lang != 'ch':
    _lang_file = os.path.join(_INTERFACE_DIR, f'{_interface_lang}.ini')
    if os.path.exists(_lang_file):
        _lang_tr = configparser.ConfigParser()
        _lang_tr.read(_lang_file, encoding='utf-8')
        for _section in _lang_tr.sections():
            if not tr.has_section(_section):
                tr.add_section(_section)
            for _key, _value in _lang_tr[_section].items():
                tr[_section][_key] = _value

# 旧版 config 里可能残留已删除的算法值（如 ProPainter），直接进 GUI 下拉会 optionToText KeyError
try:
    if config.inpaintMode.value not in [m.value for m in InpaintMode]:
        config.set(config.inpaintMode, InpaintMode.STTN_AUTO)
except Exception:
    pass

# 项目的base目录
BASE_DIR = str(Path(os.path.abspath(__file__)).parent)

os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'
# 模型权重均为本地文件，跳过 Paddle 每次启动的联网连通性探测（每次省约30秒）
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'