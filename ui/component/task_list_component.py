import os
import json
import threading
from pathlib import Path
from enum import Enum, unique
from dataclasses import dataclass
from functools import cached_property
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QMenu, QAbstractItemView,
                               QTableWidgetItem, QHeaderView, QProgressBar, QLabel, QSizePolicy)
from PySide6.QtCore import Qt, Signal, QModelIndex, QUrl
from qfluentwidgets import TableWidget, BodyLabel, FluentIcon, InfoBar, InfoBarPosition, isDarkTheme, qconfig
from PySide6.QtGui import QAction, QColor, QBrush, QDragEnterEvent, QDropEvent
from showinfm import show_in_file_manager

from backend.config import config, tr
from backend.tools.common_tools import is_image_file

@unique
class TaskStatus(Enum):
    PENDING = tr['TaskList']['Pending']
    PROCESSING = tr['TaskList']['Processing']
    COMPLETED = tr['TaskList']['Completed']
    FAILED = tr['TaskList']['Failed']


@unique
class TaskOptions(Enum):
    AB_SECTIONS = "ab_sections"
    SUB_AREAS = "sub_areas"

# 允许拖入的媒体扩展名
_DROP_EXTS = ('.mp4', '.flv', '.wmv', '.avi', '.mkv', '.mov', '.ts', '.webm',
              '.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff')

# 状态文字颜色（对齐青绿主题语义）
_STATUS_COLORS = {
    'completed': "#639922",
    'processing': "#1D9E75",
    'failed': "#e74c3c",
}

# ------------------------------------------------------------------ #
# 任务队列持久化（config/tasks.json）
# ------------------------------------------------------------------ #

#: 项目根目录（按本文件位置定位，不依赖 CWD）：ui/component/ -> 项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 任务队列持久化文件（与 config.json 同目录）
TASKS_FILE = os.path.join(_PROJECT_ROOT, 'config', 'tasks.json')

#: 持久化结构版本（不兼容变更时递增）
TASKS_VERSION = 1

#: 恢复时的状态归一表：上次运行中（PROCESSING）/失败（FAILED）均属未完成，
#: 一律恢复为待运行——安全优先，不自动开跑；分段任务点运行即走既有断点续接
_RESTORE_STATUS_MAP = {
    TaskStatus.PROCESSING: TaskStatus.PENDING,
    TaskStatus.FAILED: TaskStatus.PENDING,
}


def _options_to_json(options):
    """options 转 JSON 安全类型（range -> [start, stop]，tuple -> list）。

    ab_sections 的元素是 range 对象（视频显示组件按 .start 调整区间），
    必须转成 [start, stop] 数对才能 json.dump；非法项跳过不阻断保存。
    """
    out = {}
    if not isinstance(options, dict):
        return out
    for key, value in options.items():
        try:
            if key == TaskOptions.AB_SECTIONS.value:
                sections = []
                for r in value or []:
                    if isinstance(r, range):
                        sections.append([int(r.start), int(r.stop)])
                    elif isinstance(r, (list, tuple)) and len(r) >= 2:
                        sections.append([int(r[0]), int(r[1])])
                out[key] = sections
            elif isinstance(value, (list, tuple)):
                out[key] = [list(v) if isinstance(v, (list, tuple)) else v for v in value]
            else:
                out[key] = value
        except (TypeError, ValueError):
            continue  # 无法序列化的选项项跳过，不影响其余选项落盘
    return out


def _options_from_json(options):
    """JSON options 还原为内存结构（ab_sections -> range，sub_areas -> 元组）。"""
    out = {}
    if not isinstance(options, dict):
        return out
    for key, value in options.items():
        if key == TaskOptions.AB_SECTIONS.value and isinstance(value, list):
            sections = []
            for item in value:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    try:
                        sections.append(range(int(item[0]), int(item[1])))
                    except (TypeError, ValueError):
                        continue
            out[key] = sections
        elif key == TaskOptions.SUB_AREAS.value and isinstance(value, list):
            rects = []
            for item in value:
                if isinstance(item, (list, tuple)) and len(item) == 4:
                    try:
                        rects.append(tuple(int(v) for v in item))
                    except (TypeError, ValueError):
                        continue
            out[key] = rects
        else:
            out[key] = value
    return out


