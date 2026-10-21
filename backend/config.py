
import os
from pathlib import Path
from qfluentwidgets import (qconfig, ConfigItem, QConfig, OptionsValidator, BoolValidator, OptionsConfigItem, 
                            EnumSerializer, RangeValidator, RangeConfigItem, ConfigValidator)
from backend.tools.constant import InpaintMode, SubtitleDetectMode, EncoderMode, DecoderMode
import configparser

# 项目版本号
VERSION = "1.3.1"
# 净影 (SubZero) —— 自维护版本，仓库地址待创建后填入
PROJECT_HOME_URL = "https://github.com/dxhklck/subzero"
PROJECT_ISSUES_URL = PROJECT_HOME_URL + "/issues"
PROJECT_RELEASES_URL = PROJECT_HOME_URL + "/releases"
# 检查更新端点（有序，失败自动切下一个）：国内魔搭清单优先 → GitHub API 兜底
# 魔搭端点返回 {"tag_name": "v1.2.0", "download_url": "...安装包直链",
#              "backup_url": "...备用下载地址", "notes": "...更新说明"}，
# 上传 app/version.json 到 dxhklck/subzero-models 仓库即生效。
# 角色划分（2026-10-17 定）：download_url = 魔搭仓库直链（主下载源，国内快）；
# backup_url = 夸克网盘分享链接（备用，魔搭挂/直链失效时用）；notes 含提取码等；
# GitHub API 端点仅做版本探测与 Release 页回退（无直链字段）
PROJECT_UPDATE_URLS = [
    "https://modelscope.cn/models/dxhklck/subzero-models/resolve/master/app/version.json",
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

    # 预览方向键步长（帧）：按左/右方向键在视频预览中移动的帧数。
    # 1 = 逐帧（默认；旧行为为 ±1 秒的大跳转，精确定位字幕帧困难）；
    # Ctrl+左/右 = ±1 秒、Shift+左/右 = ±5 秒（固定秒级跳转，与本设置无关）
    previewFrameStep = RangeConfigItem("Main", "PreviewFrameStep", 1, RangeValidator(1, 300))

    # 专业模式：高级设置页是否显示专家参数（容差像素/STTN细节/MiniMax次要项）
    expertMode = ConfigItem("Window", "ExpertMode", False, BoolValidator())

    """
    MODE可选算法类型
    - InpaintMode.STTN_AUTO 智能擦除版
    - InpaintMode.STTN_DET 带字幕检测版, 无智能擦除
    - InpaintMode.LAMA 算法：对于动画类视频效果好，速度一般，不可以跳过字幕检测
    """
    # 【设置inpaint算法】
    inpaintMode = OptionsConfigItem("Main", "InpaintMode", InpaintMode.STTN_AUTO, OptionsValidator(InpaintMode), EnumSerializer(InpaintMode))

    subtitleDetectMode =  OptionsConfigItem("Main", "SubtitleDetectMode", SubtitleDetectMode.PP_OCRv5_SERVER, OptionsValidator(SubtitleDetectMode), EnumSerializer(SubtitleDetectMode))

    # 【设置视频编码器】auto = 按本机实测探测（NVENC→QSV→AMF→libx264 保底）；
    # 强制指定某编码器时优先用它，本机跑不动则自动降级到链上下一个（不会让任务失败）。
    # 实测（1080p 噪声内容）：NVENC p5 约253fps vs libx264 veryfast 约72fps；
    # 纯 CPU 机器保底 libx264 veryfast（与旧行为一致），没有 N 卡不代表变慢
    videoEncoder = OptionsConfigItem("Main", "VideoEncoder", EncoderMode.AUTO, OptionsValidator(EncoderMode), EnumSerializer(EncoderMode))

    # 输出码率（kbps）：0 = 默认按质量档（crf/cq 21 档，体积随内容浮动）；
    # >0 = 指定 VBR 目标码率（如 8000 ≈ 1080p 高画质、4000 ≈ 中等），文件体积可预期。
    # 质量档与码率档二选一；同一编码器家族内两者的体积/画质权衡不同档位自洽
    outputBitrate = RangeConfigItem("Main", "OutputBitrate", 0, RangeValidator(0, 200000))

    # 【视频解码器】2026-10-17 起设置页不再暴露该选项（auto 即实测最优、无可调项），
    # 固定走 AUTO（= CPU 软解；实测数据：ffmpeg 管道 NVDEC 需逐帧转 BGR + 管道传输，
    # 720p H.264 195fps vs cv2 软解 645fps，4K HEVC 21 vs 69fps——管道开销远大于
    # 硬解收益，而本项目解码从来不是瓶颈）。cuda/cpu 分支保留供内部与 CLI 使用
    videoDecoder = OptionsConfigItem("Main", "VideoDecoder", DecoderMode.AUTO, OptionsValidator(DecoderMode), EnumSerializer(DecoderMode))

    # 【设置像素点偏差】
    # 用于判断是不是非字幕区域(一般认为字幕文本框的长度是要大于宽度的，如果字幕框的高大于宽，且大于的幅度超过指定像素点大小，则认为是错误检测)
    subtitleYXAxisDifferencePixel = RangeConfigItem("Main", "SubtitleYXAxisDifferencePixel", 10, RangeValidator(0, 100))
    # 用于放大mask大小，防止自动检测的文本框过小，inpaint阶段出现文字边，有残留
    subtitleAreaDeviationPixel = RangeConfigItem("Main", "SubtitleAreaDeviationPixel", 20, RangeValidator(1, 80))
    # 掩膜横向附加外扩除数：描边/艺术字笔画横向超出检测框的比例明显高于纵向，
    # 在 subtitleAreaDeviationPixel（四周基础外扩）之上，横向再按「字高 ÷ N」附加
    # （实际值钳制 8~40px，与字俱缩放）。0 = 不加横向附加；N 越小外扩越大
    # （残留严重调小到 1~2，误伤背景调大或设 0）。默认 3 = 字高 1/3，与历史行为一致
    maskXExpandDivisor = RangeConfigItem("Main", "MaskXExpandDivisor", 3, RangeValidator(0, 12))
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
    # 二遍残留兜底：第一遍修复完成后对输出重跑检测，检出与原修复区域重叠的残留字幕并二修。
    # 漏检从「永久残留」变成「可救」；无残留时仅多花一次检测时间。误检防护：只修与第一遍
    # 修复区域重叠的检出，画面里天然像文字的纹理（波纹/栅栏/立面）不会被误修
    # 默认开启（质量优先）：第一遍检测受采样步长与重复帧跳过约束，静态画面里的短字幕
    # 天然存在漏检概率，二遍是唯一兜底；二遍扫描本身逐帧且禁用判重，不继承第一遍的漏检。
    # 代价 = 成片多一遍检测（无残留时只检测不重编码）；求速度可在设置里手动关闭
    residueCheck = ConfigItem("Main", "ResidueCheck", True, BoolValidator())
    # 检测输入裁剪：把待检测帧裁到「字幕带 + margin」再送 OCR，检出框映射回原分辨率。
    # ⚠ 实证（test2.mp4 真实帧）：检出框级不等价（模型输出依赖输入尺寸，裁剪后有
    # ±2~18px 位移、偶发同行重组），但掩膜级差异由 create_mask 自适应外扩
    # （横向 8~40px/纵向 dev）吸收。残留二遍裁到第一遍联合带不受此限（兜底趟）。
    # margin = max(64, 帧高/8) 覆盖 DB unclip 外扩与跨界文字。关闭后全部回退整帧检测
    detectCropToArea = ConfigItem("Main", "DetectCropToArea", True, BoolValidator())

    # 检测批处理：一次送 N 个采样帧给 OCR，摊薄 paddle 每次调用的固定开销
    # （前置/后处理管线启动）。1 = 关闭（逐帧检测）。批内结果与逐帧逐位一致
    detectBatchSize = RangeConfigItem("Main", "DetectBatchSize", 4, RangeValidator(1, 16))
    # 选区语义（全自动，无开关）：掩膜跟随检测框动态缩放，整组无检出才回落选区矩形
    # （见 main._group_interval_masks；原「选区整片擦除」开关已废弃移除）
    # 长视频分段时长（秒）：0=禁用分段；视频时长 ≤ 该值整片处理，
    # 超过则按此值切段逐段执行，支持中断续跑（checkpoint 保存在
    # 输出目录 <视频名>_no_sub_segments/，重跑同名任务自动续接）
    # 默认 300 秒（5 分钟/段）：段更短 → ①中断后重跑的续跑粒度更细（最多损失
    # 半段重算）；②单段的中间产物更早落盘，长片更抗崩溃/OOM；
    # 代价：段数变多、段边界变多（段间重叠帧已消除接缝，质量无影响）
    segmentSeconds = RangeConfigItem("Main", "SegmentSeconds", 300, RangeValidator(0, 14400))
    # 段间重叠帧数：除第一段外，每段向前多处理 N 帧作为邻帧模型（STTN/MiniMax 靠前后帧
    # 窗口推理）的上下文，消除段边界处上下文被截断产生的接缝；重叠帧只送入模型、
    # 不写入段文件，因此拼接时无需裁剪、成片总帧数不变。0=关闭（与旧行为一致）
    segmentOverlapFrames = RangeConfigItem("Main", "SegmentOverlapFrames", 2, RangeValidator(0, 10))
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
    # MiniMax-Remover（Wan2.1-1.3B DiT）每次送入的帧数，必须为 4k+1（121=4*30+1）
    # 主要是速度参数：块越大固定开销（VAE编解码/调度）摊越薄。720p 全流程基准（2026-10-14，
    # test2.mp4 240帧含检测/加载）：41→61→81→121 耗时 133→126→124→120s（端到端快约一成、
    # 推理本身约一成五），显存峰值恒定约14.4GB——峰值由条带分辨率主导，与块大小无关；
    # 且 61/81/121 输出逐像素一致（≥61帧即单批次，41是唯一触发多批次的档位）。
    # 6步仅再快2.2%但质量下降、12步慢7.5%，故步数维持8；块间重叠8保留（长视频防接缝）。
    # _fit_chunk_to_vram 按条带分辨率校验显存：典型条带原样保留，超预算时收缩或提示降 ProcessWidth
    minimaxMaxLoadNum = RangeConfigItem("MiniMax", "MaxLoadNum", 121, RangeValidator(5, 121))
    # 去噪步数：A/B 实测（720p全帧）12→8 步仅慢 13% 时省 13%、与12步画质肉眼无差异（最大像素差18/255）；
    # 480p 等小分辨率下步数近似线性影响速度。8 为画质/速度平衡点，追求极限画质可设 12
    minimaxNumInferenceSteps = RangeConfigItem("MiniMax", "NumInferenceSteps", 8, RangeValidator(1, 12))
    # 掩膜边界扩张迭代次数：越大残影越少但误伤画面越多；0 = 不扩张
    minimaxIterations = RangeConfigItem("MiniMax", "Iterations", 6, RangeValidator(0, 12))
    # 处理分辨率上限（宽度像素）。视频宽超过该值时，先等比降采样到该宽度推理、
    # 输出再放大回原分辨率（缩小 INTER_AREA / 放大 INTER_LANCZOS4，画质损失最小）；
    # 条带宽不超过该值时按原分辨率推理。0 = 恒按原分辨率。
    # 2026-10-16 实测（1714 宽条带 360 帧）：0=326s / 1280=41s（快8倍）/ 640=8s（快40倍），
    # 默认 1280 为画质优先平衡点；480p 等窄素材不受影响。
    minimaxProcessWidth = RangeConfigItem("MiniMax", "ProcessWidth", 1280, RangeValidator(0, 2160))
    # 条带裁剪修复：mask bbox 向四周外扩的上下文像素。越大背景参考越充分、耗时越多；
    # 0 = 不外扩（仅修 mask 紧贴范围，不推荐）；条带最小高度固定 160px
    minimaxStripPadding = RangeConfigItem("MiniMax", "StripPadding", 128, RangeValidator(0, 300))
    # 块间重叠帧数：视频按 chunk(4k+1帧) 分块推理，块边界处潜空间不连续可能出现闪烁/接缝；
    # 相邻块重叠这么多帧并线性加权混合可消除接缝；0 = 关闭（省推理时间）
    # 开销：总推理帧数变为 chunk/(chunk-重叠)，81帧块重叠8约多花11%时间
    minimaxBlockOverlap = RangeConfigItem("MiniMax", "BlockOverlap", 8, RangeValidator(0, 20))
    # VAE 解码精度：True = 解码器升 fp32（消除 fp16 VAE 在平坦背景的色带/条带，多占约1G显存）；
    # False = 全 fp16（显存紧张或追求极限速度时关闭）。编码始终走 fp16 省显存。
    minimaxVaeDecodeFp32 = ConfigItem("MiniMax", "VaeDecodeFp32", True, BoolValidator())
    # SageAttention 加速：True = 自注意力走 SageAttention（INT8 量化 K），False = PyTorch SDPA。
    # 实测（81帧/12步 A/B）：端到端 0.986 -> 0.578 s/帧（约快 41%），显存峰值持平，
    # 输出 PSNR 57.5dB；但平坦区/细纹理内容有量化劣化倾向（用户实测），默认关。
    # 关闭时回落 _sdpa_exact（cuDNN 后端，0.986 -> 0.849 s/帧，数学精确零损失）。
    # 软依赖：环境未装 sageattention 或运行异常（精度不支持/head_dim 不兼容/OOM）时自动回落 SDPA。
    # 预编译 wheel 见 woct0rdho/SageAttention（GitHub Releases），安装须 --no-deps。
    minimaxUseSageAttention = ConfigItem("MiniMax", "UseSageAttention", False, BoolValidator())

    # 是否使用硬件加速
    hardwareAcceleration = ConfigItem("Main", "HardwareAcceleration", HARDWARD_ACCELERATION_OPTION, BoolValidator())
    
    # 启动时检查应用更新
    checkUpdateOnStartup = ConfigItem("Main", "CheckUpdateOnStartup", True, BoolValidator())
    # 任务完成提醒：任务跑完时闪任务栏按钮 + 完成提示条。窗口在前台时
    # InfoBar 本就可见，闪灯只发生在窗口不在前台时（长任务切走场景），零侵入
    taskNotify = ConfigItem("Main", "TaskNotify", True, BoolValidator())

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

# 旧版 config 里可能残留已删除的算法值，直接进 GUI 下拉会 optionToText KeyError。
# ⚠ 比较必须同为字符串：config 加载后 value 是枚举成员（deserializeFrom），
# 直接与 [m.value...] 字符串列表比会恒为 True → 每次进程启动（含 GUI 任务子进程）
# 都把用户选择的算法静默重置回 STTN 并写盘（"选的和跑的不是同一个"的根因）
try:
    _inpaint_value = getattr(config.inpaintMode.value, 'value', config.inpaintMode.value)
    if _inpaint_value not in [m.value for m in InpaintMode]:
        config.set(config.inpaintMode, InpaintMode.STTN_AUTO)
except Exception:
    pass

# 项目的base目录
BASE_DIR = str(Path(os.path.abspath(__file__)).parent)

os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'
# 模型权重均为本地文件，跳过 Paddle 每次启动的联网连通性探测（每次省约30秒）
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'