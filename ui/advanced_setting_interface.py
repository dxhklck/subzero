"""
@desc: 高级设置页（紧凑参数行 / 专业模式 / 恢复默认 / 主题 / 环境自检 / 保存目录）
       数值参数使用「滑条粗调 + 数字框精确键入（越界自动钳制）」双输入；
       专家参数默认隐藏，「专业模式」开关打开后显示（状态记忆在配置）。
"""

from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget, QFrame, QLabel
from qfluentwidgets import (ScrollArea, CardWidget, FluentIcon, SubtitleLabel,
                           PushSettingCard, SettingCard, PushButton, SwitchButton,
                           Slider, SpinBox, MessageBox, InfoBar, RadioButton,
                           StrongBodyLabel, BodyLabel, CaptionLabel, isDarkTheme)
from backend.config import config, tr, VERSION, PROJECT_HOME_URL, PROJECT_ISSUES_URL, PROJECT_RELEASES_URL
from backend.tools.version_service import VersionService
from backend.tools.concurrent import TaskExecutor
from backend.tools.bootstrap import check_all, fix_weights


def _accent_color():
    """主题感知的主色：浅色主题用主绿，深色主题用亮一档的绿"""
    return '#5DCAA5' if isDarkTheme() else '#1D9E75'


class CompactRangeRow(QFrame):
    """紧凑数值参数行：参数名+说明 | 滑条 | 数字框（含 −/+ 步进）。

    - 滑条粗调、数字框精确键入（越界自动钳制到 validator 范围）
    - 值 ≠ 出厂默认时，说明位置显示「已修改 · 默认 N」标记
    """

    def __init__(self, config_item, title, desc, expert=False, parent=None):
        super().__init__(parent)
        self.config_item = config_item
        self.expert = expert
        self._desc_text = desc
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet('CompactRangeRow { background: transparent; }')

        validator = config_item.validator
        lo, hi = int(validator.min), int(validator.max)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(12)

        # 左侧：参数名 + 说明（值偏离默认时说明让位给「已修改」标记）
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self._title_label = StrongBodyLabel(title, self)
        self._title_label.setStyleSheet('font-size: 13px;')
        title_row.addWidget(self._title_label)
        if expert:
            badge = CaptionLabel(tr['Setting']['ExpertBadge'], self)
            badge.setStyleSheet(
                'color:#854F0B; background:#FAEEDA; border-radius:7px; padding:0 7px; font-size:10px;')
            title_row.addWidget(badge)
        title_row.addStretch(1)
        text_col.addLayout(title_row)
        self._desc_label = CaptionLabel(desc, self)
        text_col.addWidget(self._desc_label)
        layout.addLayout(text_col, 0)

        # 中部：滑条
        self.slider = Slider(Qt.Horizontal, self)
        self.slider.setRange(lo, hi)
        layout.addWidget(self.slider, 1)

        # 右侧：数字框（SpinBox 自带键盘输入与上下步进）
        self.spin = SpinBox(self)
        self.spin.setRange(lo, hi)
        self.spin.setFixedWidth(72)
        self.spin.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.spin, 0)

        self._syncing = False
        v = int(config_item.value)
        self.slider.setValue(v)
        self.spin.setValue(v)
        self._refresh_mark()

        # UI -> 配置（写盘即保存）；Qt 信号仅在值变化时触发，天然去重
        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)
        # 配置 -> UI（恢复默认等外部写入时同步）
        config_item.valueChanged.connect(self._on_config_changed)

    def _on_slider(self, v):
        if self._syncing:
            return
        self._syncing = True
        self.spin.setValue(v)
        self._syncing = False
        config.set(self.config_item, int(v))
        self._refresh_mark()

    def _on_spin(self, v):
        if self._syncing:
            return
        self._syncing = True
        self.slider.setValue(v)
        self._syncing = False
        config.set(self.config_item, int(v))
        self._refresh_mark()

    def _on_config_changed(self, v):
        self._syncing = True
        self.slider.setValue(int(v))
        self.spin.setValue(int(v))
        self._syncing = False
        self._refresh_mark()

    def _refresh_mark(self):
        """值偏离出厂默认时，说明行让位给「已修改 · 默认 N」标记"""
        if self.config_item.value != self.config_item.defaultValue:
            self._desc_label.setText(tr['Setting']['ModifiedTag'].format(self.config_item.defaultValue))
            self._desc_label.setStyleSheet(f'color: {_accent_color()}; font-size: 11px;')
            self._title_label.setStyleSheet(f'font-size: 13px; color: {_accent_color()};')
        else:
            self._desc_label.setText(self._desc_text)
            self._desc_label.setStyleSheet('color: var(--color-text-secondary); font-size: 11px;')
            self._title_label.setStyleSheet('font-size: 13px;')

    def refresh_row(self):
        self._on_config_changed(self.config_item.value)