def collect_segment_info(task):
    """扫描任务输出目录下的分段 checkpoint，派生续跑信息快照。

    对接 backend/tools/segmenter.derive_resume_info（run_task_with_segments
    续跑所用的同一扫描逻辑）；无 checkpoint / 读取失败返回 None。
    checkpoint 目录推导与 run_task_with_segments 保持一致：
    <输出目录>/<视频名>_no_sub_segments/checkpoint.json
    """
    try:
        out_dir = os.path.dirname(task.output_path) or '.'
        stem = os.path.splitext(os.path.basename(task.path))[0]
        task_dir = os.path.join(out_dir, f'{stem}_no_sub_segments')
        ckpt_path = os.path.join(task_dir, 'checkpoint.json')
        if not os.path.exists(ckpt_path):
            return None
        from backend.tools.segment_executor import build_task_params
        from backend.tools.segmenter import derive_resume_info, params_hash
        # 传入当前参数哈希：与 run_task_with_segments 的续跑判定同一口径，
        # 否则参数已改时角标仍显示「可续跑」而实际运行会被拒绝，误导用户
        params = build_task_params(task.options, config.inpaintMode.value)
        params['segmentOverlapFrames'] = config.segmentOverlapFrames.value
        info = derive_resume_info(ckpt_path, params_hash(params))
        return {
            'task_dir': task_dir,
            'checkpoint': ckpt_path,
            'total_segments': info.total_segments,
            'done_segments': info.done_count,
            'first_pending_index': info.first_pending_index,
            'hash_match': info.hash_match,
            'can_resume': info.can_resume,
            'chip_text': info.chip_text,
        }
    except Exception:
        return None


def save_tasks_file(tasks, path=None):
    """任务队列原子写盘（先写 .tmp 再 os.replace），返回写入的 payload。"""
    path = path or TASKS_FILE
    payload = {
        'version': TASKS_VERSION,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'tasks': [t.to_dict() for t in tasks],
    }
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return payload


def load_tasks_file(path=None):
    """读取任务队列文件；文件损坏/字段缺失时优雅降级。

    Returns:
        (tasks, warnings): tasks 为有效 Task 列表；warnings 为降级说明
    """
    path = path or TASKS_FILE
    warnings = []
    if not os.path.exists(path):
        return [], warnings
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        return [], [f'任务队列文件损坏或无法读取（{path}）：{e}']
    if not isinstance(data, dict):
        return [], ['任务队列文件格式不正确（根节点应为对象），已忽略']
    raw = data.get('tasks')
    if not isinstance(raw, list):
        return [], ['任务队列文件缺少 tasks 列表，已忽略']
    tasks = []
    for i, item in enumerate(raw):
        try:
            tasks.append(Task.from_dict(item))
        except Exception as e:
            warnings.append(f'任务队列第 {i + 1} 条记录无效，已跳过：{e}')
    return tasks, warnings


