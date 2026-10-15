import os
import shutil
import subprocess
import sys
import cv2
import time
import torch
import threading
import multiprocessing
import traceback
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QTabWidget,
                               QPlainTextEdit, QMenu, QFrame)
from PySide6.QtCore import Slot, QRect, Signal, Qt, QTimer
from PySide6.QtGui import QTextCursor, QAction
from PySide6 import QtWidgets
from datetime import datetime
from qfluentwidgets import (PushButton, PrimaryPushButton, CardWidget, TextEdit, FluentIcon,
                            InfoBar, ComboBox, StrongBodyLabel)
from ui.component.video_display_component import VideoDisplayComponent
from ui.component.task_list_component import (TaskListComponent, TaskStatus, TaskOptions,
                                              resolve_output_path)
from ui.component.gpu_monitor_bar import GpuMonitorBar
from ui.component.compact_rows import CompactComboRow, set_row_tooltip, _make_icon
from ui.icon.my_fluent_icon import MyFluentIcon
from backend.config import config, tr, VERSION
from backend.tools.constant import InpaintMode
from backend.tools.subtitle_remover_remote_call import SubtitleRemoverRemoteCall
from backend.tools.process_manager import ProcessManager
from backend.tools.common_tools import get_readable_path, is_image_file, read_image, SUBPROCESS_CREATION_FLAGS


