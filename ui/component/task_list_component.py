import os
from pathlib import Path
from enum import Enum, unique
from dataclasses import dataclass
from functools import cached_property

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
        top.addWidget(self.pct_label)
        top.addStretch(1)
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

@dataclass
class Task:
    path: str
    name: str
    progress: int
    status: TaskStatus
    options: dict
    # 用于储存只读的输出路径, 在任务完成后设置
    _output_path: str = None

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
        
        # 创建布局
        self.__init_widgets()
        
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
        hint = QLabel("拖入单个或多个视频文件")
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
        # 覆盖相同路径的任务
        for row, task in enumerate(self.tasks[:]):
            if task.path == video_path:
                self.delete_task(row)
                continue
                
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
        row = len(self.tasks) - 1
        self.table.setRowCount(len(self.tasks))

        item0 = QTableWidgetItem(file_name)
        item0.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        item0.setToolTip(video_path)  # 设置完整路径为工具提示
        # 设置表格的文本省略模式
        self.table.setTextElideMode(Qt.ElideMiddle)

        self.table.setItem(row, 0, item0)
        # 进度·状态卡片单元格
        self.table.setRowHeight(row, 46)
        self.table.setCellWidget(row, 1, TaskProgressCell())
        cell = self.table.cellWidget(row, 1)
        cell.set_progress(0)
        cell.set_status(TaskStatus.PENDING)

        # 滚动到最新添加的行
        self.table.scrollToBottom()
        return True

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
            # 选中当前行（保持原有行为）
            self.table.selectRow(index)
    
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
                
            # 如果删除的是当前任务，重置当前任务索引
            if row == self.current_task_index:
                self.current_task_index = -1
                
            # 发出任务删除信号
            self.task_deleted.emit(row)
    
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