class CompactSwitchRow(QFrame):
    """紧凑开关参数行：参数名+说明 | 开关"""

    def __init__(self, config_item, title, desc, expert=False, parent=None):
        super().__init__(parent)
        self.config_item = config_item
        self.expert = expert
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet('CompactSwitchRow { background: transparent; }')

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self._title_label = StrongBodyLabel(title, self)
        self._title_label.setStyleSheet('font-size: 13px;')
        title_row.addWidget(self._title_label)
        if expert:
            badge = CaptionLabel(tr['Setting']['ExpertBadge'], self)
            badge.setStyleSheet(
                'color:#854F0B; background:#FAEEDA; border-radius:7px; padding:0 7px; font-size:10px;')
            title_row.addWidget(badge)
        title_row.addStretch(1)
        text_col.addLayout(title_row)
        text_col.addWidget(CaptionLabel(desc, self))
        layout.addLayout(text_col, 0)
        layout.addStretch(1)
        self.switch = SwitchButton(self)
        self.switch.setChecked(bool(config_item.value))
        self.switch.checkedChanged.connect(self._on_toggle)
        layout.addWidget(self.switch)

    def _on_toggle(self, checked):
        config.set(self.config_item, bool(checked))

    def refresh_row(self):
        self.switch.setChecked(bool(self.config_item.value))


