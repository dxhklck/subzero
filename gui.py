# -*- coding: utf-8 -*-
"""
@Author  : 练崇坤
@Time    : 2023/4/1 6:07 下午（原始时间）
@FileName: gui.py
@desc: 字幕去除器图形化界面（由 PySimpleGUI 改写为 PySide6）
"""

# ── 首启自举钩子：必须在 cv2/torch 等重导入之前 ──────────────────────
# 注意：不能用 sys.path.insert(0, 'backend/tools') 再 import first_run——
# backend/tools 下有 concurrent 包，插到最前会遮蔽标准库 concurrent.futures！
# 纯源码发布不含 first_run.py（无自动安装逻辑），文件不存在时静默跳过。
import os as _os
import sys as _sys
import importlib.util as _ilu

_first_run_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                'backend', 'tools', 'first_run.py')
if _os.path.isfile(_first_run_path):
    _spec = _ilu.spec_from_file_location('_first_run_hook', _first_run_path)
    _mod = _ilu.module_from_spec(_spec) if _spec else None
    if _mod is not None:
        _spec.loader.exec_module(_mod)
        _mod.ensure_environment()  # 环境齐备→返回；不齐备→自举后 execv 重启（不返回）
del _os, _sys, _ilu
# ─────────────────────────────────────────────────────────────────────

import sys
import os
import configparser
import cv2
import multiprocessing
from PySide6.QtCore import Qt, QTranslator
from PySide6 import QtCore, QtWidgets, QtGui
from PySide6.QtWidgets import QApplication, QFrame, QStackedWidget, QHBoxLayout, QLabel
from qfluentwidgets import (FluentWindow, PushButton, Slider, ProgressBar, PlainTextEdit,
                          setTheme, Theme, FluentIcon, CardWidget, SettingCardGroup,
                          ComboBoxSettingCard, SwitchSettingCard, setThemeColor, OptionsConfigItem,
                          OptionsValidator, SubtitleLabel, HollowHandleStyle, qconfig, ConfigItem, QConfig,
                          NavigationWidget, NavigationItemPosition, isDarkTheme, InfoBar)

# pythonw（无控制台）下 stdout/stderr 为 None：接管到日志文件（可排查启动期错误），
# 文件尾部会在主界面「监控」标签回显；打开失败再退回 devnull
if sys.stdout is None or sys.stderr is None:
    try:
        _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'logs')
        os.makedirs(_log_dir, exist_ok=True)
        _log_file = open(os.path.join(_log_dir, 'gui-main.log'), 'a', buffering=1,
                         encoding='utf-8', errors='replace')
        if sys.stdout is None:
            sys.stdout = _log_file
        if sys.stderr is None:
            sys.stderr = _log_file
    except Exception:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')
        if sys.stderr is None:
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')

from qframelesswindow.utils import getSystemAccentColor
from backend.config import config, tr, VERSION
from backend.tools.theme_listener import SystemThemeListener
from backend.tools.process_manager import ProcessManager
from ui.advanced_setting_interface import AdvancedSettingInterface
from ui.home_interface import HomeInterface