class _ClickableLabel(QLabel):
    """可点击标签：任务队列持久化状态行（点击打开队列文件所在文件夹）"""

    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class TaskProgressCell(QWidget):
    """进度·状态 单元格：上行「百分比 + 状态」，下行进度条（方案1 卡片样式）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self.pct_label = QLabel("0%")
        self.pct_label.setStyleSheet("font-size:12px; font-family:'Consolas';")
        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size:12px;")
        self.resume_label = QLabel("")
        self.resume_label.setStyleSheet("font-size:11px; color:#A9A8A0;")
        top.addWidget(self.pct_label)
        top.addStretch(1)
        top.addWidget(self.resume_label)
        top.addWidget(self.status_label)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self._status_color = "#A9A8A0"
        self._status_text = ""
        self._apply_theme()
        qconfig.themeChanged.connect(self._apply_theme)

    def _apply_theme(self):
        track = "rgba(255,255,255,0.10)" if isDarkTheme() else "rgba(0,0,0,0.10)"
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background: {track}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: #1D9E75; border-radius: 2px; }}
        """)
        self._render_status()

    def _render_status(self):
        self.status_label.setStyleSheet(f"font-size:12px; color:{self._status_color};")

    def set_progress(self, value):
        self.bar.setValue(max(0, min(100, int(value))))
        self.pct_label.setText(f"{int(value)}%")

    def set_status(self, status: TaskStatus):
        self.status_label.setText(status.value)
        self._status_color = _STATUS_COLORS.get(status.name.lower(), "#A9A8A0")
        self._render_status()

    def set_resume(self, text, tooltip=""):
        """续跑角标（空串清除）；tooltip 挂 segment_info 的完整续跑说明"""
        self.resume_label.setText(text or "")
        self.resume_label.setToolTip(tooltip or "")
        self.resume_label.setStyleSheet(
            f"font-size:11px; color:{'#D98E04' if text else '#A9A8A0'};")

@dataclass
class Task:
    path: str
    name: str
    progress: int
    status: TaskStatus
    options: dict
    # 用于储存只读的输出路径, 在任务完成后设置
    _output_path: str = None
    # 分段续跑信息快照（checkpoint 目录/段进度，保存时刷新；非分段任务为 None）
    segment_info: dict = None

    @property
    def output_path(self):
        """获取输出路径"""
        if self._output_path is not None:
            return self._output_path
        save_directory = os.path.dirname(self.path) if not config.saveDirectory.value else config.saveDirectory.value
        if self.is_image:
            output_path = os.path.abspath(os.path.join(save_directory, f'{Path(self.path).stem}_no_sub{Path(self.path).suffix}'))
        else:
            output_path = os.path.abspath(os.path.join(save_directory, f'{Path(self.path).stem}_no_sub.mp4'))
        return output_path

    @output_path.setter
    def output_path(self, value):
        self._output_path = value

    @cached_property
    def is_image(self):
        """判断是否是图片文件"""
        return is_image_file(self.path)

    def to_dict(self):
        """序列化为 JSON 安全字典（状态用枚举名，跨语言稳定）"""
        return {
            'path': self.path,
            'name': self.name,
            'progress': int(self.progress),
            'status': self.status.name,
            'options': _options_to_json(self.options),
            'output_path': self._output_path,
            'segment_info': self.segment_info,
        }

    @classmethod
    def from_dict(cls, data):
        """从字典还原 Task；字段缺失/非法时抛 ValueError（由调用方跳过坏条目）"""
        if not isinstance(data, dict):
            raise ValueError('记录不是对象')
        path = data.get('path')
        if not isinstance(path, str) or not path.strip():
            raise ValueError('缺少有效的 path 字段')
        name = data.get('name')
        if not isinstance(name, str) or not name:
            name = os.path.basename(path)
        try:
            progress = int(data.get('progress', 0))
        except (TypeError, ValueError):
            progress = 0
        progress = max(0, min(100, progress))
        status = TaskStatus.PENDING
        status_name = data.get('status')
        if isinstance(status_name, str) and status_name in TaskStatus.__members__:
            status = TaskStatus[status_name]
        output_path = data.get('output_path')
        if not isinstance(output_path, str) or not output_path:
            output_path = None
        segment_info = data.get('segment_info')
        if not isinstance(segment_info, dict):
            segment_info = None
        return cls(path=path, name=name, progress=progress, status=status,
                   options=_options_from_json(data.get('options')),
                   _output_path=output_path, segment_info=segment_info)

