import os
import shutil
import sys
import cv2
import time
import torch
import threading
import multiprocessing
import traceback
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QTabWidget, QPlainTextEdit
from PySide6.QtCore import Slot, QRect, Signal, Qt
from PySide6 import QtWidgets
from datetime import datetime
from qfluentwidgets import (PushButton, PrimaryPushButton, CardWidget, TextEdit, FluentIcon)
from ui.component.video_display_component import VideoDisplayComponent
from ui.component.task_list_component import TaskListComponent, TaskStatus, TaskOptions
from ui.component.gpu_monitor_bar import GpuMonitorBar
from ui.component.compact_rows import CompactComboRow
from ui.icon.my_fluent_icon import MyFluentIcon
from backend.config import config, tr
from backend.tools.constant import InpaintMode
from backend.tools.subtitle_remover_remote_call import SubtitleRemoverRemoteCall
from backend.tools.process_manager import ProcessManager
from backend.tools.common_tools import get_readable_path, is_image_file, read_image


class _SegmentResult:
    """分段任务的返回 shim：对齐子进程的 exitcode 判断语义。"""

    def __init__(self, exitcode: int):
        self.exitcode = exitcode

class HomeInterface(QWidget):
    progress_signal = Signal(int, bool)
    append_log_signal = Signal(list)
    console_signal = Signal(object)
    update_preview_with_comp_signal = Signal(list)
    task_error_signal = Signal(object)
    toggle_buttons_signal = Signal(bool)  # True=显示运行按钮, False=显示停止按钮
    task_status_signal = Signal(int, object)  # (task_index, TaskStatus)
    select_task_signal = Signal(int)  # task_index
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

        # 当前正在处理的任务索引
        self.current_processing_task_index = -1
        # 处理速度 EMA 计算用
        self._speed_last = None  # (time, progress_total)
        self._speed_ema = 0.0

        self.__init_widgets()
        self.progress_signal.connect(self.update_progress)
        self.append_log_signal.connect(self.append_log)
        self.console_signal.connect(self.append_console)
        self.update_preview_with_comp_signal.connect(self.update_preview_with_comp)
        self.task_error_signal.connect(self.on_task_error)
        self.toggle_buttons_signal.connect(self._toggle_buttons)
        self.task_status_signal.connect(lambda idx, status: self.task_list_component.update_task_status(idx, status))
        self.select_task_signal.connect(self.task_list_component.select_task)

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
        self.video_display_component.ab_sections_changed.connect(self.ab_sections_changed)
        self.video_display_component.selections_changed.connect(self.selections_changed)
        self.video_display_component.size_changed.connect(self._on_preview_resized)
        self._last_frame = None
        left_layout.addWidget(self.video_display_component)
        
        # 获取视频显示和滑块的引用
        self.video_display = self.video_display_component.video_display
        self.video_slider = self.video_display_component.video_slider
        self.video_slider.valueChanged.connect(self.slider_changed)
        
        # 输出文本区域
        self.output_text = TextEdit()
        self.output_text.setMinimumHeight(150)
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
        self.console_text.setMaximumBlockCount(5000)
        self.console_text.setStyleSheet(
            "QPlainTextEdit { background:#101010; color:#C8C8C8;"
            "font-family:'Consolas'; font-size:11px; border:none; }")
        self.log_tabs.addTab(self.console_text, tr["Setting"]["MonitorTab"])
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
    
    def append_console(self, lines):
        """原控制台输出 → 监控标签（分发器传单条 str，兼容 list）"""
        if isinstance(lines, str):
            lines = [lines]
        for line in lines:
            self.console_text.appendPlainText(line)
        sb = self.console_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_scroll_change(self, value):
        """监控滚动条位置变化（日志自动滚动常开，无需额外处理）"""
        pass

    
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
        # 加载选中的视频进行预览
        self.load_video(file_path)
        ab_sections = self.task_list_component.get_task_option(index, TaskOptions.AB_SECTIONS, [])
        self.video_display_component.set_ab_sections(ab_sections)
        selections = self.task_list_component.get_task_option(index, TaskOptions.SUB_AREAS, [])
        if len(selections) <= 0:
            self.video_display_component.load_selections_from_config()
        else:
            self.video_display_component.set_selection_rects(selections)
    
    def on_task_deleted(self, index):
        """处理任务被删除事件
        
        Args:
            index: 任务索引
        """
        # 如果删除的是正在处理的任务，则需要更新状态
        if index == self.current_processing_task_index:
            self.current_processing_task_index = -1
        
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

    @Slot(bool)
    def _toggle_buttons(self, show_run):
        """线程安全地切换按钮可见性"""
        self.run_button.setVisible(show_run)
        self.stop_button.setVisible(not show_run)

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
                            if not self.load_video(task_item.path):
                                self.append_log_signal.emit([tr['SubtitleExtractorGUI']['OpenVideoFailed'].format(task_item.path)])
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.FAILED)
                                continue

                            # 获取字幕区域坐标，未选择则使用全屏
                            subtitle_areas = self.task_list_component.get_task_option(self.current_processing_task_index, TaskOptions.SUB_AREAS, [])
                            if not subtitle_areas or len(subtitle_areas) <= 0:
                                subtitle_areas = [(0, self.frame_height, 0, self.frame_width)]
                                self.task_list_component.update_task_option(self.current_processing_task_index, TaskOptions.SUB_AREAS, subtitle_areas)

                            self.video_display_component.save_selections_to_config()

                            # 更新任务状态为运行中
                            self.task_list_component.update_task_progress(self.current_processing_task_index, 1)

                            # 选中当前任务
                            self.select_task_signal.emit(self.current_processing_task_index)

                            with self._video_cap_lock:
                                if self.video_cap:
                                    self.video_cap.release()
                                    self.video_cap = None

                            self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.PROCESSING)
                            # SUB_AREAS 在组件内即按原图像素坐标存储，无需二次转换，直接下发
                            options = dict(task_item.options)
                            # 清理缓存, 使用动态路径
                            task_item.output_path = None
                            output_path = task_item.output_path
                            process = self.run_task_with_segments(task_item.path, output_path, options)

                            # 检查是否在处理过程中被停止
                            if self._stop_event.is_set():
                                break

                            # 更新任务状态为已完成
                            task_obj = self.task_list_component.get_task(self.current_processing_task_index)
                            if process.exitcode == 0 and task_obj and task_obj.status == TaskStatus.PROCESSING:
                                self.progress_signal.emit(100, True)
                                # 任务完成, 更新输出路径为只读
                                task_obj.output_path = output_path
                                self.task_status_signal.emit(self.current_processing_task_index, TaskStatus.COMPLETED)
                            else:
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
            from backend.main import SubtitleRemover
            # 无控制台（pythonw）时把子进程 stdout/stderr 重定向进队列 → GUI「监控」标签
            if sys.__stdout__ is None:
                class _QueueConsoleWriter:
                    def __init__(self, q):
                        self._q = q
                        self._buf = ''
                    def write(self, s):
                        self._buf += s
                        # \n 与 \r 都作为行界（tqdm 进度条用 \r 刷新）
                        for sep in ('\n', '\r'):
                            while sep in self._buf:
                                line, self._buf = self._buf.split(sep, 1)
                                if line.strip():
                                    SubtitleRemoverRemoteCall.remote_call_console(self._q, line.strip())
                    def flush(self):
                        # tqdm 每次 write 后都调 flush——半行直接丢弃，只在 \n/\r 边界出整行
                        self._buf = ''
                sys.stdout = _QueueConsoleWriter(queue)
                sys.stderr = _QueueConsoleWriter(queue)
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
            traceback.print_exc()
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
            compute_segment_frames, build_task_params, concat_segments, mux_audio)
        from backend.tools.segmenter import (load_or_rebuild_checkpoint,
            derive_resume_info, params_hash)

        frame_count, fps = probe_video_frames(video_path)
        seg_frames = compute_segment_frames(frame_count, fps, config.segmentSeconds.value)
        if seg_frames is None:
            return self.run_subtitle_remover_process(video_path, output_path, options)

        out_dir = os.path.dirname(output_path) or '.'
        stem = os.path.splitext(os.path.basename(video_path))[0]
        task_dir = os.path.join(out_dir, f'{stem}_no_sub_segments')
        ckpt_path = os.path.join(task_dir, 'checkpoint.json')
        params = build_task_params(options, config.inpaintMode.value)

        checkpoint, mode = load_or_rebuild_checkpoint(
            ckpt_path, params, frame_count, seg_frames, log=self._segment_log)
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
            seg_options['frame_start'] = seg.skip
            seg_options['frame_end'] = seg.frame_end
            seg_options['is_segment'] = True
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
        self._segment_log('[分段] 成片输出：', output_path)
        self.progress_signal.emit(100, True)
        return _SegmentResult(exitcode=0)

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
        """更新执行时预览"""
        frame_ori, frame_comp = args
        if self.current_processing_task_index >= 0:
            subtitle_areas = self.task_list_component.get_task_option(self.current_processing_task_index, TaskOptions.SUB_AREAS, [])
            if len(subtitle_areas) > 0:
                if frame_ori is frame_comp:
                    frame_ori = frame_ori.copy()
                # SUB_AREAS 为原图像素坐标，直接在原图上绘制
                for rect in subtitle_areas:
                    ymin, ymax, xmin, xmax = rect
                    cv2.rectangle(frame_ori, (xmin, ymin), (xmax, ymax), (0, 255, 0), 2)
        preview_frame = cv2.hconcat([frame_ori, frame_comp])
        # contain 适配 + 黑边由显示组件完成
        self.video_display_component.update_video_display(preview_frame, draw_selection=False)
        self.video_display_component.set_dragger_enabled(False)

    @Slot(object)
    def on_task_error(self, e):
        self.append_output(tr['SubtitleExtractorGUI']['ErrorDuringProcessing'].format(str(e)))
        if self.current_processing_task_index >= 0:
            self.task_list_component.update_task_status(self.current_processing_task_index, TaskStatus.FAILED)

    def load_video(self, video_path):
        self.video_path = video_path
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

            # 断开信号连接
            self.progress_signal.disconnect(self.update_progress)
            self.append_log_signal.disconnect(self.append_log)
            self.update_preview_with_comp_signal.disconnect(self.update_preview_with_comp)
            self.task_error_signal.disconnect(self.on_task_error)
            self.toggle_buttons_signal.disconnect(self._toggle_buttons)
            self.video_display_component.video_slider.valueChanged.disconnect(self.slider_changed)
            self.video_display_component.ab_sections_changed.disconnect(self.ab_sections_changed)
            self.video_display_component.selections_changed.disconnect(self.selections_changed)
            # 释放视频资源
            with self._video_cap_lock:
                if self.video_cap:
                    self.video_cap.release()
                    self.video_cap = None
        except Exception as e:
            print(f"Error during close window:", e)
        super().closeEvent(event)
    