def write_crash_report(video_path, output_path, tb_text):
    """子进程崩溃自报告：环境信息 + gui-main.log 尾部 + traceback 落盘。

    写到输出文件同目录：崩溃报告_<视频名>_<时间戳>.txt。任何单项采集失败
    都不阻断（报告本身降级也要落盘）。返回报告路径。
    """
    import platform
    lines = []
    sep = '=' * 60
    lines.append(sep)
    lines.append(f'净影 SubZero 崩溃报告 v{VERSION}')
    lines.append(f'时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(sep)

    lines.append('\n[输入输出]')
    lines.append(f'输入视频: {video_path}')
    lines.append(f'输出文件: {output_path}')

    lines.append('\n[运行环境]')
    try:
        lines.append(f'操作系统: {platform.platform()}')
    except Exception:
        pass
    try:
        lines.append(f'Python: {platform.python_version()} ({sys.executable})')
    except Exception:
        pass

    lines.append('\n[GPU / 加速]')
    try:
        if torch.cuda.is_available():
            props = torch.device_properties(0) if hasattr(torch, 'device_properties') else None
            name = torch.cuda.get_device_name(0)
            lines.append(f'torch: {torch.__version__} · CUDA 可用')
            lines.append(f'显卡: {name}')
            if props is not None:
                lines.append(f'显存: {props.total_memory / 1024 ** 3:.1f} GB')
            lines.append(f'CUDA(torch): {torch.version.cuda}')
        else:
            lines.append(f'torch: {torch.__version__} · CUDA 不可用（CPU 模式）')
    except Exception as e:
        lines.append(f'torch 信息采集失败: {e}')
    # paddle 仅在已导入时读取（避免为采信息触发 CUDA 初始化，破坏崩溃现场）
    paddle = sys.modules.get('paddle')
    if paddle is not None:
        try:
            lines.append(f'paddle: {paddle.__version__} · '
                         f'CUDA编译: {paddle.is_compiled_with_cuda()}')
        except Exception:
            pass

    lines.append('\n[关键配置]')
    for key in ('inpaintMode', 'subtitleDetectMode', 'residueCheck',
                'segmentSeconds', 'segmentOverlapFrames',
                'pipelineChunkFrames', 'pipelinePrefetchChunks'):
        try:
            lines.append(f'{key}: {getattr(config, key).value}')
        except Exception:
            pass

    lines.append('\n[主进程日志尾部 (config/logs/gui-main.log)]')
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(root, 'config', 'logs', 'gui-main.log')
        if os.path.isfile(log_path):
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 16384))
                tail = f.read().splitlines()
            lines.extend(tail[-80:])
        else:
            lines.append('（日志文件不存在）')
    except Exception as e:
        lines.append(f'日志读取失败: {e}')

    lines.append('\n[Traceback]')
    lines.append(tb_text.rstrip())
    lines.append(sep)

    stem = Path(video_path).stem
    save_dir = os.path.dirname(output_path) or '.'
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.abspath(
        os.path.join(save_dir, f'崩溃报告_{stem}_{stamp}.txt'))
    os.makedirs(save_dir, exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return report_path


class _SegmentResult:
    """分段任务的返回 shim：对齐子进程的 exitcode 判断语义。"""

    def __init__(self, exitcode: int):
        self.exitcode = exitcode

class PresetComboRow(QFrame):
    """参数预设行（#5）：动画/真人/电影一键应用，手动微调任一覆盖项 → 自定义。

    应用即逐项 config.set（写盘 config.json），随后刷新主页模型行与设置页
    各行的显示；预设值存 config/presets.json（用户可编辑，重启生效）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._applying = False  # 应用期间的覆盖项变更不算「手动微调」
        self.on_applied = None  # 预设应用后回调（宿主注入：刷新模型行/设置页）
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet('PresetComboRow { background: transparent; }')

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(10)

        icon_widget = _make_icon(FluentIcon.BRUSH, self)
        if icon_widget is not None:
            layout.addWidget(icon_widget, 0, Qt.AlignVCenter)

        title = StrongBodyLabel(tr['ParameterPreset']['Title'], self)
        title.setStyleSheet('font-size: 13px;')
        layout.addWidget(title, 1)

        self.combo = ComboBox(self)
        self._ids = ['animation', 'real', 'film', 'custom']
        self._texts = [tr['ParameterPreset']['Animation'],
                       tr['ParameterPreset']['Real'],
                       tr['ParameterPreset']['Film'],
                       tr['ParameterPreset']['Custom']]
        self.combo.addItems(self._texts)
        self.combo.setFixedWidth(150)
        self.combo.currentIndexChanged.connect(self._on_index)
        layout.addWidget(self.combo, 0)
        set_row_tooltip(tr['ParameterPreset']['Desc'], self, title, self.combo)

        # 覆盖项被手动改动（设置页/模型行）→ 预设失效显示自定义
        from backend.tools.presets import COVERED_ITEMS
        for item in COVERED_ITEMS:
            item.valueChanged.connect(self._on_covered_changed)

        self.refresh_preset()

    def refresh_preset(self):
        """按当前配置实际值匹配预设（启动/应用后调用，不触发应用逻辑）"""
        from backend.tools.presets import load_presets, match_current
        pid = match_current(load_presets())
        if pid in self._ids:
            self.combo.setCurrentIndex(self._ids.index(pid))

    def _on_index(self, idx):
        if not (0 <= idx < len(self._ids)) or self._applying:
            return
        pid = self._ids[idx]
        if pid == 'custom':
            # 用户手动选「自定义」：仅记录，不改参数
            config.set(config.parameterPreset, 'custom')
            return
        from backend.tools.presets import apply_preset, load_presets
        self._applying = True
        try:
            apply_preset(pid, load_presets())
            config.set(config.parameterPreset, pid)
        finally:
            self._applying = False
        if self.on_applied is not None:
            self.on_applied()
        InfoBar.success(
            title=tr['ParameterPreset']['Title'],
            content=tr['ParameterPreset']['Applied'].format(self._texts[idx]),
            parent=self.window(), duration=4000)

    def _on_covered_changed(self, *_):
        """任一覆盖项被手动改动 → 预设失效为自定义（应用期间的变更除外）"""
        if self._applying:
            return
        if config.parameterPreset.value != 'custom':
            config.set(config.parameterPreset, 'custom')
        if self.combo.currentIndex() != self._ids.index('custom'):
            self.combo.setCurrentIndex(self._ids.index('custom'))


class HomeInterface(QWidget):
    progress_signal = Signal(int, bool)
    append_log_signal = Signal(list)
    console_signal = Signal(object)
    update_preview_with_comp_signal = Signal(list)
    task_error_signal = Signal(object)
    toggle_buttons_signal = Signal(bool)  # True=显示运行按钮, False=显示停止按钮
    task_status_signal = Signal(int, object)  # (task_index, TaskStatus)
    select_task_signal = Signal(int)  # task_index
    task_output_path_signal = Signal(int, str)  # (task_index, output_path)
    task_progress_signal = Signal(int, int)  # (task_index, progress)——worker 线程更新任务卡片用
    task_option_signal = Signal(int, object, object)  # (task_index, TaskOptions, value)
    clear_console_signal = Signal(str)  # 新任务开始清空监控（参数=任务名，worker 线程经信号触发）
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("HomeInterface")
        # 初始化一些变量
        self.video_path = None
        self.video_cap = None
        self.fps = None
        self.frame_count = None
        self.frame_width = None
        self.frame_height = None
        self.se = None  # 后台字幕提取器

        # 字幕区域参数
        self.xmin = None
        self.xmax = None
        self.ymin = None
        self.ymax = None

        # 添加自动滚动控制标志

        self._stop_event = threading.Event()  # 线程安全的停止信号
        self._worker_thread = None
        self.running_process = None
        self._saved_inpaint_mode = None  # 保存图片锁定前的 inpaint 模式
        self._video_cap_lock = threading.Lock()  # 保护 video_cap 的线程锁

        # A/B 同步对比：B 框结果视频源（当前任务的已完成输出）+ 播放定时器
        self.b_video_cap = None
        self._compare_playing = False
        self._compare_timer = QTimer(self)
        self._compare_timer.timeout.connect(self._compare_tick)

        # 当前正在处理的任务索引
        self.current_processing_task_index = -1
        # 处理速度 EMA 计算用
        self._speed_last = None  # (time, progress_total)
        self._speed_ema = 0.0
        # 监控标签终端渲染：上一行是否为进度条重绘行（\x01 前缀），用于原地替换
        self._last_console_is_bar = False

        self.__init_widgets()
        self.progress_signal.connect(self.update_progress)
        self.append_log_signal.connect(self.append_log)
        self.console_signal.connect(self.append_console)
        self.clear_console_signal.connect(self._on_new_task_console)
        self.update_preview_with_comp_signal.connect(self.update_preview_with_comp)
        self.task_error_signal.connect(self.on_task_error)
        self.toggle_buttons_signal.connect(self._toggle_buttons)
        # 启动环境横幅：绘世式开机信息。监控即终端——每次启动都是全新会话，
        # 不回显上次会话的 gui-main.log 尾部（旧数据残留）
        self._emit_startup_banner()
        self.task_progress_signal.connect(self.task_list_component.update_task_progress)
        self.task_option_signal.connect(self.task_list_component.update_task_option)
        self.task_status_signal.connect(lambda idx, status: self.task_list_component.update_task_status(idx, status))
        self.task_output_path_signal.connect(self.on_task_output_path)
        self.select_task_signal.connect(self.task_list_component.select_task)

        # 任务队列改手动快照管理（v1.x 自动恢复机制已移除）：
        # 快照加载过程日志 → 监控输出
        self.task_list_component.snapshot_log.connect(self.append_output)

    def __init_widgets(self):
        """创建主页面"""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        main_layout = QHBoxLayout()
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(16, 16, 16, 8)

        # 左侧视频区域
        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)
        
        # 创建视频显示组件
        self.video_display_component = VideoDisplayComponent(self)
        # 竖屏预览高度预算：本窗口内容高 − 输出卡最小高(200) − 主布局边距(24)
        # − 组件内滑块容器(38) − 布局间距(~16)，随窗口缩放实时取值，
        # 使竖屏/4:3 视频的预览高度贴合实际可用空间（不再写死 640 上限）
        self.video_display_component.preview_height_budget = (
            lambda: self.height() - 278)
        self.video_display_component.ab_sections_changed.connect(self.ab_sections_changed)
        self.video_display_component.selections_changed.connect(self.selections_changed)
        self.video_display_component.size_changed.connect(self._on_preview_resized)
        self._last_frame = None
        left_layout.addWidget(self.video_display_component)
        
        # 获取视频显示和滑块的引用
        self.video_display = self.video_display_component.video_display
        self.video_slider = self.video_display_component.video_slider
        self.video_slider.valueChanged.connect(self.slider_changed)
        # A/B 同步播放按钮
        self.video_display_component.play_button.clicked.connect(self._toggle_compare_play)
        
        # 输出文本区域
        self.output_text = TextEdit()
        self.output_text.setMinimumHeight(200)
        self.output_text.setReadOnly(True)
        self.output_text.document().setDocumentMargin(10)        
        # 连接滚动条值变化信号
        self.output_text.verticalScrollBar().valueChanged.connect(self.on_scroll_change)
        
        output_container = CardWidget(self)
        output_layout = QVBoxLayout()
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(0)
        # 双标签：日志（格式化输出）/ 监控（原控制台原始输出，含 tqdm 进度）
        self.log_tabs = QTabWidget(output_container)
        self.log_tabs.setDocumentMode(True)
        self.log_tabs.addTab(self.output_text, tr["Setting"]["LogTab"])

        self.console_text = QPlainTextEdit()
        self.console_text.setReadOnly(True)
        self.console_text.setMinimumHeight(200)
        self.console_text.setMaximumBlockCount(5000)
        self.console_text.setStyleSheet(
            "QPlainTextEdit { background:#101010; color:#C8C8C8;"
            "font-family:'Consolas'; font-size:11px; border:none; }")
        self.log_tabs.addTab(self.console_text, tr["Setting"]["MonitorTab"])
        # 监控右键菜单：复制/导出/清空（终端等价操作）
        self.console_text.setContextMenuPolicy(Qt.CustomContextMenu)
        self.console_text.customContextMenuRequested.connect(self._show_console_menu)
        # 默认展示「监控」：黑色终端面板即主控制台（对齐绘世/秋叶启动器的形态，
        # 环境横幅 + 处理过程的全部原始输出都在这里流过）
        self.log_tabs.setCurrentWidget(self.console_text)
        output_layout.addWidget(self.log_tabs)
        output_container.setLayout(output_layout)
        left_layout.addWidget(output_container)

        main_layout.addLayout(left_layout, 2)

        # 右侧设置区域
        right_layout = QVBoxLayout()
        right_layout.setSpacing(10)

        # 系统状态卡：显存/内存监控 + 处理模型，同一张卡内连续三行（无分隔线）
        status_card = CardWidget(self)
        status_lay = QVBoxLayout(status_card)
        status_lay.setContentsMargins(0, 0, 0, 0)
        status_lay.setSpacing(0)
        self.gpu_monitor_bar = GpuMonitorBar(self)
        status_lay.addWidget(self.gpu_monitor_bar)
        status_lay.addSpacing(2)
        gui_tr = tr['SubtitleExtractorGUI']
        self.model_row = CompactComboRow(
            config.inpaintMode, gui_tr['InpaintMode'], gui_tr['InpaintModeDesc'],
            texts=[list(tr['InpaintMode'].values())[i]
                   for i, _ in enumerate(config.inpaintMode.validator.options)],
            values=list(config.inpaintMode.validator.options),
            icon=FluentIcon.ROBOT, parent=status_card)
        status_lay.addWidget(self.model_row)
        # 参数预设行：动画/真人/电影一键应用（跟随模型行同卡展示）
        self.preset_row = PresetComboRow(status_card)
        self.preset_row.on_applied = self._refresh_after_preset
        status_lay.addWidget(self.preset_row)
        right_layout.addWidget(status_card)

        # 添加任务列表容器
        task_list_container = CardWidget(self)
        task_list_layout = QHBoxLayout()
        task_list_layout.setContentsMargins(0, 0, 0, 0)
        task_list_layout.setSpacing(0)
        self.task_list_component = TaskListComponent(self)
        self.task_list_component.task_selected.connect(self.on_task_selected)
        self.task_list_component.task_deleted.connect(self.on_task_deleted)
        task_list_layout.addWidget(self.task_list_component)
        task_list_container.setLayout(task_list_layout)
        right_layout.addWidget(task_list_container, 1)  # 占满剩余空间
        
        # 操作按钮容器
        button_container = CardWidget(self)
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(16, 16, 16, 16)
        button_layout.setSpacing(8)
        
        self.file_button = PushButton(tr['SubtitleExtractorGUI']['Open'], self)
        self.file_button.setIcon(FluentIcon.FOLDER)
        self.file_button.clicked.connect(self.open_file)
        button_layout.addWidget(self.file_button)

        self.run_button = PrimaryPushButton(tr['SubtitleExtractorGUI']['Run'], self)
        self.run_button.setIcon(FluentIcon.PLAY)
        self.run_button.clicked.connect(self.run_button_clicked)
        button_layout.addWidget(self.run_button)

        self.stop_button = PrimaryPushButton(tr['SubtitleExtractorGUI']['Stop'], self)
        self.stop_button.setIcon(MyFluentIcon.Stop)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.stop_button_clicked)
        
        button_layout.addWidget(self.stop_button)
        
        button_container.setLayout(button_layout)
        right_layout.addWidget(button_container)

        main_layout.addLayout(right_layout, 1)

        outer_layout.addLayout(main_layout, 1)

    def _refresh_after_preset(self):
        """预设应用后同步主页模型行与设置页各行的显示（Switch/Combo 行不监听 valueChanged）"""
        self.model_row.refresh_row()
        try:
            from ui.advanced_setting_interface import AdvancedSettingInterface
            settings = self.window().findChild(AdvancedSettingInterface)
            if settings is not None:
                settings.refresh_config_rows()
        except Exception:
            pass  # 设置页尚未构建时跳过（构建时会从 config 读当前值）

    def _emit_startup_banner(self):
        """启动环境横幅 → 监控标签。

        对齐绘世/秋叶启动器的开机信息：窗口一打开就能确认版本、GPU 与
        加速状态，处理过程的原始输出随后在同一边滚动。只读 torch 的
        已初始化状态，不主动 import paddle（避免拖慢启动）。"""
        lines = ['── 净影 · 主控制台 ──']
        try:
            lines.append(f'版本 v{VERSION} · Python {sys.version.split()[0]}')
        except Exception:
            pass
        try:
            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                lines.append(f'GPU：{props.name}（{props.total_memory / 1024 ** 3:.0f}GB）· CUDA 加速可用')
            else:
                lines.append('GPU：未检测到 CUDA 设备，将回退 CPU 推理（速度显著下降）')
        except Exception:
            lines.append('GPU：检测失败')
        lines.append('检测 / 修复过程的全部原始输出（含进度条与路由决策）将在本页滚动；「日志」标签为格式化摘要')
        self.append_console(lines)

    def append_console(self, lines):
        """原控制台输出 → 监控标签（分发器传单条 str，兼容 list）。

        终端语义渲染：带 \\x01 前缀的是 tqdm 重绘行（worker 端 \\r 边界标记）。
        上一行是重绘行时，无论本条是重绘还是整行（tqdm 收尾 100%\\n、
        tqdm.write 消息），终端里都覆盖在同一物理行上 → 原地替换而非追加。
        """
        if isinstance(lines, str):
            lines = [lines]
        for line in lines:
            is_bar = line.startswith('\x01')
            text = line[1:] if is_bar else line
            if self._last_console_is_bar:
                # 上一行是进度条重绘（同一物理行）→ 原地替换（终端 \r 语义）。
                # 选择必须锚定块内文本（End → StartOfBlock）：BlockUnderCursor
                # 会连带前面的段落分隔符一起选中，每次替换都吞并上一行，
                # 连续几次重绘就把监控里其他数据全部吃掉
                cursor = QTextCursor(self.console_text.document())
                cursor.movePosition(QTextCursor.End)
                cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
                cursor.insertText(text)
            else:
                self.console_text.appendPlainText(text)
            self._last_console_is_bar = is_bar
        sb = self.console_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_scroll_change(self, value):
        """监控滚动条位置变化（日志自动滚动常开，无需额外处理）"""
        pass

    # ------------------------------------------------------------------ #
    # 监控即终端：新任务清空 + 右键复制/导出/清空
    # ------------------------------------------------------------------ #

    @Slot(str)
    def _on_new_task_console(self, task_name):
        """新任务开始（worker 经信号触发，不碰 Qt 控件）：清空监控打分隔行。

        监控即终端——每个任务是全新会话，上一任务的进度条/检测输出不再残留；
        启动横幅只在启动时打一次，这里补一行任务分隔保持上下文可读。
        """
        self.console_text.clear()
        self._last_console_is_bar = False
        self.append_console(tr['Setting']['ConsoleNewTaskSep'].format(task_name))

    def _show_console_menu(self, pos):
        """监控右键菜单：复制选中 / 复制全部 / 导出日志 / 清空"""
        menu = QMenu(self)
        has_selection = self.console_text.textCursor().hasSelection()

        copy_sel = QAction(tr['Setting']['ConsoleCopySel'], self)
        copy_sel.setEnabled(has_selection)
        copy_sel.triggered.connect(self.console_text.copy)
        menu.addAction(copy_sel)

        copy_all = QAction(tr['Setting']['ConsoleCopyAll'], self)
        copy_all.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(
            self.console_text.toPlainText()))
        menu.addAction(copy_all)

        export = QAction(tr['Setting']['ConsoleExport'], self)
        export.triggered.connect(self._export_console)
        menu.addAction(export)

        menu.addSeparator()

        clear = QAction(tr['Setting']['ConsoleClear'], self)
        clear.triggered.connect(self._clear_console)
        menu.addAction(clear)

        menu.exec_(self.console_text.viewport().mapToGlobal(pos))

    def _clear_console(self):
        """手动清空监控（复位进度条重绘状态）"""
        self.console_text.clear()
        self._last_console_is_bar = False

    def _export_console(self):
        """导出监控全文到 config/logs/console_export_时间戳.txt（等价黑窗回放）"""
        text = self.console_text.toPlainText()
        if not text.strip():
            InfoBar.warning(
                title=tr['Setting']['MonitorTab'],
                content=tr['Setting']['ConsoleExportEmpty'],
                parent=self.window(), duration=3000)
            return
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(root, 'config', 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        path = os.path.join(
            logs_dir, f'console_export_{datetime.now():%Y%m%d_%H%M%S}.txt')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
        except OSError as e:
            InfoBar.error(
                title=tr['Setting']['MonitorTab'],
                content=tr['Setting']['ConsoleExportFailed'].format(e),
                parent=self.window(), duration=5000)
            return
        InfoBar.success(
            title=tr['Setting']['MonitorTab'],
            content=tr['Setting']['ConsoleExportDone'].format(path),
            parent=self.window(), duration=5000)

    
    def slider_changed(self, value):
        frame = None
        with self._video_cap_lock:
            if self.video_cap is not None and self.video_cap.isOpened():
                frame_no = self.video_slider.value()
                self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
                ret, frame = self.video_cap.read()
                if not ret:
                    frame = None
        if frame is not None:
            # 更新预览图像
            self.update_preview(frame)
        # B 框同步：有结果源时按同一帧号取结果帧（拖动滑块 = A/B 同步逐帧对比）
        self._sync_b_to_slider()

    # ------------------------------------------------------------------ #
    # A/B 同步对比：B 框结果源管理 + 同步播放
    # ------------------------------------------------------------------ #

    def _sync_b_to_slider(self):
        """按滑块当前帧号同步 B 框（无结果源时不动 B 框内容）"""
        if self.b_video_cap is None or not self.b_video_cap.isOpened():
            return
        frame_no = self.video_slider.value()
        self.b_video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ret, frame = self.b_video_cap.read()
        self.video_display_component.set_b_frame(frame if ret else None)

    def _reopen_source_cap(self):
        """按当前视频重开 A 路源（worker 收尾会释放 video_cap）。

        处理结束后预览拖动、A/B 同步播放都依赖 A 路 cap；旧逻辑只在
        on_task_output_path 里补开一次，且按钮启用态与真实状态会漂移
        （按钮亮着但 A 路已释放 → 点播放弹「结果还没有生成」，被用户报障）。
        """
        with self._video_cap_lock:
            if (self.video_cap is None or not self.video_cap.isOpened()) \
                    and self.video_path and os.path.isfile(self.video_path) \
                    and not is_image_file(self.video_path):
                self.video_cap = cv2.VideoCapture(get_readable_path(self.video_path))
                if not self.video_cap.isOpened():
                    self.video_cap = None
        return self.video_cap is not None and self.video_cap.isOpened()

    def _sync_play_button(self):
        """按 A/B 两路源的实时可用性同步播放按钮启用态（消除「亮着却点不了」）"""
        ready = (self.video_cap is not None and self.video_cap.isOpened()
                 and self.b_video_cap is not None and self.b_video_cap.isOpened())
        self.video_display_component.play_button.setEnabled(ready)
        return ready

    def _ensure_compare_sources(self):
        """同步播放前确保 A/B 两路源可用：A 路缺失时自愈重开"""
        self._reopen_source_cap()
        return self._sync_play_button()

    def _open_compare_output(self, output_path):
        """打开结果视频作为 B 框同步源（当前任务已完成输出）。

        三种结局均有用户可见反馈（不再静默）：
        - 成功 → 切 A/B 对比布局 + 就绪提示（每次新开结果源提示一次）；
        - 输出文件不存在 / 打不开 → 警告并退回占位。
        """
        self._stop_compare_play()
        was_open = self.b_video_cap is not None
        if self.b_video_cap is not None:
            self.b_video_cap.release()
            self.b_video_cap = None
        if not output_path or not os.path.isfile(output_path):
            self.video_display_component.set_b_frame(None)
            self.video_display_component.play_button.setEnabled(False)
            if output_path:
                InfoBar.warning(
                    title=tr['SubtitleExtractorGUI']['ComparePlayTip'],
                    content=tr['SubtitleExtractorGUI']['CompareOutputMissing'].format(output_path),
                    parent=self.window(), duration=5000)
            return
        cap = cv2.VideoCapture(get_readable_path(output_path))
        if not cap.isOpened():
            self.video_display_component.set_b_frame(None)
            self.video_display_component.play_button.setEnabled(False)
            InfoBar.warning(
                title=tr['SubtitleExtractorGUI']['ComparePlayTip'],
                content=tr['SubtitleExtractorGUI']['CompareOutputMissing'].format(output_path),
                parent=self.window(), duration=5000)
            return
        self.b_video_cap = cap
        ret, frame = cap.read()
        self.video_display_component.set_b_frame(frame if ret else None)
        # 有结果源 → 切到 A/B 对比布局（双框同步对比/播放）
        self.video_display_component.set_compare_mode(True)
        # A 路源就绪才开放同步播放（按钮常驻可见，启用态按两路实时同步）
        self._sync_play_button()
        # 就绪提示（仅从「无结果源」新开时提示，避免重复刷屏）
        if not was_open:
            InfoBar.success(
                title=tr['SubtitleExtractorGUI']['ComparePlayTip'],
                content=tr['SubtitleExtractorGUI']['CompareReadyHint'],
                parent=self.window(), duration=5000)

    def _close_compare_output(self):
        """释放 B 框结果源并复位按钮/占位"""
        self._stop_compare_play()
        if self.b_video_cap is not None:
            self.b_video_cap.release()
            self.b_video_cap = None
        self.video_display_component.play_button.setEnabled(False)
        self.video_display_component.set_b_frame(None)

    def _toggle_compare_play(self):
        """A/B 同步播放/暂停：从滑块当前位置起，两路源逐帧同步推进"""
        if self._compare_playing:
            self._stop_compare_play()
            return
        # 起播前自愈：A 路可能已被 worker 收尾释放（按钮亮着但点不了的原因）
        if not self._ensure_compare_sources():
            InfoBar.warning(
                title=tr['SubtitleExtractorGUI']['ComparePlayTip'],
                content=tr['SubtitleExtractorGUI']['CompareNotReady'],
                parent=self.window(), duration=3000)
            return
        frame_no = self.video_slider.value()
        with self._video_cap_lock:
            self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        self.b_video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        # 先置播放态：起播 tick 若立即到结尾，_stop_compare_play 会复位为 False
        self._compare_playing = True
        # 起播前先显示当前位置的 A/B 帧（seek 后立即出画面，不闪占位）
        self._compare_tick()
        if not self._compare_playing:
            return  # 起播即到结尾（已停止）不再启动定时器
        interval = max(10, int(1000 / (self.fps or 30)))
        self._compare_timer.start(interval)
        self.video_display_component.play_button.setIcon(FluentIcon.PAUSE)

    def _stop_compare_play(self):
        """停止 A/B 同步播放（复位为可播放状态）"""
        if self._compare_timer.isActive():
            self._compare_timer.stop()
        self._compare_playing = False
        self.video_display_component.play_button.setIcon(FluentIcon.PLAY)

    def _compare_tick(self):
        """播放步进：顺序读取两路下一帧并同步显示；任一路到结尾即停"""
        with self._video_cap_lock:
            if self.video_cap is None or not self.video_cap.isOpened():
                self._stop_compare_play()
                return
            ret_a, frame_a = self.video_cap.read()
            pos = int(self.video_cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        if self.b_video_cap is None or not self.b_video_cap.isOpened():
            self._stop_compare_play()
            return
        ret_b, frame_b = self.b_video_cap.read()
        if not ret_a or not ret_b or frame_a is None or frame_b is None:
            # 播完：停在最后一帧并复位按钮
            self._stop_compare_play()
            if frame_a is not None:
                self.update_preview(frame_a)
            if frame_b is not None:
                self.video_display_component.set_b_frame(frame_b)
            return
        self.update_preview(frame_a)
        self.video_display_component.set_b_frame(frame_b)
        # 进度条跟随（不发 slider_changed，避免播放中被 seek 打断）
        if self.frame_count and pos != self.video_slider.value():
            self.video_slider.blockSignals(True)
            self.video_slider.setValue(max(1, min(self.frame_count, pos)))
            self.video_slider.blockSignals(False)

    def ab_sections_changed(self, ab_sections):
        get_current_task_index = self.task_list_component.get_current_task_index()
        if get_current_task_index == -1:
            return
        self.task_list_component.update_task_option(get_current_task_index, TaskOptions.AB_SECTIONS, ab_sections)

    def selections_changed(self, selections):
        get_current_task_index = self.task_list_component.get_current_task_index()
        if get_current_task_index == -1:
            return
        self.task_list_component.update_task_option(get_current_task_index, TaskOptions.SUB_AREAS, selections)

    def on_task_selected(self, index, file_path):
        """处理任务被选中事件

        Args:
            index: 任务索引
            file_path: 文件路径
        """
        # 处理链路中（worker 存活）→ 保留 A/B 对比布局；用户点选待处理任务 → 单框
        worker = getattr(self, '_worker_thread', None)
        processing_active = bool(worker and worker.is_alive())
        # 加载选中的视频进行预览（load_video 内会复位 B 框与同步播放；
        # 处理中 keep_compare 保留对比模式，避免 select_task 信号把布局打回单框）
        self.load_video(file_path, keep_compare=processing_active)
        if processing_active:
            self.video_display_component.set_compare_mode(True)
        ab_sections = self.task_list_component.get_task_option(index, TaskOptions.AB_SECTIONS, [])
        self.video_display_component.set_ab_sections(ab_sections)
        selections = self.task_list_component.get_task_option(index, TaskOptions.SUB_AREAS, [])
        if len(selections) <= 0:
            self.video_display_component.load_selections_from_config()
        else:
            self.video_display_component.set_selection_rects(selections)
        # B 框对比源：选中的任务已有完成输出 → 打开结果视频同步对比
        task = self.task_list_component.get_task(index)
        if task and task.status == TaskStatus.COMPLETED and task.output_path \
                and os.path.isfile(task.output_path):
            self._open_compare_output(task.output_path)
    
    def on_task_deleted(self, index):
        """处理任务被删除事件
        
        Args:
            index: 任务索引
        """
        # 删除的是正在处理的任务则重置；删除其前的任务会使索引前移，
        # 否则后续进度/状态/输出路径会写到错误的任务上
        if index == self.current_processing_task_index:
            self.current_processing_task_index = -1
        elif 0 <= self.current_processing_task_index and index < self.current_processing_task_index:
            self.current_processing_task_index -= 1
        
        task = self.task_list_component.get_task(0)
        if task:
            # 如果还有任务，选中第一个
            self.task_list_component.select_task(0)

    def update_preview(self, frame):
        self._last_frame = frame  # 缓存原始帧，供窗口缩放后自适应重渲染
        # 记录原图尺寸与帧率；letterbox 适配（contain 等比缩放 + 黑边）由显示组件完成
        self.video_display_component.set_video_parameters(
            self.frame_width, self.frame_height,
            self.fps if self.fps is not None else 30,
        )

        # 更新视频显示（组件内按控件实际尺寸 contain 适配，并记录映射几何）
        self.video_display_component.update_video_display(frame)

    def _on_preview_resized(self, width):
        """窗口缩放：更新预览尺寸并重渲染当前帧，使画面铺满可用区域"""
        if self.video_display_component.update_preview_size(width) and self._last_frame is not None:
            self.update_preview(self._last_frame)

    def stop_button_clicked(self):
        try:
            self._stop_event.set()
            running_process = self.running_process
            if running_process:
                ProcessManager.instance().terminate_by_process(running_process)
            # 更新任务状态为待处理
            if self.current_processing_task_index >= 0:
                self.task_list_component.update_task_status(self.current_processing_task_index, TaskStatus.PENDING)
        finally:
            self.running_process = None
            self.run_button.setVisible(True)
            self.stop_button.setVisible(False)
            # 停止后回到单框模式（画面最大化），恢复字幕框选便于重新调整
            self.video_display_component.set_compare_mode(False)
            self.video_display_component.set_dragger_enabled(True)

    @Slot(bool)
    def _toggle_buttons(self, show_run):
        """线程安全地切换按钮可见性"""
        self.run_button.setVisible(show_run)
        self.stop_button.setVisible(not show_run)

    @Slot(int, str)
    def on_task_output_path(self, index, output_path):
        """worker 线程任务完成后经信号回填输出路径（GUI 线程落库 + 持久化）"""
        self.task_list_component.set_task_output_path(index, output_path)
        # 完成的正是当前预览的任务 → 打开结果视频，B 框进入同步对比
        if index == self.task_list_component.get_current_task_index():
            # worker 收尾已释放 A 路视频源（video_cap=None），此处重开：
            # 否则同步播放按钮的 A 路就绪判定恒为 False
            self._reopen_source_cap()
            self._open_compare_output(output_path)

    def run_button_clicked(self):
        if not self.task_list_component.get_pending_tasks():
            self.append_output(tr['SubtitleExtractorGUI']['OpenVideoFirst'])
            return

        try:
            # 获取所有待执行的任务
            pending_tasks = self.task_list_component.get_pending_tasks()
            if not pending_tasks:
                return

            self._stop_event.clear()
            self.toggle_buttons_signal.emit(False)
            # 处理开始：关闭旧结果源（B 框改由 worker 实时结果帧接管）
            self._close_compare_output()
            # 切到 A/B 对比布局（处理中实时预览双框显示）
            self.video_display_component.set_compare_mode(True)
            # 重置监控：速度 EMA 与显存峰值
            self._speed_last = None
            self._speed_ema = 0.0
            self.gpu_monitor_bar.reset_peak()
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            # 开启后台线程处理视频
            def task():
                try:
                    while not self._stop_event.is_set():
                        try:
                            pending_tasks = self.task_list_component.get_pending_tasks()
                            if not pending_tasks:
                                break
                            pending_task = pending_tasks[0]
                            # 更新当前处理的任务索引
                            self.current_processing_task_index, task_item = pending_task
                            if not self.load_video(task_item.path, keep_compare=True):
                                self.append_log_signal.emit([tr['SubtitleExtractorGUI']['OpenVideoFailed'].format(task_item.path)])
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.FAILED)
                                continue

                            # 新任务开始：清空监控（监控即终端，每任务全新会话）
                            self.clear_console_signal.emit(task_item.name)

                            # 获取字幕区域坐标，未选择则使用全屏
                            subtitle_areas = self.task_list_component.get_task_option(self.current_processing_task_index, TaskOptions.SUB_AREAS, [])
                            if not subtitle_areas or len(subtitle_areas) <= 0:
                                subtitle_areas = [(0, self.frame_height, 0, self.frame_width)]
                                self.task_option_signal.emit(self.current_processing_task_index,
                                                             TaskOptions.SUB_AREAS, subtitle_areas)

                            self.video_display_component.save_selections_to_config()

                            # 更新任务状态为运行中（worker 线程不得直接触碰 Qt 控件，一律经信号）
                            self.task_progress_signal.emit(self.current_processing_task_index, 1)

                            # 选中当前任务
                            self.select_task_signal.emit(self.current_processing_task_index)

                            with self._video_cap_lock:
                                if self.video_cap:
                                    self.video_cap.release()
                                    self.video_cap = None

                            self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.PROCESSING)
                            # SUB_AREAS 在组件内即按原图像素坐标存储，无需二次转换，直接下发
                            options = dict(task_item.options)
                            # 输出同名不覆盖：上次输出可能仍被残留的 python 子进程
                            # 占用（合并后进程退出/句柄释放有延迟），且不应覆盖旧成果；
                            # 存在同名输出时加序号另存（_no_sub_2.mp4、_3…）
                            task_item.output_path = resolve_output_path(task_item.path)
                            output_path = task_item.output_path
                            # 上一次运行的兜底子进程可能仍活着（占用旧成片与 GPU），
                            # 启动新任务前先清理，避免句柄占用与显存拖慢
                            self._terminate_stale_residue_process()
                            process = self.run_task_with_segments(task_item.path, output_path, options)

                            # 检查是否在处理过程中被停止
                            if self._stop_event.is_set():
                                break

                            # 更新任务状态为已完成/失败。
                            # 仅当状态已同步为 PROCESSING 才判失败：GUI 线程繁忙时
                            # PROCESSING 信号可能尚未落地，exitcode!=0 + PENDING 的组合
                            # 若直接标 FAILED 会覆盖停止操作设置的 PENDING（状态竞态）
                            task_obj = self.task_list_component.get_task(self.current_processing_task_index)
                            if process.exitcode == 0 and not self._stop_event.is_set() \
                                    and task_obj and task_obj.status == TaskStatus.PROCESSING:
                                self.progress_signal.emit(100, True)
                                # 任务完成, 更新输出路径为只读（经信号在 GUI 线程落库+持久化）
                                self.task_output_path_signal.emit(self.current_processing_task_index, output_path)
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.COMPLETED)
                            elif task_obj and task_obj.status == TaskStatus.PROCESSING:
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.FAILED)

                        except Exception as e:
                            print(e)
                            self.append_log_signal.emit([f"Error: {e}"])
                            # 更新任务状态为失败
                            if self.current_processing_task_index >= 0:
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.FAILED)
                            break
                        finally:
                            with self._video_cap_lock:
                                if self.video_cap:
                                    self.video_cap.release()
                                    self.video_cap = None
                            time.sleep(1)
                finally:
                    self.toggle_buttons_signal.emit(True)

            self._worker_thread = threading.Thread(target=task, daemon=True)
            self._worker_thread.start()
        except Exception as e:
            print(traceback.format_exc())
            self.append_log_signal.emit([f"Error: {e}"])
            self.toggle_buttons_signal.emit(True)

    @staticmethod
    def remover_process(queue, video_path, output_path, options):
        """
        在子进程中执行字幕提取的函数
        
        Args:
            video_path: 视频文件路径
            output_path: 输出文件路径
            options: 选项
        """
        sr = None
        try:
            # 子进程原始输出统一进「监控」标签（监控内容与黑窗终端保持一致）：
            # 有控制台（bat/exe 启动）时 Tee——原始内容同时写控制台与队列；
            # 无控制台（pythonw）时仅写队列。
            # 必须放在 from backend.main import 之前：tqdm 在导入期捕获 sys.stderr，
            # 且重依赖（torch/paddle）导入期就可能报错，晚接管道会让输出/traceback
            # 绕过监控或落进 None 永远不可见。
            class _QueueConsoleWriter:
                """子进程 stdout/stderr → 控制台镜像 + 监控队列。

                终端语义逐字符处理：\\r 开始原地重绘（tqdm 进度条）、\\n 结束整行。
                重绘行加 \\x01 前缀发队列，GUI 端据此前缀「替换上一行」而非追加——
                监控里呈现与终端一致的进度条动画（旧实现按 \\n 优先拆行，
                会把 \\r 重绘粘进上一行、且每帧重绘都堆成新行，进度条无法呈现）。
                """
                BAR_MARK = '\x01'
                # tqdm 用 _supports_unicode(fp) 探测 fp.encoding 决定进度条字符：
                # 无此属性时回退 ASCII '#'。声明 UTF-8 让进度条用实心方块 █
                encoding = 'utf-8'

                def __init__(self, q, original):
                    self._q = q
                    self._original = original
                    self._cur = []  # 当前行字符缓冲（跨 write 调用保持，半行不丢）

                def write(self, s):
                    if self._original is not None:
                        try:
                            self._original.write(s)
                            self._original.flush()
                        except Exception:
                            pass  # 控制台编码异常不影响监控链路
                    for ch in s:
                        if ch == '\r':
                            # \r：当前行作为「重绘行」发出（tqdm 每次刷新触发）
                            text = ''.join(self._cur).strip()
                            self._cur = []
                            if text:
                                SubtitleRemoverRemoteCall.remote_call_console(
                                    self._q, self.BAR_MARK + text)
                        elif ch == '\n':
                            # \n：当前行作为「整行」发出
                            text = ''.join(self._cur).strip()
                            self._cur = []
                            if text:
                                SubtitleRemoverRemoteCall.remote_call_console(self._q, text)
                        else:
                            self._cur.append(ch)
                    return len(s)

                def flush(self):
                    # 半行留在 _cur 等待 \r/\n 边界（tqdm 每次 write 后都调 flush，
                    # 若在此清空会丢掉进度条的当前帧）
                    if self._original is not None:
                        try:
                            self._original.flush()
                        except Exception:
                            pass

                def isatty(self):
                    try:
                        return bool(self._original is not None and self._original.isatty())
                    except Exception:
                        return False
            sys.stdout = _QueueConsoleWriter(queue, sys.__stdout__)
            sys.stderr = _QueueConsoleWriter(queue, sys.__stderr__)
            from backend.main import SubtitleRemover
            sr = SubtitleRemover(video_path, True)
            sr.video_out_path = output_path
            for key in options:
                setattr(sr, key, options[key])
            sr.add_progress_listener(lambda progress, isFinished: SubtitleRemoverRemoteCall.remote_call_update_progress(queue, progress, isFinished))
            sr.append_output = lambda *args: SubtitleRemoverRemoteCall.remote_call_append_log(queue, args)
            sr.manage_process = lambda pid: SubtitleRemoverRemoteCall.remote_call_manage_process(queue, pid)
            sr.update_preview_with_comp = lambda *args: SubtitleRemoverRemoteCall.remote_call_update_preview_with_comp(queue, args)
            sr.run()
        except Exception as e:
            # traceback 完整进「监控」标签（pythonw 下 print_exc 落进 None 会静默丢失）
            tb = traceback.format_exc()
            for ln in tb.splitlines():
                SubtitleRemoverRemoteCall.remote_call_console(queue, ln)
            # 崩溃自报告：环境 + 日志尾部 + traceback 落盘到输出目录旁，客户报障一次说清
            try:
                report = write_crash_report(video_path, output_path, tb)
                SubtitleRemoverRemoteCall.remote_call_console(
                    queue, f'[崩溃报告] 已保存: {report}')
            except Exception:
                pass
            SubtitleRemoverRemoteCall.remote_call_catch_error(queue, e)
        finally:
            if sr:
                sr.isFinished = True
                sr.vsf_running = False
            SubtitleRemoverRemoteCall.remote_call_finish(queue)
            

    # 修改run_subtitle_remover_process方法
    def run_subtitle_remover_process(self, video_path, output_path, options, progress_cb=None):
        """
        使用多进程执行字幕提取，并等待进程完成

        Args:
            video_path: 视频文件路径
            output_path: 输出文件路径
            options: 任务选项
            progress_cb: 进度回调 (progress_total, isFinished)，None 用默认信号
        """
        subtitle_remover_remote_caller = SubtitleRemoverRemoteCall()
        subtitle_remover_remote_caller.register_update_progress_callback(progress_cb or self.progress_signal.emit)
        subtitle_remover_remote_caller.register_log_callback(self.append_log_signal.emit)
        subtitle_remover_remote_caller.register_console_callback(self.console_signal.emit)
        subtitle_remover_remote_caller.register_update_preview_with_comp_callback(self.update_preview_with_comp_signal.emit)
        subtitle_remover_remote_caller.register_error_callback(self.task_error_signal.emit)
        process = multiprocessing.Process(
            target=HomeInterface.remover_process,
            args=(subtitle_remover_remote_caller.queue, video_path, output_path, options)
        )
        try:
            if self._stop_event.is_set():
                return process
            process.start()
            ProcessManager.instance().add_process(process)
            self.running_process = process
            process.join()
            print(f"Process exited with code {process.exitcode}")
        finally:
            subtitle_remover_remote_caller.stop()
        return process

    # ------------------------------------------------------------------ #
    # 长视频分段执行（可中断续跑）
    # ------------------------------------------------------------------ #

    def _segment_log(self, *args):
        self.append_log_signal.emit([' '.join(str(a) for a in args)])

    def _make_segment_progress_cb(self, done_frames: int, seg_cap: int, total_frames: int):
        """把段内进度 (0-100) 合成为全局进度；段级 isFinished 压为 False，
        避免 processing_finished 在段间被误触发。"""
        def cb(progress_total, isFinished):
            overall = (done_frames + seg_cap * float(progress_total) / 100.0) / max(1, total_frames) * 100.0
            self.progress_signal.emit(min(100, int(overall)), False)
        return cb

    def run_task_with_segments(self, video_path, output_path, options):
        """长视频分段执行（可中断续跑）；禁用/短片时退化为整片处理。

        返回带 exitcode 属性的对象（整片=子进程；分段=末段子进程或 shim）。
        """
        from backend.tools.segment_executor import (probe_video_frames,
            compute_segment_frames, build_task_params, build_segment_options,
            concat_segments, mux_audio)
        from backend.tools.segmenter import (load_or_rebuild_checkpoint,
            derive_resume_info, params_hash)

        frame_count, fps = probe_video_frames(video_path)
        seg_frames = compute_segment_frames(frame_count, fps, config.segmentSeconds.value)
        if seg_frames is None:
            return self.run_subtitle_remover_process(video_path, output_path, options)

        # 段间重叠帧数（0=关闭）：除第一段外每段向前多取 N 帧作邻帧模型上下文，
        # 只写本段真正的帧；参与参数哈希——改重叠值=拒绝续跑/按新参数重建
        overlap = int(config.segmentOverlapFrames.value or 0)

        out_dir = os.path.dirname(output_path) or '.'
        stem = os.path.splitext(os.path.basename(video_path))[0]
        task_dir = os.path.join(out_dir, f'{stem}_no_sub_segments')
        ckpt_path = os.path.join(task_dir, 'checkpoint.json')
        params = build_task_params(options, config.inpaintMode.value)
        params['segmentOverlapFrames'] = overlap

        checkpoint, mode = load_or_rebuild_checkpoint(
            ckpt_path, params, frame_count, seg_frames, log=self._segment_log,
            overlap=overlap)
        if mode == 'refused':
            self._segment_log('[续跑] 参数与上次不一致且已有完成段，拒绝续跑。',
                              f'如需全新重跑请删除目录：{task_dir}')
            return _SegmentResult(exitcode=1)
        checkpoint.reset_running()
        info = derive_resume_info(ckpt_path, params_hash(params))
        if info.chip_text:
            self._segment_log('[续跑]', info.chip_text)

        total = checkpoint.total_count()
        last_process = None
        for seg in checkpoint.pending():
            if self._stop_event.is_set():
                self._segment_log(f'[分段] 已中断：段 {seg.index + 1}/{total} 未跑，'
                                  f'已完成段保留，重跑本任务自动续接')
                return last_process if last_process is not None else _SegmentResult(exitcode=0)
            seg_file = os.path.join(task_dir, seg.output_name)
            checkpoint.mark_running(seg.index)
            seg_options = dict(options)
            # 处理范围含重叠上下文，frame_write_start=段首（跳过前 overlap 帧）
            seg_options.update(build_segment_options(seg))
            if seg.overlap:
                self._segment_log(f'[分段] 段 {seg.index + 1}/{total}：写入帧 '
                                  f'{seg.skip}-{seg.frame_end}（{seg.cap} 帧），'
                                  f'处理 {seg.frame_start}-{seg.frame_end}'
                                  f'（含 {seg.overlap} 帧重叠上下文）')
            else:
                self._segment_log(f'[分段] 段 {seg.index + 1}/{total}：帧 {seg.skip}-{seg.frame_end} '
                                  f'（{seg.cap} 帧）')
            process = self.run_subtitle_remover_process(
                video_path, seg_file, seg_options,
                progress_cb=self._make_segment_progress_cb(
                    checkpoint.done_frames(), seg.cap, frame_count))
            last_process = process
            if process.exitcode != 0:
                checkpoint.mark_failed(seg.index)
                return process  # 段失败/被终止：沿用 worker 的 exitcode != 0 → FAILED 判断
            # 段正常完成先落 checkpoint，再检查中断——避免“段已完成但状态未写”的丢失
            checkpoint.mark_done(seg.index)
            if self._stop_event.is_set():
                self._segment_log(f'[分段] 段 {seg.index + 1} 已完成后中断，'
                                  f'重跑本任务自动从下一段续接')
                return last_process

        # 全部段完成：拼接 + 音频封装 + 清理
        self._segment_log(f'[分段] 全部 {total} 段完成，拼接成片…')
        concat_out = os.path.join(task_dir, 'concat_no_sub.mp4')
        try:
            concat_segments(task_dir, checkpoint.segments(), concat_out, self._segment_log)
            ok = mux_audio(video_path, concat_out, output_path, self._segment_log)
        except Exception as e:
            self._segment_log(f'[分段] 拼接失败：{e}')
            return _SegmentResult(exitcode=1)
        finally:
            if os.path.exists(concat_out):
                try:
                    os.remove(concat_out)
                except OSError:
                    pass
        if not ok:
            return _SegmentResult(exitcode=1)
        shutil.rmtree(task_dir, ignore_errors=True)  # 段文件与 checkpoint 清理
        # 成片级残留二遍兜底（与非分段模式的 SubtitleRemover._residue_second_pass 对齐）：
        # 对 concat+mux 后的成片重跑一次 --residue-only 子进程，成功后原子替换
        self._residue_second_pass_on_output(output_path, options)
        self._segment_log('[分段] 成片输出：', output_path)
        self.progress_signal.emit(100, True)
        return _SegmentResult(exitcode=0)

    def _residue_second_pass_on_output(self, output_path, options):
        """分段成片残留二遍兜底。

        对 concat+mux 后的成片以后端子进程重跑残留检测与二修（后端 CLI 契约）：

            python backend/main.py -i <成片路径> -o <二修临时输出>
                  --inpaint-mode <当前inpaintMode> --residue-only

        退出码 0 且临时输出存在 → 原子替换成片（中文路径经 Python os.replace）；
        失败仅日志警告、不阻塞、保留原成片。config.residueCheck 关闭时直接跳过。
        """
        # config 可能被测试以 SimpleNamespace 替换（缺 residueCheck 时视为关闭）
        residue_check = getattr(config, 'residueCheck', None)
        if residue_check is None or not residue_check.value:
            return False
        if self._stop_event.is_set():
            self._segment_log('[分段] 已请求停止，跳过成片残留二遍兜底')
            return False
        # 与分段子进程同参的 inpaint 模式（段子进程经 config.json 读取同一配置）；
        # options 显式携带时优先
        mode = options.get('inpaint_mode') or config.inpaintMode.value
        mode_name = getattr(mode, 'value', None) or str(mode)
        out_dir = os.path.dirname(output_path) or '.'
        stem = os.path.splitext(os.path.basename(output_path))[0]
        residue_out = os.path.join(out_dir, f'{stem}_residue_tmp.mp4')
        # 项目根：ui/home_interface.py 的上两级（与 backend/config.py 读取
        # config/config.json 的相对 CWD 要求一致）
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = [sys.executable, os.path.join(root, 'backend', 'main.py'),
               '-i', output_path, '-o', residue_out,
               '--inpaint-mode', mode_name, '--residue-only']
        log_path = os.path.join(out_dir, f'{stem}_residue_tmp.log')
        self._segment_log(f'[分段] 成片残留二遍兜底（模式 {mode_name}）… '
                          f'实时日志：{os.path.basename(log_path)}')
        t0 = time.time()
        stopped = False
        proc = None
        try:
            with open(log_path, 'w', encoding='utf-8') as log_f:
                proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL,
                                        stdout=log_f, stderr=subprocess.STDOUT,
                                        creationflags=SUBPROCESS_CREATION_FLAGS)
                self._residue_process = proc
                # 可中断等待：每秒检查一次停止信号与超时，不再死等 2 小时。
                # 子进程全程以读方式打开成片，终止后句柄即刻释放
                while True:
                    try:
                        proc.wait(timeout=1.0)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if self._stop_event.is_set():
                        self._segment_log('[分段] 已请求停止，终止残留兜底子进程')
                        self._kill_process(proc)
                        stopped = True
                        break
                    if time.time() - t0 > 7200:
                        self._segment_log('[分段] 残留兜底超时（2h），终止子进程')
                        self._kill_process(proc)
                        break
        except Exception as e:
            self._segment_log(f'[分段] 残留二遍兜底未执行（保留原成片）：{e}')
            return False
        finally:
            self._residue_process = None
        if stopped or proc.returncode != 0 or not os.path.exists(residue_out):
            # 失败/停止时从日志文件取尾部，帮助定位卡住原因
            tail = ''
            try:
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    tail = ''.join(f.readlines()[-15:]).strip()[-500:]
            except OSError:
                pass
            reason = '已停止' if stopped else f'退出码 {proc.returncode}'
            self._segment_log(f'[分段] 残留二遍兜底失败（保留原成片），{reason}。'
                              f'日志尾部：\n{tail}')
            if os.path.exists(residue_out):
                try:
                    os.remove(residue_out)
                except OSError:
                    pass
            return False
        try:
            # 二修输出原子替换成片（半成品不会覆盖原成片）
            os.replace(residue_out, output_path)
        except OSError as e:
            self._segment_log(f'[分段] 残留二遍输出替换失败（保留原成片）：{e}')
            try:
                os.remove(residue_out)
            except OSError:
                pass
            return False
        try:
            os.remove(log_path)  # 成功即清日志，失败保留供排查
        except OSError:
            pass
        self._segment_log('[分段] 残留二遍兜底完成，成片已更新：', output_path)
        return True

    @staticmethod
    def _kill_process(proc):
        """终止子进程：先 terminate，10 秒未退再强杀，确保句柄释放"""
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
        except Exception:
            pass

    def _terminate_stale_residue_process(self):
        """新任务启动前清理上一次残留的兜底子进程：它以读方式打开着旧成片，
        进程退出/句柄释放有延迟，不清理会持续占用 GPU 与文件"""
        proc = getattr(self, '_residue_process', None)
        if proc is not None and proc.poll() is None:
            self._segment_log('[分段] 清理上次残留的兜底子进程（仍在运行）')
            self._kill_process(proc)
        self._residue_process = None

    @Slot()
    def processing_finished(self):
        pending_tasks = self.task_list_component.get_pending_tasks()
        if pending_tasks:
            # 还有待执行任务, 忽略
            return
        # 处理完成后恢复界面可用性
        self.run_button.setVisible(True)
        self.stop_button.setVisible(False)
        self.se = None
        # 重置视频滑块
        self.video_slider.setValue(1)
        # 重置当前处理任务索引
        self.current_processing_task_index = -1
        # worker 收尾已释放 A 路视频源：重开，否则处理后拖动滑块/同步播放全部失效
        self._reopen_source_cap()
        self._sync_play_button()

    @Slot(int, bool)
    def update_progress(self, progress_total, isFinished):
        try:
            pos = min(self.frame_count - 1, int(progress_total / 100 * self.frame_count))
            if pos != self.video_slider.value():
                self.video_slider.blockSignals(True)
                self.video_slider.setValue(pos)
                self.video_slider.blockSignals(False)
            
            # 更新任务进度
            if self.current_processing_task_index >= 0:
                self.task_list_component.update_task_progress(
                    self.current_processing_task_index,
                    progress_total,
                )

            # 处理速度（帧/s，EMA 平滑），喂给监控栏
            now = time.time()
            if self._speed_last is not None and self.frame_count:
                last_t, last_p = self._speed_last
                dt = now - last_t
                if dt > 0.05:
                    df = (progress_total - last_p) / 100 * self.frame_count
                    if df > 0:
                        inst = df / dt
                        self._speed_ema = inst if self._speed_ema == 0 else (self._speed_ema * 0.4 + inst * 0.6)
                        self.gpu_monitor_bar.update_speed(self._speed_ema)
                    self._speed_last = (now, progress_total)
            else:
                self._speed_last = (now, progress_total)
            
            # 检查是否完成
            if isFinished:
                self.processing_finished()
        except Exception as e:
            # 捕获任何异常，防止崩溃
            print(f"更新进度时出错: {str(e)}")

    @Slot(list)
    def append_log(self, log):
        self.append_output(*log)

    def append_output(self, *args):
        """添加文本到输出区域并控制滚动
        Args:
            *args: 要输出的内容，多个参数将用空格连接
        """
        # 将所有参数转换为字符串并用空格连接
        text = ' '.join(str(arg) for arg in args).rstrip()
        timestamp = datetime.now().strftime('%H:%M:%S')
        # 转义HTML特殊字符
        escaped = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        # 根据内容判断消息类型并着色
        if '错误' in text or 'Error' in text or '失败' in text or 'Failed' in text:
            color = '#e74c3c'
        elif '成功' in text or '完成' in text or 'Success' in text or 'Finished' in text:
            color = '#27ae60'
        elif '警告' in text or 'Warning' in text:
            color = '#f39c12'
        else:
            color = '#2980b9'
        html = f'<span style="color:#888;">[{timestamp}]</span> <span style="color:{color};">{escaped}</span><br>'
        self.output_text.append(html)
        print(*args)  # 保持原始的 print 行为
        # 始终滚动到底部
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @Slot(list)
    def update_preview_with_comp(self, args):
        """处理中实时预览：A 框=源帧（叠加字幕区域矩形），B 框=去字幕结果帧"""
        frame_ori, frame_comp = args
        if self.current_processing_task_index >= 0:
            subtitle_areas = self.task_list_component.get_task_option(self.current_processing_task_index, TaskOptions.SUB_AREAS, [])
            if len(subtitle_areas) > 0:
                if frame_ori is frame_comp:
                    frame_ori = frame_ori.copy()
                # SUB_AREAS 为原图像素坐标，直接在原图上绘制（取整防 float 坐标）
                for rect in subtitle_areas:
                    ymin, ymax, xmin, xmax = rect
                    cv2.rectangle(frame_ori, (int(xmin), int(ymin)),
                                  (int(xmax), int(ymax)), (0, 255, 0), 2)
        # A/B 双框：结果帧进 B 框（分段处理时同样实时更新）
        self.video_display_component.set_b_frame(frame_comp)
        # contain 适配 + 黑边由显示组件完成（A 框不画选区：矩形已直接画在帧上）
        self.video_display_component.update_video_display(frame_ori, draw_selection=False)
        self.video_display_component.set_dragger_enabled(False)

    @Slot(object)
    def on_task_error(self, e):
        self.append_output(tr['SubtitleExtractorGUI']['ErrorDuringProcessing'].format(str(e)))
        # 错误详情同步进「监控」标签（用户排查时第一个看的地方）
        self.console_signal.emit('❌ ' + tr['SubtitleExtractorGUI']['ErrorDuringProcessing'].format(str(e)))
        tb = getattr(e, '__traceback__', None)
        if tb is not None:
            for ln in traceback.format_exception(type(e), e, tb):
                for piece in ln.rstrip().splitlines():
                    self.console_signal.emit(piece)
        if self.current_processing_task_index >= 0:
            self.task_list_component.update_task_status(self.current_processing_task_index, TaskStatus.FAILED)

    def load_video(self, video_path, keep_compare=False):
        """加载视频预览。

        keep_compare=False（用户主动换视频/选中待处理任务）→ 回单框模式
        （画面按最长边撑满窗口）；keep_compare=True（worker 处理链路加载
        当前任务）→ 保留当前模式，不打断开始处理时切入的 A/B 对比布局。
        """
        self.video_path = video_path
        # 换视频：停止 A/B 同步播放并释放旧结果源
        self._close_compare_output()
        if not keep_compare:
            self.video_display_component.set_compare_mode(False)
        with self._video_cap_lock:
            if self.video_cap:
                self.video_cap.release()
                self.video_cap = None
        # 如果是图片文件，直接走图片加载路径
        if is_image_file(video_path):
            return self.load_as_picture(video_path)
        with self._video_cap_lock:
            self.video_cap = cv2.VideoCapture(get_readable_path(self.video_path))
            if not self.video_cap.isOpened():
                self.video_cap = None
                return self.load_as_picture(video_path)
            ret, frame = self.video_cap.read()
            if not ret:
                self.video_cap.release()
                self.video_cap = None
                return self.load_as_picture(video_path)
            self.frame_count = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.frame_height = int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.frame_width = int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.fps = self.video_cap.get(cv2.CAP_PROP_FPS)

        self.update_preview(frame)
        self.video_slider.setMaximum(self.frame_count)
        self.video_slider.setValue(1)
        self.video_display_component.set_dragger_enabled(True)
        # 视频模式下恢复用户原始的 inpaint 模式选择
        self._unlock_inpaint_mode()
        return True

    def load_as_picture(self, path):
        if not is_image_file(path):
            return False
        self.video_path = path
        self.video_cap = None
        frame = read_image(get_readable_path(path))
        if frame is None:
            return False
        self.frame_count = 1
        self.frame_height = frame.shape[0]
        self.frame_width = frame.shape[1]
        self.fps = 1
        self.update_preview(frame)
        self.video_slider.setMaximum(self.frame_count)
        self.video_slider.setValue(1)
        self.video_display_component.set_dragger_enabled(True)
        # 图片模式锁定为 LAMA
        self._lock_inpaint_mode_to_lama()
        return True

    def _lock_inpaint_mode_to_lama(self):
        """图片模式锁定 inpaint 模式为 LAMA"""
        if self._saved_inpaint_mode is None:
            self._saved_inpaint_mode = config.inpaintMode.value
        config.set(config.inpaintMode, InpaintMode.LAMA)
        self.model_row.combo.setEnabled(False)

    def _unlock_inpaint_mode(self):
        """视频模式恢复用户原始的 inpaint 模式选择"""
        if self._saved_inpaint_mode is not None:
            config.set(config.inpaintMode, self._saved_inpaint_mode)
            self._saved_inpaint_mode = None
        self.model_row.combo.setEnabled(True)
        self.video_slider.setValue(1)
        self.video_display_component.set_dragger_enabled(True)
        return True


    def open_file(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            tr['SubtitleExtractorGUI']['Open'],
            "",
            "All Files (*.*);;Video Files (*.mp4 *.flv *.wmv *.avi *.mkv *.mov);;Image Files (*.jpg *.jpeg *.png *.bmp *.webp *.tiff)"
        )
        if files:
            files_loaded = []
            # 倒序打开, 确保第一个视频截图显示在屏幕上
            for path in reversed(files):
                if self.load_video(path):
                    self.append_output(f"{tr['SubtitleExtractorGUI']['OpenVideoSuccess']}: {path}")
                    files_loaded.append(path)
                else:
                    self.append_output(f"{tr['SubtitleExtractorGUI']['OpenVideoFailed']}: {path}")
            # 正序添加, 确保任务列表顺序一致
            for path in reversed(files_loaded):
                # 添加到任务列表
                self.task_list_component.add_task(path)
                index = max(0, self.task_list_component.find_task_index_by_path(path))
                self.task_list_component.select_task(index)

    def closeEvent(self, event):
        """窗口关闭时断开信号连接并清理资源"""
        try:
            # 通知 worker 线程停止
            self._stop_event.set()
            # 停止 GPU 监控采样线程
            self.gpu_monitor_bar.stop()
            # 终止子进程
            ProcessManager.instance().terminate_all()
            # 等待 worker 线程结束（最多5秒）
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=5)
            # 快照模式下不自动落盘；此处仅刷新分段续跑信息与状态行
            self.task_list_component.save_tasks()

            # 断开信号连接
            self.progress_signal.disconnect(self.update_progress)
            self.append_log_signal.disconnect(self.append_log)
            self.update_preview_with_comp_signal.disconnect(self.update_preview_with_comp)
            self.task_error_signal.disconnect(self.on_task_error)
            self.toggle_buttons_signal.disconnect(self._toggle_buttons)
            self.video_display_component.video_slider.valueChanged.disconnect(self.slider_changed)
            self.video_display_component.ab_sections_changed.disconnect(self.ab_sections_changed)
            self.video_display_component.selections_changed.disconnect(self.selections_changed)
            # 其余信号一并断开（部分连接目标是 lambda，用无参 disconnect）
            for _sig in (self.console_signal, self.task_status_signal,
                         self.task_output_path_signal, self.select_task_signal,
                         self.task_progress_signal, self.task_option_signal):
                try:
                    _sig.disconnect()
                except (TypeError, RuntimeError):
                    pass
            # 释放视频资源
            with self._video_cap_lock:
                if self.video_cap:
                    self.video_cap.release()
                    self.video_cap = None
            if self.b_video_cap is not None:
                self.b_video_cap.release()
                self.b_video_cap = None
        except Exception as e:
            print("Error during close window:", e)
        super().closeEvent(event)
    