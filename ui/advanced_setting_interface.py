"""
@desc: 设置页面（检测参数 / 算法参数 / 保存目录）
"""

from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout
from qfluentwidgets import (ScrollArea, ExpandLayout, CardWidget, SubtitleLabel,
                           FluentIcon, NavigationWidget, NavigationItemPosition,
                           SettingCardGroup, RangeSettingCard, SwitchSettingCard,
                           HyperlinkCard, PrimaryPushSettingCard, PushSettingCard,
                           MessageBox, SettingCard, PushButton, InfoBar, InfoBarPosition, RadioButton)
from backend.config import config, tr, VERSION, PROJECT_HOME_URL, PROJECT_ISSUES_URL, PROJECT_RELEASES_URL
from backend.tools.version_service import VersionService
from backend.tools.concurrent import TaskExecutor
from backend.tools.bootstrap import check_all, fix_weights

# MiniMax 性能预设：按素材分辨率一键写入推荐参数（16G 显存 + 条带裁剪实测）
# 注：条带裁剪下典型字幕条带显存约为全帧的 1/5~1/3，原生分辨率可跑；
#     MaxLoadNum 是宽 mask（多行/居中大字）时的显存保险，超宽 mask 需手动调小
MINIMAX_PRESETS = {
    "480p":  dict(maxload=61, steps=4, iterations=8, padding=128, width=0),
    "720p":  dict(maxload=41, steps=8, iterations=8, padding=128, width=0),
    "1080p": dict(maxload=41, steps=8, iterations=8, padding=128, width=0),
    "4K":    dict(maxload=21, steps=8, iterations=8, padding=160, width=0),
}


class MiniMaxPresetCard(SettingCard):
    """MiniMax 分辨率预设卡：四个按钮一键写入 MaxLoadNum/Steps/Iterations/StripPadding/ProcessWidth"""

    def __init__(self, title, content, parent=None):
        super().__init__(FluentIcon.SPEED_HIGH, title, content, parent)
        self._btns = {}
        for name in ["480p", "720p", "1080p", "4K"]:
            btn = PushButton(name, self)
            btn.setFixedWidth(56)
            btn.clicked.connect(lambda _=False, n=name: self.apply_preset(n))
            self._btns[name] = btn
            self.hBoxLayout.addWidget(btn, 0, Qt.AlignRight)
            self.hBoxLayout.addSpacing(8)

    def apply_preset(self, name):
        p = MINIMAX_PRESETS[name]
        self._cards["maxload"].setValue(p["maxload"])
        self._cards["steps"].setValue(p["steps"])
        self._cards["iterations"].setValue(p["iterations"])
        self._cards["padding"].setValue(p["padding"])
        self._cards["width"].setValue(p["width"])
        for n, b in self._btns.items():
            b.setChecked(n == name)
        InfoBar.success(
            "预设已应用", f"{name}：MaxLoadNum {p['maxload']} / Steps {p['steps']} / Iterations {p['iterations']} / 外扩 {p['padding']}px",
            orient=Qt.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2500, parent=self.window())

