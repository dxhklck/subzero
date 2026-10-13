import os
import sys

from PySide6 import QtWidgets
from qfluentwidgets import (FluentWindow, PushButton, Slider, ProgressBar, PlainTextEdit,
                          setTheme, Theme, FluentIcon, CardWidget, SettingCardGroup,
                          ComboBoxSettingCard, SwitchSettingCard, RangeSettingCard,
                          PushSettingCard, PrimaryPushSettingCard, OptionsSettingCard,
                          FolderListSettingCard, HyperlinkCard, ColorSettingCard, 
                          CustomColorSettingCard, MessageBox)
from backend.config import config, tr, HARDWARD_ACCELERATION_OPTION, BASE_DIR
from backend.tools.constant import InpaintMode, SubtitleDetectMode

class SettingInterface(QtWidgets.QVBoxLayout):

    def __init__(self, parent):
        super().__init__()
        self.setContentsMargins(16, 16, 16, 16)
        self._parent = parent
        # 语言切换需重启生效：界面文案在启动时一次性加载并固化到各控件，运行中热切换
        # 需要重建全部界面，成本高收益低，故用「弹窗确认 + 一键重启」方案
        self._interface_at_init = config.interface.value

        # 界面语言设置
        self.interface_combo = ComboBoxSettingCard(
            configItem=config.interface,
            icon=FluentIcon.LANGUAGE,
            title=tr["SubtitleExtractorGUI"]["InterfaceLanguage"],
            content=tr["SubtitleExtractorGUI"]["InterfaceLanguageDesc"],
            parent=parent,
            texts=config.intefaceTexts.keys(),
        )
        self.addWidget(self.interface_combo)
        self.interface_combo.configItem.valueChanged.connect(self._on_interface_changed)

        # 处理模式设置
        self.inpaint_mode_combo = ComboBoxSettingCard(
            configItem=config.inpaintMode,
            icon=FluentIcon.GLOBE,
            title=tr["SubtitleExtractorGUI"]["InpaintMode"],
            content="",
            parent=parent,
            texts=[list(tr['InpaintMode'].values())[i] for i,_ in enumerate(config.inpaintMode.validator.options)],
        )
        self.inpaint_mode_combo.setToolTip(tr["SubtitleExtractorGUI"]["InpaintModeDesc"])
        self.addWidget(self.inpaint_mode_combo)

        self.subtitle_detect_model_combo = ComboBoxSettingCard(
            configItem=config.subtitleDetectMode,
            icon=FluentIcon.SEARCH,
            title=tr["SubtitleExtractorGUI"]["SubtitleDetectMode"],
            content="",
            parent=parent,
            texts=[list(tr['SubtitleDetectMode'].values())[i] for i,_ in enumerate(config.subtitleDetectMode.validator.options)],
        )
        self.addWidget(self.subtitle_detect_model_combo)

        # 是否启用硬件加速
        self.hardware_acceleration = SwitchSettingCard(
            configItem=config.hardwareAcceleration,
            icon=FluentIcon.SPEED_HIGH, 
            title=tr["Setting"]["HardwareAcceleration"],
            content=tr["Setting"]["HardwareAccelerationDesc"],
            parent=parent
        )
        self.addWidget(self.hardware_acceleration)
        # 如果硬件加速选项被禁用, 设置硬件加速为False并只读
        if not HARDWARD_ACCELERATION_OPTION:
            self.hardware_acceleration.switchButton.setChecked(False)
            self.hardware_acceleration.switchButton.setEnabled(False)
            self.hardware_acceleration.setContent(tr["Setting"]["HardwareAccelerationNO"])
            config.set(config.hardwareAcceleration, False)
        # 添加一些空间
        self.addStretch(1)
    
    def set_inpaint_mode_enabled(self, enabled):
        """启用或禁用 inpaint 模式下拉框"""
        self.inpaint_mode_combo.comboBox.setEnabled(enabled)

    def _on_interface_changed(self):
        """语言切换：弹窗确认并一键重启（配置已写盘，重启后按新语言加载界面）"""
        if config.interface.value == self._interface_at_init:
            return
        box = MessageBox(
            tr["SubtitleExtractorGUI"]["RestartRequiredTitle"],
            tr["SubtitleExtractorGUI"]["RestartRequiredContent"],
            self._parent.window())
        box.yesButton.setText(tr["SubtitleExtractorGUI"]["RestartNow"])
        box.cancelButton.setText(tr["SubtitleExtractorGUI"]["RestartLater"])
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
        import subprocess
        subprocess.Popen([exe, os.path.join(root_dir, 'gui.py')], cwd=root_dir)
        QtWidgets.QApplication.quit()

    def reset_setting(self):
        """重置所有设置为默认值"""
        # 这里需要实现重置逻辑
        pass