class TaskListComponent(QWidget):
    """任务列表组件"""
    
    # 定义信号
    task_selected = Signal(int, str)  # 任务被选中时发出信号，参数为任务索引和视频路径
    task_deleted = Signal(int)  # 任务被删除时发出信号，参数为任务索引
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskListComponent")
        
        # 初始化变量
        self.tasks = []  # 存储任务列表
        self.current_task_index = -1  # 当前选中的任务索引
        # 持久化：写锁（GUI 线程与 worker 线程均可能触发保存）+ 上次落盘内容（去重基线）
        self._save_lock = threading.Lock()
        self._last_saved_blob = None
        self._last_save_time = None  # 最近一次成功保存的时刻（状态行显示）

        # 创建布局
        self.__init_widgets()
        self._update_queue_status()
        
    def __init_widgets(self):
        """初始化组件"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 面板标题行
        header = QHBoxLayout()
        header.setContentsMargins(10, 8, 10, 4)
        title = QLabel(tr['TaskList']['Name'])
        title.setStyleSheet("font-size:12px; font-weight:500;")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        # 任务队列持久化状态行（功能入口：点击打开队列文件所在文件夹）
        self.queue_status = _ClickableLabel('', self)
        self.queue_status.setStyleSheet(
            "color:#A9A8A0; font-size:11px; padding:0 10px 3px 10px;")
        self.queue_status.setCursor(Qt.PointingHandCursor)
        self.queue_status.clicked.connect(self._open_queue_dir)
        layout.addWidget(self.queue_status)

        # 创建表格：名称 | 进度·状态（卡片式单元格）
        self.table = TableWidget(self)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels([tr['TaskList']['Name'],
                                              f"{tr['TaskList']['Progress']} · {tr['TaskList']['Status']}"])

        # 卡片式样式：无网格、无隔行底色
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)

        # 设置列宽模式
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.Stretch)           # 名称列拉伸填充
        header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 进度·状态列自适应

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        # 连接信号
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.clicked.connect(self.on_task_clicked)

        # 支持单/多文件拖放
        self.setAcceptDrops(True)

        layout.addWidget(self.table, 1)

        # 底部拖入提示
        hint = QLabel(tr["Setting"]["DropHint"])
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color:#A9A8A0; font-size:11px; border:1px dashed #5F5E5A;"
                           "border-radius:6px; margin:6px 10px 8px 10px; padding:5px;")
        layout.addWidget(hint)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖入校验：只接受包含媒体文件的拖放"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(_DROP_EXTS):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        """拖放落地：逐个入队，选中最后一个新任务"""
        added = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(_DROP_EXTS) and os.path.isfile(path):
                self.add_task(path)
                added.append(path)
        if added:
            index = self.find_task_index_by_path(added[-1])
            self.select_task(max(0, index))
        event.acceptProposedAction()
        
    def add_task(self, video_path):
        """添加任务到列表
        
        Args:
            video_path: 视频文件路径
        """
        # 覆盖相同路径的任务（先收集全部匹配行再倒序删除：
        # 边删边遍历会使后续 row 前移错位——误删无关任务或漏删形成重复）
        dup_rows = [row for row, task in enumerate(self.tasks) if task.path == video_path]
        for row in reversed(dup_rows):
            self.delete_task(row)
                
        # 获取文件名
        file_name = os.path.basename(video_path)
        
        # 添加到任务列表
        task = Task(
            path=video_path,
            name=file_name,
            progress=0,
            status=TaskStatus.PENDING,
            options={},
        )
        self.tasks.append(task)

        # 更新表格
        self._add_task_row(task)

        # 滚动到最新添加的行
        self.table.scrollToBottom()
        self.save_tasks()
        return True

    def _add_task_row(self, task):
        """为 self.tasks 末行的任务创建表格行（add_task 与持久化恢复共用）"""
        row = len(self.tasks) - 1
        self.table.setRowCount(len(self.tasks))

        item0 = QTableWidgetItem(task.name)
        item0.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        item0.setToolTip(task.path)  # 设置完整路径为工具提示
        # 设置表格的文本省略模式
        self.table.setTextElideMode(Qt.ElideMiddle)

        self.table.setItem(row, 0, item0)
        # 进度·状态卡片单元格
        self.table.setRowHeight(row, 46)
        self.table.setCellWidget(row, 1, TaskProgressCell())
        cell = self.table.cellWidget(row, 1)
        cell.set_progress(task.progress)
        cell.set_status(task.status)
        # 续跑角标：分段任务且有可续跑 checkpoint 时显示短文本，完整说明挂 tooltip
        info = getattr(task, 'segment_info', None)
        if info and info.get('can_resume') and info.get('total_segments'):
            cell.set_resume(
                tr['TaskList']['QueueResumeChip'].format(
                    info.get('done_segments', 0), info.get('total_segments', 0)),
                info.get('chip_text', ''))

    def update_task_progress(self, index, progress):
        """更新任务进度

        Args:
            index: 任务索引
            progress: 进度值(0-100)
        """
        if 0 <= index < len(self.tasks):
            self.tasks[index].progress = progress

            # 更新进度单元格
            cell = self.table.cellWidget(index, 1)
            if cell:
                cell.set_progress(progress)

            # 如果是当前处理的任务，滚动到可见区域
            if index == self.current_task_index:
                self.table.scrollTo(self.table.model().index(index, 0))

    def update_task_status(self, index, status):
        """更新任务状态

        Args:
            index: 任务索引
            status: 任务状态
        """
        if 0 <= index < len(self.tasks):
            self.tasks[index].status = status
            cell = self.table.cellWidget(index, 1)
            if cell:
                cell.set_status(status)
                # 续跑角标随状态刷新：仅待处理态保留（处理中/已完成/失败均清除）
                info = self.tasks[index].segment_info
                if (status == TaskStatus.PENDING and info
                        and info.get('can_resume') and info.get('total_segments')):
                    cell.set_resume(
                        tr['TaskList']['QueueResumeChip'].format(
                            info.get('done_segments', 0), info.get('total_segments', 0)),
                        info.get('chip_text', ''))
                else:
                    cell.set_resume('')
            # 选中当前行（保持原有行为）
            self.table.selectRow(index)
            self.save_tasks()
    
    def get_pending_tasks(self):
        """获取所有待处理的任务
        
        Returns:
            list: 待处理任务列表，每项为 (索引, 任务) 元组
        """
        return [(i, task) for i, task in enumerate(self.tasks) if task.status == TaskStatus.PENDING]
    
    def get_all_tasks(self):
        """获取所有任务
        
        Returns:
            list: 所有任务列表
        """
        return self.tasks

    def get_task(self, index):
        """获取指定索引的任务

        Args:
            index: 任务索引

        Returns:
            Task: 任务对象
        """
        if 0 <= index < len(self.tasks):
            return self.tasks[index]
        return None
    
    def find_task_index_by_path(self, path):
        tasks = self.get_all_tasks()
        for idx, task in enumerate(tasks):
            if task.path == path:
                return idx
        return -1  # 没找到返回-1
        
    def show_context_menu(self, pos):
        """显示右键菜单
        
        Args:
            pos: 鼠标位置
        """
        index = self.table.indexAt(pos)
        if index.isValid():
            menu = QMenu(self)
            
            # 打开视频文件位置
            open_video_location_action = QAction(tr['TaskList']['OpenSourceVideoLocation'], self)
            open_video_location_action.triggered.connect(lambda: self.open_file_location(self.tasks[index.row()].path))
            menu.addAction(open_video_location_action)
            
            # 打开目标文件位置
            def open_target_location():
                task = self.tasks[index.row()]
                path = task.output_path
                if task.status != TaskStatus.COMPLETED:
                    InfoBar.warning(
                        title=tr['TaskList']['Warning'],
                        content=tr['TaskList']['TargetFileNotFound'],
                        parent=self.get_root_parent(),
                        duration=3000
                    )
                    return
                self.open_file_location(path)
            open_target_location_action = QAction(tr['TaskList']['OpenTargetVideoLocation'], self)
            open_target_location_action.triggered.connect(open_target_location)
            menu.addAction(open_target_location_action)

            reset_task_status_action = QAction(tr['TaskList']['ResetTaskStatus'], self)
            reset_task_status_action.triggered.connect((lambda: (
                    self.update_task_status(index.row(), TaskStatus.PENDING), 
                    self.update_task_progress(index.row(), 0)
                )
            ))
            menu.addAction(reset_task_status_action)
            
            # 删除任务
            delete_action = QAction(tr['TaskList']['DeleteTask'], self)
            delete_action.triggered.connect(lambda: self.delete_task(index.row()))
            menu.addAction(delete_action)
            
            # 显示菜单
            menu.exec_(self.table.viewport().mapToGlobal(pos))
    
    def delete_task(self, row):
        """删除任务
        
        Args:
            row: 行索引
        """
        if 0 <= row < len(self.tasks):
            # 从列表中删除
            del self.tasks[row]
            
            # 从表格中删除
            self.table.removeRow(row)
                
            # 删除当前任务则重置；删除其前的任务会使当前索引前移，
            # 否则后续进度/状态/输出路径会写到错误的任务上
            if row == self.current_task_index:
                self.current_task_index = -1
            elif 0 <= self.current_task_index and row < self.current_task_index:
                self.current_task_index -= 1
                
            # 发出任务删除信号
            self.task_deleted.emit(row)
            self.save_tasks()
    
    def on_task_clicked(self, index):
        """任务被点击时的处理
        
        Args:
            index: 索引
        """
        row = index.row()
        if 0 <= row < len(self.tasks):
            self.current_task_index = row
            # 发出信号，通知外部加载对应视频
            self.task_selected.emit(row, self.tasks[row].path)
            
    def set_current_task(self, index):
        """设置当前处理的任务
        
        Args:
            index: 任务索引
        """
        if 0 <= index < len(self.tasks):
            self.current_task_index = index
            self.table.selectRow(index)
            self.table.scrollTo(self.table.model().index(index, 0))
        
    def get_current_task_index(self):
        """获取当前处理的任务索引

        Returns:
            int: 任务索引
        """
        return self.current_task_index
            
    def select_task(self, index):
        """选中指定任务
        
        Args:
            index: 任务索引
        """
        self.set_current_task(index)
        if 0 <= index < len(self.tasks):
            self.task_selected.emit(index, self.tasks[index].path)

    def open_file_location(self, path):
        """打开文件所在位置
        
        Args:
            row: 行索引
            path: 目标路径
        """                
        # 检查视频文件是否存在
        if not os.path.exists(path):
            InfoBar.warning(
                title=tr['TaskList']['Warning'],
                content=tr['TaskList']['UnableToLocateFile'],
                parent=self.get_root_parent(),
                duration=3000
            )
            return
            
        show_in_file_manager(os.path.abspath(path))

    def get_root_parent(self):
        parent = self
        while parent.parent():
            parent = parent.parent()
        return parent

    def update_task_option(self, index, task_option: TaskOptions, value):
        """更新任务选项

        Args:
            index: 任务索引
            task_option: 选项名
            value: 选项值
        """
        if 0 <= index < len(self.tasks):
            self.tasks[index].options[task_option.value] = value
            self.save_tasks()

    def get_task_option(self, index, task_option: TaskOptions, default=None):
        """获取任务选项
        Args:
            index: 任务索引
            task_option: 选项名
            default: 默认值
        Returns:
            选项值
        """
        if 0 <= index < len(self.tasks):
            return self.tasks[index].options.get(task_option.value, default)

    # ------------------------------------------------------------------ #
    # 任务队列持久化（config/tasks.json）
    # ------------------------------------------------------------------ #

    def save_tasks(self, path=None):
        """持久化任务队列到 config/tasks.json。

        内容去重（无变更不落盘）+ 原子写（.tmp + os.replace）；
        保存前按盘上 checkpoint 刷新分段续跑信息；失败仅打印警告不崩。
        """
        with self._save_lock:
            try:
                # 分段续跑信息以盘上 checkpoint 为准刷新（无 checkpoint 的任务为 None）
                for task in self.tasks:
                    task.segment_info = collect_segment_info(task)
                blob = json.dumps([t.to_dict() for t in self.tasks],
                                  ensure_ascii=False, sort_keys=True)
                # 去重基线含路径：同内容同路径才跳过（测试/多路径场景不误跳过）
                key = (os.path.abspath(path or TASKS_FILE), blob)
                if key == self._last_saved_blob:
                    return False
                save_tasks_file(self.tasks, path)
                self._last_saved_blob = key
                self._last_save_time = datetime.now().strftime('%H:%M:%S')
                self._update_queue_status()
                return True
            except Exception as e:
                print(f'[任务持久化] 保存失败: {e}')
                return False

    def _update_queue_status(self):
        """刷新任务队列持久化状态行：自动保存说明 + 任务/可续跑计数 + 最近保存时刻"""
        tl = tr['TaskList']
        n = len(self.tasks)
        resumable = sum(1 for t in self.tasks
                        if t.segment_info and t.segment_info.get('can_resume'))
        text = tl['QueueAutoSave']
        if n:
            text += ' · ' + tl['QueueTasksCount'].format(n)
            if resumable:
                text += ' · ' + tl['QueueResumeCount'].format(resumable)
            if self._last_save_time:
                text += ' · ' + tl['QueueSavedAt'].format(self._last_save_time)
        self.queue_status.setText(text)
        self.queue_status.setToolTip(tl['QueueOpenTip'])

    def _open_queue_dir(self):
        """状态行入口：打开任务队列文件（config/tasks.json）所在文件夹"""
        try:
            show_in_file_manager(os.path.dirname(TASKS_FILE))
        except Exception as e:
            print(f'[任务持久化] 打开文件夹失败: {e}')

    def load_tasks(self, path=None, logger=None):
        """启动时从 config/tasks.json 恢复任务队列。

        - 上次运行中（PROCESSING）/失败（FAILED）的任务恢复为待运行态，
          不自动开跑（安全优先；分段任务点运行即走既有断点续接）；
        - 分段任务的续跑信息对接 segmenter.derive_resume_info 刷新；
        - 文件损坏/字段缺失时逐条降级跳过，不崩。
        """
        tasks, warnings = load_tasks_file(path)
        for w in warnings:
            self._log_restore(logger, f'[任务恢复] {w}')
        restored = 0
        resumable = 0
        for task in tasks:
            task.status = _RESTORE_STATUS_MAP.get(task.status, task.status)
            task.progress = 100 if task.status == TaskStatus.COMPLETED else 0
            # 分段续跑信息：以盘上 checkpoint 为准刷新（段进度/可续跑性）
            info = collect_segment_info(task)
            if info is not None:
                task.segment_info = info
            if not os.path.isfile(task.path):
                self._log_restore(logger, f'[任务恢复] {task.name} → {task.status.value}'
                                          f'（源文件不存在，可能已被移动或删除）')
            else:
                msg = f'[任务恢复] {task.name} → {task.status.value}'
                if info is not None and info.get('chip_text'):
                    msg += f' · {info["chip_text"]}'
                    if info.get('can_resume'):
                        resumable += 1
                self._log_restore(logger, msg)
            self.tasks.append(task)
            self._add_task_row(task)
            restored += 1
        if restored:
            self._log_restore(logger, f'[任务恢复] 共恢复 {restored} 个任务'
                                      f'（{resumable} 个分段任务可断点续跑）；'
                                      f'不会自动开始运行，点击「开始处理」后启动')
        # 以载入内容为去重基线：无变更时不重复落盘
        self._last_saved_blob = (os.path.abspath(path or TASKS_FILE),
                                 json.dumps([t.to_dict() for t in self.tasks],
                                            ensure_ascii=False, sort_keys=True))
        self._update_queue_status()
        return restored

    def set_task_output_path(self, index, output_path):
        """设置任务输出路径（任务完成后落库并触发持久化）"""
        if 0 <= index < len(self.tasks):
            self.tasks[index].output_path = output_path
            self.save_tasks()

    @staticmethod
    def _log_restore(logger, message):
        """恢复日志输出（无 logger 时退化为 print）"""
        if logger is not None:
            try:
                logger(message)
                return
            except Exception:
                pass
        print(message)