class ThemeCard(SettingCard):
    """应用主题卡：三个单选按钮直接在卡片内选择（浅色/深色/跟随系统），无下拉"""

    def __init__(self, title, content, parent=None):
        super().__init__(FluentIcon.BRUSH, title, content, parent)
        self._btns = {}
        for key, text in [("light", "浅色"), ("dark", "深色"), ("auto", "跟随系统")]:
            rb = RadioButton(text, self)
            rb.setChecked(config.appTheme.value == key)
            # clicked 仅用户点击时触发（程序 setChecked 不触发），避免初始化回写
            rb.clicked.connect(lambda _=False, k=key: config.set(config.appTheme, k))
            self._btns[key] = rb
            self.hBoxLayout.addWidget(rb, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def sync_selection(self):
        """外部改主题后同步选中态"""
        for key, rb in self._btns.items():
            rb.setChecked(config.appTheme.value == key)


class AdvancedSettingInterface(ScrollArea):
    """高级设置页面"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.version_manager = VersionService()
        self.__init_widgets()

    def __init_widgets(self):
        # 创建滚动内容的容器
        self.scrollWidget = QtWidgets.QWidget(self)
        self.expandLayout = ExpandLayout(self.scrollWidget)
        
        # 设置滚动区域属性
        self.setWidget(self.scrollWidget)
        self.enableTransparentBackground()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        
        # 设置滚动区域样式以适应主题
        self.setAttribute(QtCore.Qt.WA_StyledBackground)
        
        # 设置UI
        self.setup_ui()
        self.setup_layout()

    def setup_layout(self):
        self.appearance_group.addSettingCard(self.theme_card)
        self.expandLayout.addWidget(self.appearance_group)

        self.subtitle_detection_group.addSettingCard(self.subtitle_yx_axis_difference_pixel)
        self.subtitle_detection_group.addSettingCard(self.subtitle_area_deviation_pixel)
        self.subtitle_detection_group.addSettingCard(self.subtitle_area_y_axis_difference_pixel)
        self.subtitle_detection_group.addSettingCard(self.subtitle_area_pixel_tolerance_y_pixel)
        self.subtitle_detection_group.addSettingCard(self.subtitle_area_pixel_tolerance_x_pixel)
        self.subtitle_detection_group.addSettingCard(self.subtitle_timeline_backward_frame_count)
        self.subtitle_detection_group.addSettingCard(self.subtitle_timeline_forward_frame_count)
        self.subtitle_detection_group.addSettingCard(self.subtitle_detect_sample_step)
        self.subtitle_detection_group.addSettingCard(self.subtitle_detect_process_width)
        self.expandLayout.addWidget(self.subtitle_detection_group)

        self.sttn_group.addSettingCard(self.sttn_neighbor_stride)
        self.sttn_group.addSettingCard(self.sttn_reference_length)
        self.sttn_group.addSettingCard(self.sttn_max_load_num)
        self.expandLayout.addWidget(self.sttn_group)

        self.propainter_group.addSettingCard(self.propainter_max_load_num)
        self.expandLayout.addWidget(self.propainter_group)

        self.minimax_group.addSettingCard(self.minimax_max_load_num)
        self.minimax_group.addSettingCard(self.minimax_num_inference_steps)
        self.minimax_group.addSettingCard(self.minimax_iterations)
        self.minimax_group.addSettingCard(self.minimax_process_width)
        self.minimax_group.addSettingCard(self.minimax_strip_padding)
        self.minimax_group.addSettingCard(self.minimax_block_overlap)
        self.minimax_group.addSettingCard(self.minimax_vae_decode_fp32)
        self.minimax_group.addSettingCard(self.minimax_torch_compile)
        self.minimax_group.addSettingCard(self.minimax_preset_card)
        self.expandLayout.addWidget(self.minimax_group)

        self.advanced_group.addSettingCard(self.save_directory)
        self.advanced_group.addSettingCard(self.env_check_card)
        self.expandLayout.addWidget(self.advanced_group)

        self.expandLayout.setSpacing(16)
        self.expandLayout.setContentsMargins(16, 16, 16, 48)
        
    def setup_ui(self):
        """设置UI"""
        # 字幕检测设置组
        self.subtitle_detection_group = SettingCardGroup(tr["Setting"]["SubtitleDetectionSetting"], self.scrollWidget)
        # STTN设置组
        self.sttn_group = SettingCardGroup(tr["Setting"]["SttnSetting"], self.scrollWidget)
        # Propainter设置组
        self.propainter_group = SettingCardGroup(tr["Setting"]["ProPainterSetting"], self.scrollWidget)
        # MiniMax-Remover设置组
        self.minimax_group = SettingCardGroup(tr["Setting"]["MiniMaxSetting"], self.scrollWidget)
        # 通用设置组
        self.advanced_group = SettingCardGroup(tr["Setting"]["AdvancedSetting"], self.scrollWidget)
        # 外观设置组
        self.appearance_group = SettingCardGroup("外观", self.scrollWidget)

        self.theme_card = ThemeCard(
            "应用主题",
            "浅色 / 深色 / 跟随系统（主色固定为青绿），点击即刻生效",
            parent=self.appearance_group
        )
        
        self.subtitle_yx_axis_difference_pixel = RangeSettingCard(
            configItem=config.subtitleYXAxisDifferencePixel,
            icon=FluentIcon.ZOOM,
            title=tr["Setting"]["SubtitleYXAxisDifferencePixel"],
            content=tr["Setting"]["SubtitleYXAxisDifferencePixelDesc"],
            parent=self.subtitle_detection_group
        )
        
        self.subtitle_area_deviation_pixel = RangeSettingCard(
            configItem=config.subtitleAreaDeviationPixel,
            icon=FluentIcon.ZOOM_IN,
            title=tr["Setting"]["SubtitleAreaDeviationPixel"],
            content=tr["Setting"]["SubtitleAreaDeviationPixelDesc"],
            parent=self.subtitle_detection_group
        )
        
        self.subtitle_area_y_axis_difference_pixel = RangeSettingCard(
            configItem=config.subtitleAreaYAxisDifferencePixel,
            icon=FluentIcon.ALIGNMENT,
            title=tr["Setting"]["SubtitleAreaYAxisDifferencePixel"],
            content=tr["Setting"]["SubtitleAreaYAxisDifferencePixelDesc"],
            parent=self.subtitle_detection_group
        )

        self.subtitle_area_pixel_tolerance_y_pixel = RangeSettingCard(
            configItem=config.subtitleAreaPixelToleranceYPixel,
            icon=FluentIcon.UP,
            title=tr["Setting"]["SubtitleAreaPixelToleranceYPixel"],
            content=tr["Setting"]["SubtitleAreaPixelToleranceYPixelDesc"],
            parent=self.subtitle_detection_group
        )

        self.subtitle_area_pixel_tolerance_x_pixel = RangeSettingCard(
            configItem=config.subtitleAreaPixelToleranceXPixel,
            icon=FluentIcon.RIGHT_ARROW,
            title=tr["Setting"]["SubtitleAreaPixelToleranceXPixel"],
            content=tr["Setting"]["SubtitleAreaPixelToleranceXPixelDesc"],
            parent=self.subtitle_detection_group
        )

        self.subtitle_timeline_backward_frame_count = RangeSettingCard(
            configItem=config.subtitleTimelineBackwardFrameCount,
            icon=FluentIcon.PAGE_LEFT,
            title=tr["Setting"]["SubtitleTimelineBackwardFrameCount"],
            content=tr["Setting"]["SubtitleTimelineBackwardFrameCountDesc"],
            parent=self.subtitle_detection_group
        )

        self.subtitle_timeline_forward_frame_count = RangeSettingCard(
            configItem=config.subtitleTimelineForwardFrameCount,
            icon=FluentIcon.PAGE_RIGHT,
            title=tr["Setting"]["subtitleTimelineForwardFrameCount"],
            content=tr["Setting"]["subtitleTimelineForwardFrameCountDesc"],
            parent=self.subtitle_detection_group
        )

        self.subtitle_detect_sample_step = RangeSettingCard(
            configItem=config.subtitleDetectSampleStep,
            icon=FluentIcon.UNIT,
            title=tr["Setting"]["SubtitleDetectSampleStep"],
            content=tr["Setting"]["SubtitleDetectSampleStepDesc"],
            parent=self.subtitle_detection_group
        )

        self.subtitle_detect_process_width = RangeSettingCard(
            configItem=config.subtitleDetectProcessSize,
            icon=FluentIcon.FIT_PAGE,
            title=tr["Setting"]["SubtitleDetectProcessSize"],
            content=tr["Setting"]["SubtitleDetectProcessSizeDesc"],
            parent=self.subtitle_detection_group
        )

        self.sttn_neighbor_stride = RangeSettingCard(
            configItem=config.sttnNeighborStride,
            icon=FluentIcon.UNIT,
            title=tr["Setting"]["SttnNeighborStride"],
            content=tr["Setting"]["SttnNeighborStrideDesc"],
            parent=self.sttn_group
        )

        self.sttn_reference_length = RangeSettingCard(
            configItem=config.sttnReferenceLength,
            icon=FluentIcon.MORE,
            title=tr["Setting"]["SttnReferenceLength"],
            content=tr["Setting"]["SttnReferenceLengthDesc"],
            parent=self.sttn_group
        )

        self.sttn_max_load_num = RangeSettingCard(
            configItem=config.sttnMaxLoadNum,
            icon=FluentIcon.DICTIONARY,
            title=tr["Setting"]["SttnMaxLoadNum"],
            content=tr["Setting"]["SttnMaxLoadNumDesc"],
            parent=self.sttn_group
        )

        self.propainter_max_load_num = RangeSettingCard(
            configItem=config.propainterMaxLoadNum,
            icon=FluentIcon.DICTIONARY,
            title=tr["Setting"]["PropainterMaxLoadNum"],
            content=tr["Setting"]["PropainterMaxLoadNumDesc"],
            parent=self.propainter_group
        )

        self.minimax_max_load_num = RangeSettingCard(
            configItem=config.minimaxMaxLoadNum,
            icon=FluentIcon.DICTIONARY,
            title=tr["Setting"]["MiniMaxMaxLoadNum"],
            content=tr["Setting"]["MiniMaxMaxLoadNumDesc"],
            parent=self.minimax_group
        )

        self.minimax_num_inference_steps = RangeSettingCard(
            configItem=config.minimaxNumInferenceSteps,
            icon=FluentIcon.SPEED_HIGH,
            title=tr["Setting"]["MiniMaxNumInferenceSteps"],
            content=tr["Setting"]["MiniMaxNumInferenceStepsDesc"],
            parent=self.minimax_group
        )

        self.minimax_iterations = RangeSettingCard(
            configItem=config.minimaxIterations,
            icon=FluentIcon.ZOOM,
            title=tr["Setting"]["MiniMaxIterations"],
            content=tr["Setting"]["MiniMaxIterationsDesc"],
            parent=self.minimax_group
        )

        self.minimax_process_width = RangeSettingCard(
            configItem=config.minimaxProcessWidth,
            icon=FluentIcon.FIT_PAGE,
            title=tr["Setting"]["MiniMaxProcessWidth"],
            content=tr["Setting"]["MiniMaxProcessWidthDesc"],
            parent=self.minimax_group
        )

        self.minimax_strip_padding = RangeSettingCard(
            configItem=config.minimaxStripPadding,
            icon=FluentIcon.ZOOM,
            title=tr["Setting"]["MiniMaxStripPadding"],
            content=tr["Setting"]["MiniMaxStripPaddingDesc"],
            parent=self.minimax_group
        )

        self.minimax_block_overlap = RangeSettingCard(
            configItem=config.minimaxBlockOverlap,
            icon=FluentIcon.UNIT,
            title=tr["Setting"]["MiniMaxBlockOverlap"],
            content=tr["Setting"]["MiniMaxBlockOverlapDesc"],
            parent=self.minimax_group
        )

        self.minimax_vae_decode_fp32 = SwitchSettingCard(
            configItem=config.minimaxVaeDecodeFp32,
            icon=FluentIcon.ZOOM,
            title=tr["Setting"]["MiniMaxVaeDecodeFp32"],
            content=tr["Setting"]["MiniMaxVaeDecodeFp32Desc"],
            parent=self.minimax_group
        )
        self.minimax_torch_compile = SwitchSettingCard(
            configItem=config.minimaxTorchCompile,
            icon=FluentIcon.SPEED_HIGH,
            title=tr["Setting"]["MiniMaxTorchCompile"],
            content=tr["Setting"]["MiniMaxTorchCompileDesc"],
            parent=self.minimax_group
        )

        # 性能预设卡（引用上面五张卡，一键写入）
        self.minimax_preset_card = MiniMaxPresetCard(
            "性能预设",
            "按素材分辨率一键写入推荐参数（480p~4K），覆盖下方五项设置",
            parent=self.minimax_group
        )
        self.minimax_preset_card._cards = {
            "maxload": self.minimax_max_load_num,
            "steps": self.minimax_num_inference_steps,
            "iterations": self.minimax_iterations,
            "padding": self.minimax_strip_padding,
            "width": self.minimax_process_width,
        }

        # 视频保存路径
        self.save_directory = PushSettingCard(
            text=tr["Setting"]["ChooseDirectory"],
            icon=FluentIcon.DOWNLOAD,
            title=tr["Setting"]["SaveDirectory"],
            content=tr["Setting"]["SaveDirectoryDefault"] if not config.saveDirectory.value else config.saveDirectory.value,
            parent=self.advanced_group
        )
        self.save_directory.clicked.connect(self.choose_save_directory)

        # 环境自检（运行时/GPU/驱动/依赖/权重，缺失权重可一键下载）
        self.env_check_card = PushSettingCard(
            text="立即自检",
            icon=FluentIcon.SYNC,
            title="环境自检",
            content="检查 Python / GPU / 驱动 / 依赖 / 模型权重完整性，缺失权重支持自动下载",
            parent=self.advanced_group
        )
        self.env_check_card.clicked.connect(self.run_env_check)

    def choose_save_directory(self):
        """选择保存目录"""
        last_save_directory = "./" if not config.saveDirectory.value else config.saveDirectory.value
        folder = QFileDialog.getExistingDirectory(
            self, tr['Setting']['ChooseDirectory'], last_save_directory)
        if not folder:
            folder = ""

        config.set(config.saveDirectory, folder)
        self.save_directory.setContent(tr["Setting"]["SaveDirectoryDefault"] if not config.saveDirectory.value else config.saveDirectory.value)

    # ---------- 环境自检 ----------

    def _env_log(self, text):
        """把自检/下载日志写进主界面日志窗"""
        try:
            self.parent.homeInterface.append_output(text)
        except Exception:
            pass

    def run_env_check(self):
        """后台执行环境自检"""
        self.env_check_card.setContent("正在自检环境，请稍候...")
        TaskExecutor.runTask(lambda: check_all()).then(self.on_env_checked)

    def on_env_checked(self, payload):
        results, missing = payload
        lines = [f"{'✅' if r['ok'] else '❌'} {r['name']}: {r['detail']}" for r in results]
        report = '\n'.join(lines)
        for line in lines:
            self._env_log(line)
        self.env_check_card.setContent("全部通过 ✅" if not missing else f"发现 {len(missing)} 项缺失权重，详见日志")

        if missing:
            n_dl = sum(1 for m in missing if m.get('sources'))
            msg = report + f"\n\n缺失权重 {len(missing)} 项"
            if n_dl:
                msg += f"，其中 {n_dl} 项可自动下载（大文件支持断点续传）"
            msg += "\n是否立即下载？"
            w = MessageBox("环境自检", msg, self.window())
            if w.exec():
                self.env_check_card.setContent("正在下载缺失权重...")
                TaskExecutor.runTask(lambda: fix_weights(missing, log=self._env_log)).then(self.on_weights_fixed)
        else:
            InfoBar.success("环境自检", "全部通过，可以正常使用", duration=2500, parent=self.window())

    def on_weights_fixed(self, payload):
        fixed, manual = payload
        if manual:
            self._env_log(f"⚠ {len(manual)} 项无自动下载源，请按 README 手动获取: {', '.join(manual)}")
        self.env_check_card.setContent(f"下载完成 ✅（{len(fixed)} 项自动修复）" if fixed else "请按日志提示手动补齐")
        InfoBar.success("环境自检", f"权重修复完成：自动下载 {len(fixed)} 项", duration=2500, parent=self.window())