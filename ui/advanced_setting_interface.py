"""
@desc: 设置页（紧凑参数行 / 四列布局 / 恢复默认）
       分组自上而下：字幕检测 → MiniMax → STTN → 通用（含外观与关于，置于最下）；
       说明一律挂 tooltip 悬停显示，不占行内空间。
"""

import os
import sys
import subprocess

from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget, QFrame
from qfluentwidgets import (ScrollArea, CardWidget, FluentIcon, PushButton,
                           MessageBox, InfoBar, StrongBodyLabel, CaptionLabel)
from backend.config import (config, tr, VERSION, PROJECT_HOME_URL, PROJECT_UPDATE_URLS,
                           PROJECT_RELEASES_URL, HARDWARD_ACCELERATION_OPTION, BASE_DIR)
from backend.tools.version_service import VersionService
from backend.tools.concurrent import TaskExecutor
from backend.tools.bootstrap import check_all, fix_weights
from ui.component.compact_rows import (CompactRangeRow, CompactSwitchRow,
                                       CompactComboRow, CompactParamRow,
                                       CompactActionRow,
                                       accent_color as _accent_color,
                                       set_row_tooltip)


class AdvancedSettingInterface(ScrollArea):
    """设置页面"""

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
        self._build_detect_group()
        self._build_minimax_group()
        self._build_sttn_group()
        self._build_general_group()
        self.page_layout.addStretch(1)

    # ---------- 页头：恢复默认（按钮靠最左） ----------

    def _build_header(self):
        header = QHBoxLayout()
        self.restore_btn = PushButton(tr['Setting']['RestoreDefaultsBtn'], self)
        self.restore_btn.clicked.connect(self._on_restore_defaults)
        header.addWidget(self.restore_btn)
        header.addStretch(1)
        self.page_layout.addLayout(header)

    # ---------- 分组构建 ----------

    def _make_group(self, title):
        """一组：分组标题（主题色 + 短下划线）+ 卡片容器，返回 (card, vbox, header)"""
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

    def _build_detect_group(self):
        """字幕检测：四列布局（3 行 × 4）"""
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
        r2 = CompactRangeRow(
            config.subtitleDetectSampleStep, tr['Setting']['SubtitleDetectSampleStep'],
            tr['Setting']['SubtitleDetectSampleStepDesc'], icon=FluentIcon.STOP_WATCH, parent=card)
        vbox.addWidget(CompactParamRow(self.detect_model_row, self.residue_row, r1, r2, columns=4))
        r3 = CompactRangeRow(
            config.subtitleTimelineForwardFrameCount, tr['Setting']['subtitleTimelineForwardFrameCount'],
            tr['Setting']['subtitleTimelineForwardFrameCountDesc'], icon=FluentIcon.MOVIE, parent=card)
        r4 = CompactRangeRow(
            config.subtitleTimelineBackwardFrameCount, tr['Setting']['SubtitleTimelineBackwardFrameCount'],
            tr['Setting']['SubtitleTimelineBackwardFrameCountDesc'], icon=FluentIcon.VIDEO, parent=card)
        r5 = CompactRangeRow(
            config.subtitleAreaDeviationPixel, tr['Setting']['SubtitleAreaDeviationPixel'],
            tr['Setting']['SubtitleAreaDeviationPixelDesc'], icon=FluentIcon.ZOOM_IN, parent=card)
        r6 = CompactRangeRow(
            config.autoRouteStaticThreshold, tr['Setting']['AutoRouteStaticThreshold'],
            tr['Setting']['AutoRouteStaticThresholdDesc'], icon=FluentIcon.SYNC, parent=card)
        vbox.addWidget(CompactParamRow(r3, r4, r5, r6, columns=4))
        e1 = CompactRangeRow(
            config.subtitleYXAxisDifferencePixel, tr['Setting']['SubtitleYXAxisDifferencePixel'],
            tr['Setting']['SubtitleYXAxisDifferencePixelDesc'], expert=True, icon=FluentIcon.ZOOM_IN, parent=card)
        e2 = CompactRangeRow(
            config.subtitleAreaYAxisDifferencePixel, tr['Setting']['SubtitleAreaYAxisDifferencePixel'],
            tr['Setting']['SubtitleAreaYAxisDifferencePixelDesc'], expert=True, icon=FluentIcon.ZOOM_IN, parent=card)
        e3 = CompactRangeRow(
            config.subtitleAreaPixelToleranceYPixel, tr['Setting']['SubtitleAreaPixelToleranceYPixel'],
            tr['Setting']['SubtitleAreaPixelToleranceYPixelDesc'], expert=True, icon=FluentIcon.ZOOM_IN, parent=card)
        e4 = CompactRangeRow(
            config.subtitleAreaPixelToleranceXPixel, tr['Setting']['SubtitleAreaPixelToleranceXPixel'],
            tr['Setting']['SubtitleAreaPixelToleranceXPixelDesc'], expert=True, icon=FluentIcon.ZOOM_IN, parent=card)
        vbox.addWidget(CompactParamRow(e1, e2, e3, e4, columns=4))
        self.rows_detect = [r1, r2, r3, r4, r5, r6]
        self.rows_detect_expert = [e1, e2, e3, e4]

    def _build_minimax_group(self):
        """MiniMax：四列布局（2 行 × 4）"""
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
        m4 = CompactRangeRow(
            config.minimaxIterations, tr['Setting']['MiniMaxIterations'],
            tr['Setting']['MiniMaxIterationsDesc'], expert=True, icon=FluentIcon.SYNC, parent=card)
        vbox.addWidget(CompactParamRow(m1, m2, m3, m4, columns=4))
        m5 = CompactRangeRow(
            config.minimaxProcessWidth, tr['Setting']['MiniMaxProcessWidth'],
            tr['Setting']['MiniMaxProcessWidthDesc'], expert=True, icon=FluentIcon.FIT_PAGE, parent=card)
        m6 = CompactRangeRow(
            config.minimaxStripPadding, tr['Setting']['MiniMaxStripPadding'],
            tr['Setting']['MiniMaxStripPaddingDesc'], expert=True, icon=FluentIcon.ALBUM, parent=card)
        m7 = CompactSwitchRow(
            config.minimaxVaeDecodeFp32, tr['Setting']['MiniMaxVaeDecodeFp32'],
            tr['Setting']['MiniMaxVaeDecodeFp32Desc'], expert=True, icon=FluentIcon.PALETTE, parent=card)
        vbox.addWidget(CompactParamRow(m5, m6, m7, columns=4))
        self.rows_minimax = [m1, m2, m3]
        self.rows_minimax_expert = [m4, m5, m6, m7]

    def _build_sttn_group(self):
        """STTN：四列布局（1 行 × 4）"""
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
        vbox.addWidget(CompactParamRow(s1, s2, s3, columns=4))
        self.rows_sttn = [s1, s2, s3]

    def _build_general_group(self):
        """通用（页面最底部）：并入外观与关于。
        行1：界面语言 | 硬件加速 | 视频分段时长 | 启动检查更新
        行2：段间重叠帧数（单独一行，四列占位）
        行3：应用主题 | 保存目录 | 环境自检 | 关于·检查更新
        行4：项目链接
        """
        gui = tr['SubtitleExtractorGUI']
        st = tr['Setting']
        card, vbox, self._general_header = self._make_group(gui['GroupGeneral'])

        self.lang_row = CompactComboRow(
            config.interface, gui['InterfaceLanguage'], gui['InterfaceLanguageDesc'],
            texts=list(config.intefaceTexts.keys()),
            values=list(config.intefaceTexts.values()),
            on_user_changed=self._on_interface_changed,
            icon=FluentIcon.LANGUAGE, parent=card)
        self.hw_row = CompactSwitchRow(
            config.hardwareAcceleration, st['HardwareAcceleration'],
            st['HardwareAccelerationDesc'],
            icon=FluentIcon.SPEED_HIGH, parent=card)
        if not HARDWARD_ACCELERATION_OPTION:
            self.hw_row.switch.setEnabled(False)
            self.hw_row.switch.setChecked(False)
            self.hw_row.setToolTip(st['HardwareAccelerationNO'])
            config.set(config.hardwareAcceleration, False)
        self.segment_row = CompactRangeRow(
            config.segmentSeconds, gui['SegmentMinutes'],
            gui['SegmentMinutesDesc'], icon=FluentIcon.STOP_WATCH, parent=card)
        self.update_row = CompactSwitchRow(
            config.checkUpdateOnStartup, st['CheckUpdateOnStartup'],
            st['CheckUpdateOnStartupDesc'], icon=FluentIcon.UPDATE, parent=card)
        vbox.addWidget(CompactParamRow(self.lang_row, self.hw_row,
                                       self.segment_row, self.update_row, columns=4))

        # 段间重叠帧数：分段处理时为邻帧模型提供段边界上下文（0=关闭）
        self.segment_overlap_row = CompactRangeRow(
            config.segmentOverlapFrames, gui['SegmentOverlap'],
            gui['SegmentOverlapDesc'], icon=FluentIcon.LINK, parent=card)
        vbox.addWidget(CompactParamRow(self.segment_overlap_row, columns=4))

        # 四列网格：主题 | 环境自检 | 保存目录（跨两列，路径中段省略，悬停看全路径）
        self.theme_row = CompactComboRow(
            config.appTheme, st['AppTheme'], st['AppThemeDesc'],
            texts=[st['ThemeLight'], st['ThemeDark'], st['ThemeAuto']],
            values=['light', 'dark', 'auto'],
            icon=FluentIcon.BRUSH, parent=card)
        self.env_row = CompactActionRow(
            st['EnvCheck'], st['EnvCheckDesc'], icon=FluentIcon.SYNC, parent=card)
        self.env_status = self.env_row.add_status_label(width=72)
        self.env_row.add_button(st['EnvCheckBtn'], on_click=self.run_env_check)
        self.save_dir_row = CompactActionRow(
            st['SaveDirectory'], '', icon=FluentIcon.DOWNLOAD, parent=card)
        self.save_dir_row.add_path_label(width=330)
        self.save_dir_row.add_button(st['ChooseDirectory'], on_click=self.choose_save_directory)
        self._update_save_dir_display()
        vbox.addWidget(CompactParamRow(self.theme_row, self.env_row,
                                       (self.save_dir_row, 2), columns=4))

        # 关于并入：版权与版本直接作为行标题显示（原 tooltip 内容），按钮即检查更新
        self.copyright_row = CompactActionRow(
            st['CopyrightDesc'].format(VERSION), st['CopyrightDesc'].format(VERSION),
            icon=FluentIcon.MAIL, parent=card)
        self.copyright_row.add_button(st['CopyrightButton'], on_click=lambda: self.check_update())

        # 项目链接（仓库地址配置后才显示）
        self.project_row = None
        if PROJECT_HOME_URL:
            self.project_row = CompactActionRow(
                st['ProjectLinkTitle'], st['ProjectLinkDesc'],
                icon=FluentIcon.GITHUB, parent=card)
            self.project_row.add_button(st['ProjectLinkOpen'],
                                        on_click=lambda: QDesktopServices.openUrl(QUrl(PROJECT_HOME_URL)))
            vbox.addWidget(CompactParamRow(self.copyright_row, self.project_row, columns=2))
        else:
            vbox.addWidget(CompactParamRow(self.copyright_row, columns=2))

    def _update_save_dir_display(self):
        """保存目录行内显示当前文件夹；整行 tooltip 同步为实际路径"""
        path = config.saveDirectory.value or tr['Setting']['SaveDirectoryDefault']
        self.save_dir_row.set_path(path)
        self.save_dir_row.set_desc(path)

    # ---------- 恢复默认 ----------

    def _on_restore_defaults(self):
        moved_rows = [self.lang_row, self.detect_model_row,
                      self.residue_row, self.hw_row, self.segment_row,
                      self.segment_overlap_row,
                      self.update_row, self.theme_row]
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

    # ---------- 语言切换 ----------

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

    # ---------- 保存目录 / 环境自检 ----------

    def choose_save_directory(self):
        last_save_directory = "./" if not config.saveDirectory.value else config.saveDirectory.value
        folder = QFileDialog.getExistingDirectory(
            self, tr['Setting']['ChooseDirectory'], last_save_directory)
        if not folder:
            folder = ""
        config.set(config.saveDirectory, folder)
        self._update_save_dir_display()

    def _env_log(self, text):
        """把自检/下载日志写进主界面日志窗"""
        try:
            self.parent.homeInterface.append_output(text)
        except Exception:
            pass

    def run_env_check(self):
        """后台执行环境自检"""
        self.env_status.setText(tr["Setting"]["EnvCheckRunning"])
        TaskExecutor.runTask(lambda: check_all()).then(self.on_env_checked)

    def on_env_checked(self, payload):
        results, missing = payload
        lines = [f"{'✅' if r['ok'] else '❌'} {r['name']}: {r['detail']}" for r in results]
        report = '\n'.join(lines)
        for line in lines:
            self._env_log(line)
        self.env_status.setText(
            tr["Setting"]["EnvCheckPass"] if not missing
            else tr["Setting"]["EnvCheckMissing"].format(len(missing)))

        if missing:
            n_dl = sum(1 for m in missing if m.get('sources'))
            msg = report + f"\n\n{tr['Setting']['EnvCheckMissingSummary'].format(len(missing))}"
            if n_dl:
                msg += f"，{tr['Setting']['EnvCheckDownloadable'].format(n_dl)}"
            msg += f"\n{tr['Setting']['EnvCheckAskDownload']}"
            w = MessageBox(tr["Setting"]["EnvCheck"], msg, self.window())
            if w.exec():
                self.env_status.setText(tr["Setting"]["EnvCheckDownloading"])
                TaskExecutor.runTask(lambda: fix_weights(missing, log=self._env_log)).then(self.on_weights_fixed)
        else:
            InfoBar.success(tr["Setting"]["EnvCheck"], tr["Setting"]["EnvCheckAllGood"],
                            duration=2500, parent=self.window())

    def on_weights_fixed(self, payload):
        fixed, manual = payload
        if manual:
            self._env_log(f"⚠ {tr['Setting']['EnvCheckNoSource'].format(len(manual), ', '.join(manual))}")
        self.env_status.setText(
            tr["Setting"]["EnvCheckDownloadDone"].format(len(fixed)) if fixed
            else tr["Setting"]["EnvCheckManualHint"])
        InfoBar.success(tr["Setting"]["EnvCheck"],
                        tr["Setting"]["EnvCheckRepaired"].format(len(fixed)),
                        duration=2500, parent=self.window())

    # ---------- 检查更新（自「关于」页并入） ----------

    def show_message_box(self, title, content, showYesButton=False, yesSlot=None):
        w = MessageBox(title, content, self.window())
        if not showYesButton:
            w.cancelButton.setText(tr['Setting']['DialogCancel'])
            w.yesButton.hide()
            w.buttonLayout.insertStretch(0, 1)
        if w.exec() and yesSlot is not None:
            yesSlot()

    def check_update(self, ignore=False):
        if not PROJECT_UPDATE_URLS:
            if not ignore:
                InfoBar.warning(
                    title=tr["Setting"]["NoUpdatesAvailableTitle"],
                    content=tr["Setting"]["NoUpdatesAvailableDesc"],
                    parent=self.window(), duration=3000)
            return
        TaskExecutor.runTask(self.version_manager.has_new_version).then(
            lambda success: self.on_version_info_fetched(success, ignore))

    def on_version_info_fetched(self, success, ignore=False):
        if success:
            # 优先用更新清单里的国内直链（魔搭 version.json）；GitHub API 端点无直链，回退 Releases 页
            dl = self.version_manager.get_download_url()
            target = dl or PROJECT_RELEASES_URL
            self.show_message_box(
                tr["Setting"]["UpdatesAvailableTitle"],
                tr["Setting"]["UpdatesAvailableDesc"].format(self.version_manager.lastest_version),
                True,
                lambda: QDesktopServices.openUrl(QUrl(target))
            )
        elif not ignore:
            self.show_message_box(
                tr["Setting"]["NoUpdatesAvailableTitle"],
                tr["Setting"]["NoUpdatesAvailableDesc"],
            )
