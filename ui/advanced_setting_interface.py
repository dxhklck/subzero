"""
@desc: 高级设置页（紧凑参数行 / 专业模式 / 恢复默认 / 主题 / 环境自检 / 保存目录）
       数值参数使用「滑条粗调 + 数字框精确键入（越界自动钳制）」双输入；
       专家参数默认隐藏，「专业模式」开关打开后显示（状态记忆在配置）。
"""

import os
import sys
import subprocess

from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget, QFrame, QLabel
from qfluentwidgets import (ScrollArea, CardWidget, FluentIcon,
                           PushSettingCard, SettingCard, PushButton,
                           Slider, SpinBox, ComboBox, MessageBox, InfoBar, RadioButton,
                           StrongBodyLabel, BodyLabel, CaptionLabel, isDarkTheme)
from backend.config import (config, tr, VERSION, PROJECT_HOME_URL, PROJECT_ISSUES_URL,
                           PROJECT_RELEASES_URL, HARDWARD_ACCELERATION_OPTION, BASE_DIR)
from backend.tools.version_service import VersionService
from backend.tools.concurrent import TaskExecutor
from backend.tools.bootstrap import check_all, fix_weights
from ui.component.compact_rows import (CompactRangeRow, CompactSwitchRow,
                                       CompactComboRow, CompactParamRow,
                                       accent_color as _accent_color,
                                       set_row_tooltip)


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
        # 语言切换需重启生效：记录启动时语言，切换时弹窗确认并一键重启
        self._interface_at_init = config.interface.value
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
        self._build_general_group()
        self._build_detect_group()
        self._build_minimax_group()
        self._build_sttn_group()
        self._build_maintenance_group()
        self.page_layout.addStretch(1)

    # ---------- 页头：标题 + 恢复默认（参数全部直接展示，不再折叠隐藏） ----------

    def _build_header(self):
        header = QHBoxLayout()
        # 恢复默认按钮靠最左侧（无页面大标题）
        self.restore_btn = PushButton(tr['Setting']['RestoreDefaultsBtn'], self)
        self.restore_btn.clicked.connect(self._on_restore_defaults)
        header.addWidget(self.restore_btn)
        header.addStretch(1)
        self.page_layout.addLayout(header)

    # ---------- 分组构建 ----------

    def _build_general_group(self):
        """通用：界面语言 + 硬件加速 + 视频分段时长（一行三列）"""
        card, vbox, self._general_header = self._make_group(
            tr['SubtitleExtractorGUI']['GroupGeneral'])
        gui = tr['SubtitleExtractorGUI']
        setting_tr = tr['Setting']
        self.lang_row = CompactComboRow(
            config.interface, gui['InterfaceLanguage'], gui['InterfaceLanguageDesc'],
            texts=list(config.intefaceTexts.keys()),
            values=list(config.intefaceTexts.values()),
            on_user_changed=self._on_interface_changed,
            icon=FluentIcon.LANGUAGE, parent=card)
        self.hw_row = CompactSwitchRow(
            config.hardwareAcceleration, setting_tr['HardwareAcceleration'],
            setting_tr['HardwareAccelerationDesc'],
            icon=FluentIcon.SPEED_HIGH, parent=card)
        if not HARDWARD_ACCELERATION_OPTION:
            self.hw_row.switch.setEnabled(False)
            self.hw_row.switch.setChecked(False)
            self.hw_row.setToolTip(setting_tr['HardwareAccelerationNO'])
            config.set(config.hardwareAcceleration, False)
        self.segment_row = CompactRangeRow(
            config.segmentSeconds, gui['SegmentMinutes'],
            gui['SegmentMinutesDesc'], icon=FluentIcon.STOP_WATCH, parent=card)
        vbox.addWidget(CompactParamRow(self.lang_row, self.hw_row, self.segment_row))

    def _make_group(self, title):
        """一组：分组标题（主题色 + 短下划线，参照卡片式设计稿）+ 卡片容器，返回 (card, vbox, header)"""
        head_col = QVBoxLayout()
        head_col.setSpacing(4)
        header = StrongBodyLabel(title, self.scrollWidget)
        header.setStyleSheet(f'color: {_accent_color()}; font-size: 13px; font-weight: 600;')
        underline = QFrame(self.scrollWidget)
        underline.setFixedSize(42, 2)
        underline.setStyleSheet(f'background: {_accent_color()}; border-radius: 1px;')
        head_col.addWidget(header)
        head_col.addWidget(underline)
        head_col.addSpacing(2)
        card = CardWidget(self.scrollWidget)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(0, 2, 0, 2)
        vbox.setSpacing(0)
        self.page_layout.addLayout(head_col)
        self.page_layout.addWidget(card)
        return card, vbox, header

    def _add_row(self, vbox, row):
        vbox.addWidget(row)
        return row

    def _build_detect_group(self):
        card, vbox, self._detect_header = self._make_group(tr['Setting']['SubtitleDetectionSetting'])
        gui = tr['SubtitleExtractorGUI']
        self.detect_model_row = CompactComboRow(
            config.subtitleDetectMode, gui['SubtitleDetectMode'], gui['SubtitleDetectModeDesc'],
            texts=[list(tr['SubtitleDetectMode'].values())[i]
                   for i, _ in enumerate(config.subtitleDetectMode.validator.options)],
            values=list(config.subtitleDetectMode.validator.options),
            icon=FluentIcon.SEARCH, parent=card)
        self.residue_row = CompactSwitchRow(
            config.residueCheck, gui['ResidueCheck'], gui['ResidueCheckDesc'],
            icon=FluentIcon.BROOM, parent=card)
        r1 = CompactRangeRow(
            config.subtitleDetectProcessSize, tr['Setting']['SubtitleDetectProcessSize'],
            tr['Setting']['SubtitleDetectProcessSizeDesc'], icon=FluentIcon.FULL_SCREEN, parent=card)
        vbox.addWidget(CompactParamRow(self.detect_model_row, self.residue_row, r1))
        r2 = CompactRangeRow(
            config.subtitleDetectSampleStep, tr['Setting']['SubtitleDetectSampleStep'],
            tr['Setting']['SubtitleDetectSampleStepDesc'], icon=FluentIcon.STOP_WATCH, parent=card)
        r3 = CompactRangeRow(
            config.subtitleTimelineForwardFrameCount, tr['Setting']['subtitleTimelineForwardFrameCount'],
            tr['Setting']['subtitleTimelineForwardFrameCountDesc'], icon=FluentIcon.MOVIE, parent=card)
        r4 = CompactRangeRow(
            config.subtitleTimelineBackwardFrameCount, tr['Setting']['SubtitleTimelineBackwardFrameCount'],
            tr['Setting']['SubtitleTimelineBackwardFrameCountDesc'], icon=FluentIcon.VIDEO, parent=card)
        vbox.addWidget(CompactParamRow(r2, r3, r4))
        r5 = CompactRangeRow(
            config.subtitleAreaDeviationPixel, tr['Setting']['SubtitleAreaDeviationPixel'],
            tr['Setting']['SubtitleAreaDeviationPixelDesc'], icon=FluentIcon.ZOOM_IN, parent=card)
        r6 = CompactRangeRow(
            config.autoRouteStaticThreshold, tr['Setting']['AutoRouteStaticThreshold'],
            tr['Setting']['AutoRouteStaticThresholdDesc'], icon=FluentIcon.SYNC, parent=card)
        e1 = CompactRangeRow(
            config.subtitleYXAxisDifferencePixel, tr['Setting']['SubtitleYXAxisDifferencePixel'],
            tr['Setting']['SubtitleYXAxisDifferencePixelDesc'], expert=True, icon=FluentIcon.ZOOM_IN, parent=card)
        vbox.addWidget(CompactParamRow(r5, r6, e1))
        e2 = CompactRangeRow(
            config.subtitleAreaYAxisDifferencePixel, tr['Setting']['SubtitleAreaYAxisDifferencePixel'],
            tr['Setting']['SubtitleAreaYAxisDifferencePixelDesc'], expert=True, icon=FluentIcon.ZOOM_IN, parent=card)
        e3 = CompactRangeRow(
            config.subtitleAreaPixelToleranceYPixel, tr['Setting']['SubtitleAreaPixelToleranceYPixel'],
            tr['Setting']['SubtitleAreaPixelToleranceYPixelDesc'], expert=True, icon=FluentIcon.ZOOM_IN, parent=card)
        e4 = CompactRangeRow(
            config.subtitleAreaPixelToleranceXPixel, tr['Setting']['SubtitleAreaPixelToleranceXPixel'],
            tr['Setting']['SubtitleAreaPixelToleranceXPixelDesc'], expert=True, icon=FluentIcon.ZOOM_IN, parent=card)
        vbox.addWidget(CompactParamRow(e2, e3, e4))
        self.rows_detect = [r1, r2, r3, r4, r5, r6]
        self.rows_detect_expert = [e1, e2, e3, e4]

    def _build_minimax_group(self):
        card, vbox, self._minimax_header = self._make_group(tr['Setting']['MiniMaxSetting'])
        m1 = CompactRangeRow(
            config.minimaxMaxLoadNum, tr['Setting']['MiniMaxMaxLoadNum'],
            tr['Setting']['MiniMaxMaxLoadNumDesc'], icon=FluentIcon.CLOUD, parent=card)
        m2 = CompactRangeRow(
            config.minimaxNumInferenceSteps, tr['Setting']['MiniMaxNumInferenceSteps'],
            tr['Setting']['MiniMaxNumInferenceStepsDesc'], icon=FluentIcon.HISTORY, parent=card)
        m3 = CompactRangeRow(
            config.minimaxBlockOverlap, tr['Setting']['MiniMaxBlockOverlap'],
            tr['Setting']['MiniMaxBlockOverlapDesc'], icon=FluentIcon.LAYOUT, parent=card)
        vbox.addWidget(CompactParamRow(m1, m2, m3))
        m4 = CompactRangeRow(
            config.minimaxIterations, tr['Setting']['MiniMaxIterations'],
            tr['Setting']['MiniMaxIterationsDesc'], expert=True, icon=FluentIcon.SYNC, parent=card)
        m5 = CompactRangeRow(
            config.minimaxProcessWidth, tr['Setting']['MiniMaxProcessWidth'],
            tr['Setting']['MiniMaxProcessWidthDesc'], expert=True, icon=FluentIcon.FIT_PAGE, parent=card)
        m6 = CompactRangeRow(
            config.minimaxStripPadding, tr['Setting']['MiniMaxStripPadding'],
            tr['Setting']['MiniMaxStripPaddingDesc'], expert=True, icon=FluentIcon.ALBUM, parent=card)
        vbox.addWidget(CompactParamRow(m4, m5, m6))
        m7 = CompactSwitchRow(
            config.minimaxVaeDecodeFp32, tr['Setting']['MiniMaxVaeDecodeFp32'],
            tr['Setting']['MiniMaxVaeDecodeFp32Desc'], expert=True, icon=FluentIcon.PALETTE, parent=card)
        vbox.addWidget(m7)
        self.rows_minimax = [m1, m2, m3]
        self.rows_minimax_expert = [m4, m5, m6, m7]

    def _build_sttn_group(self):
        card, vbox, self._sttn_header = self._make_group(tr['Setting']['SttnSetting'])
        s1 = CompactRangeRow(
            config.sttnNeighborStride, tr['Setting']['SttnNeighborStride'],
            tr['Setting']['SttnNeighborStrideDesc'], expert=True, icon=FluentIcon.LINK, parent=card)
        s2 = CompactRangeRow(
            config.sttnReferenceLength, tr['Setting']['SttnReferenceLength'],
            tr['Setting']['SttnReferenceLengthDesc'], expert=True, icon=FluentIcon.LIBRARY, parent=card)
        s3 = CompactRangeRow(
            config.sttnMaxLoadNum, tr['Setting']['SttnMaxLoadNum'],
            tr['Setting']['SttnMaxLoadNumDesc'], expert=True, icon=FluentIcon.CLOUD, parent=card)
        vbox.addWidget(CompactParamRow(s1, s2, s3))
        self.rows_sttn = [s1, s2, s3]
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

    # ---------- 恢复默认 ----------

    def _on_restore_defaults(self):
        moved_rows = [self.lang_row, self.detect_model_row,
                      self.residue_row, self.hw_row, self.segment_row]
        total = len(self.rows_detect) + len(self.rows_detect_expert) + \
            len(self.rows_minimax) + len(self.rows_minimax_expert) + len(self.rows_sttn) + \
            len(moved_rows)
        msg = MessageBox(
            tr['Setting']['RestoreDefaultsTitle'],
            tr['Setting']['RestoreDefaultsContent'].format(total),
            self.window())
        msg.yesButton.setText(tr['Setting']['DialogConfirm'])
        msg.cancelButton.setText(tr['Setting']['DialogCancel'])
        if not msg.exec():
            return
        all_rows = (self.rows_detect + self.rows_detect_expert +
                    self.rows_minimax + self.rows_minimax_expert + self.rows_sttn +
                    moved_rows)
        for row in all_rows:
            config.set(row.config_item, row.config_item.defaultValue)
            row.refresh_row()
        InfoBar.success(
            tr['Setting']['RestoreDefaultsTitle'],
            tr['Setting']['RestoreDone'].format(total),
            duration=2500, parent=self.window())

    # ---------- 语言切换（从主页迁移） ----------

    def _on_interface_changed(self):
        """语言切换：弹窗确认并一键重启（配置已写盘，重启后按新语言加载界面）"""
        if config.interface.value == self._interface_at_init:
            return
        gui = tr['SubtitleExtractorGUI']
        box = MessageBox(gui['RestartRequiredTitle'], gui['RestartRequiredContent'], self.window())
        box.yesButton.setText(gui['RestartNow'])
        box.cancelButton.setText(gui['RestartLater'])
        if box.exec():
            self._restart_app()

    def _restart_app(self):
        """启动新进程后退出当前进程（优先用 pythonw，避免重启后出现黑窗）"""
        exe = sys.executable
        if exe.lower().endswith('python.exe'):
            pythonw = os.path.join(os.path.dirname(exe), 'pythonw.exe')
            if os.path.exists(pythonw):
                exe = pythonw
        root_dir = os.path.dirname(BASE_DIR)
        subprocess.Popen([exe, os.path.join(root_dir, 'gui.py')], cwd=root_dir)
        QtWidgets.QApplication.quit()

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