class SubtitleExtractorGUI(FluentWindow): 
    def __init__(self):
        super().__init__()
        # 禁用云母效果
        self.setMicaEffectEnabled(False)
        # 主色固定青绿；明暗主题由设置页选择（默认跟随系统）
        setThemeColor("#1D9E75", save=False)
        self._apply_theme()
        config.appTheme.valueChanged.connect(self._apply_theme)
        # 最小窗口尺寸，避免内容被挤压不可用
        self.setMinimumSize(1400, 760)  # 预览960+右栏422+间距：低于此宽度两栏会被压扁

        # 初始化系统主题监听器并连接信号
        # self.themeListener = SystemThemeListener(self)
        # self.themeListener.start()
 
        # 设置窗口图标
        self.setWindowIcon(QtGui.QIcon("design/subzero.ico"))
        self.setWindowTitle(tr['SubtitleExtractorGUI']['Title'] + " v" + VERSION)
        # 创建界面布局
        self._create_layout()
        self._connectSignalToSlot()
        self._lazy_check_update()

    def _apply_theme(self, value=None):
        """应用明暗主题：light / dark / auto（跟随系统）"""
        v = value if value is not None else config.appTheme.value
        if v == 'dark':
            setTheme(Theme.DARK, save=False)
        elif v == 'light':
            setTheme(Theme.LIGHT, save=False)
        else:
            setTheme(Theme.AUTO, save=False)

    def _lazy_check_update(self):
        """ 延迟检查更新 """
        if not config.checkUpdateOnStartup.value:
            return
        self.check_update_timer = QtCore.QTimer(self)
        self.check_update_timer.setSingleShot(True)
        self.check_update_timer.timeout.connect(lambda: self.settingsInterface.check_update(ignore=True))
        self.check_update_timer.start(2000)

    def _connectSignalToSlot(self):
        config.appRestartSig.connect(self._showRestartTooltip)

    def _showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            'Updated successfully',
            'Configuration takes effect after restart',
            duration=5000,
            parent=self
        )

    def _create_layout(self):
        # 创建主页面和设置页面（关于内容已并入设置页「通用」卡）
        self.homeInterface = HomeInterface(self)
        self.homeInterface.setObjectName("HomeInterface")
        self.settingsInterface = AdvancedSettingInterface(self)
        self.settingsInterface.setObjectName("SettingsInterface")

        # 两个页面：首页 / 设置
        self.addSubInterface(self.homeInterface, FluentIcon.HOME, "首页")
        self.addSubInterface(self.settingsInterface, FluentIcon.SETTING, "设置")

    def on_navigation_item_changed(self, key):
        """导航项变更时的处理函数"""
        if key == 'main':
            self.stackWidget.setCurrentIndex(0)
        elif key == 'advanced':
            self.stackWidget.setCurrentIndex(1)

    def closeEvent(self, event):
        """程序关闭时保存窗口位置并清理资源"""
        self.save_window_position()
        ProcessManager.instance().terminate_all()
        super().closeEvent(event)

    def _onThemeChangedFinished(self):
        super()._onThemeChangedFinished()

    def save_window_position(self):
        """保存窗口位置到配置文件"""
        # 保存窗口位置和大小
        config.set(config.windowX, self.x())
        config.set(config.windowY, self.y())
        config.set(config.windowW, self.width())
        config.set(config.windowH, self.height())

    def update_progress(self):
        # 定时器轮询更新进度（现在更新到视频滑块上）
        if self.se is not None:
            try:
                pos = min(self.frame_count - 1, int(self.se.progress_total / 100 * self.frame_count))
                if pos != self.video_slider.value():
                    self.video_slider.setValue(pos)
                # 检查是否完成
                if self.se.isFinished:
                    self.processing_finished()
            except Exception as e:
                # 捕获任何异常，防止崩溃
                print(f"更新进度时出错: {str(e)}")

    def load_window_position(self):
        # 尝试读取窗口位置
        try:
            x = config.windowX.value
            y = config.windowY.value
            width = config.windowW.value
            height = config.windowH.value

            # 尺寸超出屏幕则收进屏幕（不低于最小尺寸），确保预览不缩水
            avail = QtWidgets.QApplication.primaryScreen().availableGeometry()
            width = max(self.minimumWidth(), min(width, avail.width()))
            height = max(self.minimumHeight(), min(height, avail.height()))

            if x and y and avail.contains(QtCore.QRect(x, y, width, height)):
                self.setGeometry(x, y, width, height)
            else:
                # 位置非法：按配置尺寸居中（旧版此处不调尺寸，导致窗口/预览缩水）
                self.resize(width, height)
                self.center_window()
        except Exception as e:
            print(e)
            self.center_window()
    
    def center_window(self):
        """将窗口居中显示"""
        screen_rect = QtWidgets.QApplication.primaryScreen().availableGeometry()
        window_rect = self.frameGeometry()
        center_point = screen_rect.center()
        window_rect.moveCenter(center_point)
        self.move(window_rect.topLeft())

    def keyPressEvent(self, event):
        """处理键盘事件"""
        # 检测Ctrl+C组合键
        if event.key() == QtCore.Qt.Key_C and event.modifiers() == QtCore.Qt.ControlModifier:
            print("\n程序被用户中断(Ctrl+C)，正在退出...")
            self.close()
        else:
            super().keyPressEvent(event)


if __name__ == '__main__':
    multiprocessing.set_start_method("spawn")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
    # 权重完整性启动扫描（首启自举钩子之后、主窗口构建之前；不阻塞、不弹模态框）
    from backend.tools.model_scanner import scan_weights, format_report
    _scan_ok, _scan_missing, _scan_mismatch = scan_weights()
    window = SubtitleExtractorGUI()
    # 先设置透明, 再显示, 否则会有闪烁的效果
    window.setWindowOpacity(0.0)
    window.show()
    window.load_window_position()
    # 使用动画效果逐渐显示窗口
    animation = QtCore.QPropertyAnimation(window, b"windowOpacity")
    animation.setDuration(300)  # 300毫秒的动画
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.start()
    # 权重异常提示：日志窗逐行警告 + InfoBar（duration 8000，不阻塞启动）
    if _scan_missing or _scan_mismatch:
        for _line in format_report(_scan_ok, _scan_missing, _scan_mismatch).splitlines():
            window.homeInterface.append_output(_line)
        InfoBar.warning(
            '权重完整性',
            f'发现 {len(_scan_missing) + len(_scan_mismatch)} 项权重异常，详见日志；'
            '请按 使用教程 手动下载补齐',
            duration=8000,
            parent=window
        )
    app.exec()