class ThemeCard(SettingCard):
    """应用主题卡：三个单选按钮直接在卡片内选择（浅色/深色/跟随系统），无下拉"""

    def __init__(self, title, content, parent=None):
        super().__init__(FluentIcon.BRUSH, title, content, parent)
        self._btns = {}
        for key, text in [("light", tr["Setting"]["ThemeLight"]), ("dark", tr["Setting"]["ThemeDark"]),
                          ("auto", tr["Setting"]["ThemeAuto"])]:
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
        self.scrollWidget = QWidget(self)
        self.page_layout = QVBoxLayout(self.scrollWidget)
        self.page_layout.setContentsMargins(16, 12, 16, 48)
        self.page_layout.setSpacing(12)

        self.setWidget(self.scrollWidget)
        self.enableTransparentBackground()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setAttribute(QtCore.Qt.WA_StyledBackground)

        self._build_header()
        self._build_detect_group()
        self._build_minimax_group()
        self._build_sttn_group()
        self._build_maintenance_group()
        self.page_layout.addStretch(1)
        self._apply_expert_visibility()

    # ---------- 页头：标题 + 专业模式开关 + 恢复默认 ----------

    def _build_header(self):
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.addWidget(SubtitleLabel(tr['Setting']['AdvancedSetting']))
        self.page_layout.addLayout(title_col)

        header.addStretch(1)
        expert_box = QHBoxLayout()
        expert_box.setSpacing(6)
        self.expert_switch = SwitchButton(tr['Setting']['ExpertMode'], self)
        self.expert_switch.setChecked(bool(config.expertMode.value))
        self.expert_switch.checkedChanged.connect(self._on_expert_toggled)
        expert_box.addWidget(self.expert_switch)
        header.addLayout(expert_box)

        self.restore_btn = PushButton(tr['Setting']['RestoreDefaultsBtn'], self)
        self.restore_btn.clicked.connect(self._on_restore_defaults)
        header.addWidget(self.restore_btn)
        self.page_layout.addLayout(header)

    # ---------- 分组构建 ----------

    def _make_group(self, title):
        """一组：绿色分组标题 + 白卡容器（含行布局），返回 (card, vbox, header)"""
        header = StrongBodyLabel(title, self.scrollWidget)
        header.setStyleSheet(f'color: {_accent_color()}; font-size: 12px;')
        card = CardWidget(self.scrollWidget)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(0, 2, 0, 2)
        vbox.setSpacing(0)
        self.page_layout.addWidget(header)
        self.page_layout.addWidget(card)
        return card, vbox, header

    def _add_row(self, vbox, row):
        vbox.addWidget(row)
        return row

    def _build_detect_group(self):
        card, vbox, self._detect_header = self._make_group(tr['Setting']['SubtitleDetectionSetting'])
        self.rows_detect = [
            self._add_row(vbox, CompactRangeRow(
                config.subtitleDetectProcessSize, tr['Setting']['SubtitleDetectProcessSize'],
                tr['Setting']['SubtitleDetectProcessSizeDesc'])),
            self._add_row(vbox, CompactRangeRow(
                config.subtitleDetectSampleStep, tr['Setting']['SubtitleDetectSampleStep'],
                tr['Setting']['SubtitleDetectSampleStepDesc'])),
            self._add_row(vbox, CompactRangeRow(
                config.subtitleTimelineForwardFrameCount, tr['Setting']['subtitleTimelineForwardFrameCount'],
                tr['Setting']['subtitleTimelineForwardFrameCountDesc'])),
            self._add_row(vbox, CompactRangeRow(
                config.subtitleTimelineBackwardFrameCount, tr['Setting']['SubtitleTimelineBackwardFrameCount'],
                tr['Setting']['SubtitleTimelineBackwardFrameCountDesc'])),
            self._add_row(vbox, CompactRangeRow(
                config.subtitleAreaDeviationPixel, tr['Setting']['SubtitleAreaDeviationPixel'],
                tr['Setting']['SubtitleAreaDeviationPixelDesc'])),
            self._add_row(vbox, CompactRangeRow(
                config.autoRouteStaticThreshold, tr['Setting']['AutoRouteStaticThreshold'],
                tr['Setting']['AutoRouteStaticThresholdDesc'])),
        ]
        self.rows_detect_expert = [
            self._add_row(vbox, CompactRangeRow(
                config.subtitleYXAxisDifferencePixel, tr['Setting']['SubtitleYXAxisDifferencePixel'],
                tr['Setting']['SubtitleYXAxisDifferencePixelDesc'], expert=True)),
            self._add_row(vbox, CompactRangeRow(
                config.subtitleAreaYAxisDifferencePixel, tr['Setting']['SubtitleAreaYAxisDifferencePixel'],
                tr['Setting']['SubtitleAreaYAxisDifferencePixelDesc'], expert=True)),
            self._add_row(vbox, CompactRangeRow(
                config.subtitleAreaPixelToleranceYPixel, tr['Setting']['SubtitleAreaPixelToleranceYPixel'],
                tr['Setting']['SubtitleAreaPixelToleranceYPixelDesc'], expert=True)),
            self._add_row(vbox, CompactRangeRow(
                config.subtitleAreaPixelToleranceXPixel, tr['Setting']['SubtitleAreaPixelToleranceXPixel'],
                tr['Setting']['SubtitleAreaPixelToleranceXPixelDesc'], expert=True)),
        ]

    def _build_minimax_group(self):
        card, vbox, self._minimax_header = self._make_group(tr['Setting']['MiniMaxSetting'])
        self.rows_minimax = [
            self._add_row(vbox, CompactRangeRow(
                config.minimaxMaxLoadNum, tr['Setting']['MiniMaxMaxLoadNum'],
                tr['Setting']['MiniMaxMaxLoadNumDesc'])),
            self._add_row(vbox, CompactRangeRow(
                config.minimaxNumInferenceSteps, tr['Setting']['MiniMaxNumInferenceSteps'],
                tr['Setting']['MiniMaxNumInferenceStepsDesc'])),
            self._add_row(vbox, CompactRangeRow(
                config.minimaxBlockOverlap, tr['Setting']['MiniMaxBlockOverlap'],
                tr['Setting']['MiniMaxBlockOverlapDesc'])),
        ]
        self.rows_minimax_expert = [
            self._add_row(vbox, CompactRangeRow(
                config.minimaxIterations, tr['Setting']['MiniMaxIterations'],
                tr['Setting']['MiniMaxIterationsDesc'], expert=True)),
            self._add_row(vbox, CompactRangeRow(
                config.minimaxProcessWidth, tr['Setting']['MiniMaxProcessWidth'],
                tr['Setting']['MiniMaxProcessWidthDesc'], expert=True)),
            self._add_row(vbox, CompactRangeRow(
                config.minimaxStripPadding, tr['Setting']['MiniMaxStripPadding'],
                tr['Setting']['MiniMaxStripPaddingDesc'], expert=True)),
            self._add_row(vbox, CompactSwitchRow(
                config.minimaxVaeDecodeFp32, tr['Setting']['MiniMaxVaeDecodeFp32'],
                tr['Setting']['MiniMaxVaeDecodeFp32Desc'], expert=True)),
        ]

    def _build_sttn_group(self):
        card, vbox, self._sttn_header = self._make_group(tr['Setting']['SttnSetting'])
        self.rows_sttn = [
            self._add_row(vbox, CompactRangeRow(
                config.sttnNeighborStride, tr['Setting']['SttnNeighborStride'],
                tr['Setting']['SttnNeighborStrideDesc'], expert=True)),
            self._add_row(vbox, CompactRangeRow(
                config.sttnReferenceLength, tr['Setting']['SttnReferenceLength'],
                tr['Setting']['SttnReferenceLengthDesc'], expert=True)),
            self._add_row(vbox, CompactRangeRow(
                config.sttnMaxLoadNum, tr['Setting']['SttnMaxLoadNum'],
                tr['Setting']['SttnMaxLoadNumDesc'], expert=True)),
        ]
        self.sttn_card = card

    def _build_maintenance_group(self):
        card, vbox, self._maint_header = self._make_group(tr['Setting']['AppearanceSetting'])
        self.theme_card = ThemeCard(
            tr['Setting']['AppTheme'], tr['Setting']['AppThemeDesc'], parent=card)
        vbox.addWidget(self.theme_card)

        self.save_directory = PushSettingCard(
            text=tr['Setting']['ChooseDirectory'],
            icon=FluentIcon.DOWNLOAD,
            title=tr['Setting']['SaveDirectory'],
            content=tr['Setting']['SaveDirectoryDefault'] if not config.saveDirectory.value else config.saveDirectory.value,
            parent=card
        )
        self.save_directory.clicked.connect(self.choose_save_directory)
        vbox.addWidget(self.save_directory)

        self.env_check_card = PushSettingCard(
            text=tr['Setting']['EnvCheckBtn'],
            icon=FluentIcon.SYNC,
            title=tr['Setting']['EnvCheck'],
            content=tr['Setting']['EnvCheckDesc'],
            parent=card
        )
        self.env_check_card.clicked.connect(self.run_env_check)
        vbox.addWidget(self.env_check_card)

    # ---------- 专业模式 ----------

    def _on_expert_toggled(self, checked):
        config.set(config.expertMode, bool(checked))
        self._apply_expert_visibility()

    def _apply_expert_visibility(self):
        expert = bool(config.expertMode.value)
        for row in self.rows_detect_expert + self.rows_minimax_expert + self.rows_sttn:
            row.setVisible(expert)
        # STTN 组整体是专家内容：普通模式连组标题一起隐藏
        self._sttn_header.setVisible(expert)
        self.sttn_card.setVisible(expert)

    # ---------- 恢复默认 ----------

    def _on_restore_defaults(self):
        total = len(self.rows_detect) + len(self.rows_detect_expert) + \
            len(self.rows_minimax) + len(self.rows_minimax_expert) + len(self.rows_sttn)
        msg = MessageBox(
            tr['Setting']['RestoreDefaultsTitle'],
            tr['Setting']['RestoreDefaultsContent'].format(total),
            self.window())
        msg.yesButton.setText(tr['Setting']['DialogConfirm'])
        msg.cancelButton.setText(tr['Setting']['DialogCancel'])
        if not msg.exec():
            return
        all_rows = (self.rows_detect + self.rows_detect_expert +
                    self.rows_minimax + self.rows_minimax_expert + self.rows_sttn)
        for row in all_rows:
            config.set(row.config_item, row.config_item.defaultValue)
            row.refresh_row()
        InfoBar.success(
            tr['Setting']['RestoreDefaultsTitle'],
            tr['Setting']['RestoreDone'].format(total),
            duration=2500, parent=self.window())

    # ---------- 保存目录 / 环境自检（保持原逻辑） ----------

    def choose_save_directory(self):
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
        self.env_check_card.setContent(tr["Setting"]["EnvCheckRunning"])
        TaskExecutor.runTask(lambda: check_all()).then(self.on_env_checked)

    def on_env_checked(self, payload):
        results, missing = payload
        lines = [f"{'✅' if r['ok'] else '❌'} {r['name']}: {r['detail']}" for r in results]
        report = '\n'.join(lines)
        for line in lines:
            self._env_log(line)
        self.env_check_card.setContent(
            f'{tr["Setting"]["EnvCheckPass"]} ✅' if not missing
            else tr["Setting"]["EnvCheckMissing"].format(len(missing)))

        if missing:
            n_dl = sum(1 for m in missing if m.get('sources'))
            msg = report + f"\n\n{tr['Setting']['EnvCheckMissingSummary'].format(len(missing))}"
            if n_dl:
                msg += f"，{tr['Setting']['EnvCheckDownloadable'].format(n_dl)}"
            msg += f"\n{tr['Setting']['EnvCheckAskDownload']}"
            w = MessageBox(tr["Setting"]["EnvCheck"], msg, self.window())
            if w.exec():
                self.env_check_card.setContent(tr["Setting"]["EnvCheckDownloading"])
                TaskExecutor.runTask(lambda: fix_weights(missing, log=self._env_log)).then(self.on_weights_fixed)
        else:
            InfoBar.success(tr["Setting"]["EnvCheck"], tr["Setting"]["EnvCheckAllGood"],
                            duration=2500, parent=self.window())

    def on_weights_fixed(self, payload):
        fixed, manual = payload
        if manual:
            self._env_log(f"⚠ {tr['Setting']['EnvCheckNoSource'].format(len(manual), ', '.join(manual))}")
        self.env_check_card.setContent(
            f'{tr["Setting"]["EnvCheckDownloadDone"].format(len(fixed))} ✅' if fixed
            else tr["Setting"]["EnvCheckManualHint"])
        InfoBar.success(tr["Setting"]["EnvCheck"],
                        tr["Setting"]["EnvCheckRepaired"].format(len(fixed)),
                        duration=2500, parent=self.window())
