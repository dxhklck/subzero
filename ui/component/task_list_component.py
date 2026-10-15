import os
import json
import time
import shutil
from pathlib import Path
from enum import Enum, unique
from dataclasses import dataclass
from functools import cached_property
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QMenu, QAbstractItemView,
                               QTableWidgetItem, QHeaderView, QProgressBar, QLabel, QSizePolicy,
                               QFileDialog)
from PySide6.QtCore import Qt, Signal, QModelIndex, QUrl, QTimer, QRect, QPoint
from qfluentwidgets import TableWidget, BodyLabel, FluentIcon, InfoBar, InfoBarPosition, isDarkTheme, qconfig, MessageBox, ToolButton
from PySide6.QtGui import QAction, QColor, QBrush, QDragEnterEvent, QDropEvent, QPainter, QPen
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


def resolve_output_path(video_path: str) -> str:
    """解析任务输出路径；同名输出已存在时不覆盖，追加序号另存。

    背景：上次运行的输出可能仍被残留的 python 子进程占用（Windows 下无法
    覆盖正被打开的文件导致本次任务失败），且直接覆盖会销毁上次的成果。
    规则：
      <stem>_no_sub.mp4        首选（同名不存在时）
      <stem>_no_sub_2.mp4 ...  已存在则序号依次递增
    """
    save_directory = (os.path.dirname(video_path)
                      if not config.saveDirectory.value else config.saveDirectory.value)
    stem = Path(video_path).stem
    if is_image_file(video_path):
        base = os.path.abspath(os.path.join(
            save_directory, f'{stem}_no_sub{Path(video_path).suffix}'))
    else:
        base = os.path.abspath(os.path.join(save_directory, f'{stem}_no_sub.mp4'))
    if not os.path.exists(base):
        return base
    root, ext = os.path.splitext(base)
    n = 2
    while os.path.exists(f'{root}_{n}{ext}'):
        n += 1
    return f'{root}_{n}{ext}'

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
# 任务队列手动快照管理（快照文件格式与旧 config/tasks.json 完全兼容）
# ------------------------------------------------------------------ #

#: 项目根目录（按本文件位置定位，不依赖 CWD）：ui/component/ -> 项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 旧任务队列持久化文件（v1.x 自动保存/自动恢复已移除；存量文件仍可手动加载）
TASKS_FILE = os.path.join(_PROJECT_ROOT, 'config', 'tasks.json')

#: 快照默认保存目录（保存/加载对话框的起始位置）
SNAPSHOTS_DIR = os.path.join(_PROJECT_ROOT, 'config', 'snapshots')

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
    """进度·状态 单元格：上行「百分比 + 时间 + 状态」，下行进度条（卡片样式）

    内容变化时发 content_changed 信号——外层据此对「进度·状态」列
    resizeColumnToContents，列宽始终贴合内容（时间文本变长不被截断）。
    """

    content_changed = Signal()

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
        self.time_label = QLabel("")
        self.time_label.setStyleSheet("font-size:11px; font-family:'Consolas';")
        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size:12px;")
        self.resume_label = QLabel("")
        self.resume_label.setStyleSheet("font-size:11px; color:#A9A8A0;")
        top.addWidget(self.pct_label)
        top.addWidget(self.time_label)
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
        self._time_text = ""
        self._pct_text = "0%"
        self._apply_theme()
        qconfig.themeChanged.connect(self._apply_theme)

    def _apply_theme(self):
        track = "rgba(255,255,255,0.10)" if isDarkTheme() else "rgba(0,0,0,0.10)"
        # 时间标签颜色随主题：暗色用亮灰，浅色用深灰，两种主题下都可读
        time_color = "#C9C8C0" if isDarkTheme() else "#6B6A63"
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background: {track}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: #1D9E75; border-radius: 2px; }}
        """)
        self.time_label.setStyleSheet(
            f"font-size:11px; color:{time_color}; font-family:'Consolas';")
        self._render_status()

    def _render_status(self):
        self.status_label.setStyleSheet(f"font-size:12px; color:{self._status_color};")

    def set_progress(self, value):
        self.bar.setValue(max(0, min(100, int(value))))
        text = f"{int(value)}%"
        if text != self._pct_text:
            self._pct_text = text
            self.pct_label.setText(text)
            self.content_changed.emit()

    def set_status(self, status: TaskStatus):
        if self._status_text != status.value:
            self._status_text = status.value
            self.status_label.setText(status.value)
            self._status_color = _STATUS_COLORS.get(status.name.lower(), "#A9A8A0")
            self._render_status()
            self.content_changed.emit()

    def set_resume(self, text, tooltip=""):
        """续跑角标（空串清除）；tooltip 挂 segment_info 的完整续跑说明"""
        self.resume_label.setText(text or "")
        self.resume_label.setToolTip(tooltip or "")
        self.resume_label.setStyleSheet(
            f"font-size:11px; color:{'#D98E04' if text else '#A9A8A0'};")

    def set_time(self, text):
        """时间标签（空串清除）：预计 ~mm:ss / 运行 mm:ss/预计 mm:ss / 用时 mm:ss"""
        text = text or ""
        if text != self._time_text:
            self._time_text = text
            self.time_label.setText(text)
            self.time_label.setToolTip(text)
            self.content_changed.emit()

class _TaskTable(TableWidget):
    """任务列表表格：支持行拖拽排序（自实现）。

    Qt 的 InternalMove 只移动 item 不同步 tasks 列表和 cellWidget，不可用；
    这里手动跟踪拖拽：按住左键移动 ≥12px 进入拖拽态，指示线画在目标行的
    上/下边缘（光标位于目标行上半 → 插到其前，下半 → 插到其后），松开时发
    row_move_requested(from_row, insert_index) 由组件同步 tasks 列表并重建。

    can_drag 回调为 False 时（如存在处理中任务——索引被运行占用，重排会错位）
    完全禁用拖拽，鼠标行为与普通表格一致。
    """
    row_move_requested = Signal(int, int)  # (from_row, insert_index)

    _DRAG_THRESHOLD = 12  # manhattan 距离阈值，防误触
    _INDICATOR_COLOR = '#1D9E75'

    def __init__(self, parent=None, can_drag=None):
        super().__init__(parent)
        self._can_drag = can_drag or (lambda: False)
        self._press_row = None
        self._press_pos = None
        self._dragging = False
        self._marker_row = None   # 指示线所在目标行
        self._marker_after = False  # True=插到目标行之后
        self._ghost = None        # 拖动跟随的行幽灵（顶层半透明快照）
        self._ghost_offset = None  # 光标相对行左上角的偏移

    # ---- 拖拽状态机 ----

    def mousePressEvent(self, event):
        self._reset_drag()
        # 重排消歧（多选共存）：仅「无修饰键 + 按下已选中的唯一行」才进入
        # 重排状态机；其余按压（Ctrl/Shift 点选、未选中行/空白处拖拽框选）
        # 交回 Qt 默认行为（范围选择/橡皮筋框选）。语义：先点选要移动的任务，
        # 再拖它重排；未选中行上直接拖 = 框选。
        if (event.button() == Qt.LeftButton and self._can_drag()
                and not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier
                                              | Qt.AltModifier))):
            sel = self.selectionModel().selectedRows() if self.selectionModel() else []
            if len(sel) == 1:
                index = self.indexAt(event.position().toPoint())
                if index.isValid() and index.row() == sel[0].row():
                    self._press_row = index.row()
                    self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_row is not None and (event.buttons() & Qt.LeftButton):
            pos = event.position().toPoint()
            if not self._dragging:
                if (pos - self._press_pos).manhattanLength() >= self._DRAG_THRESHOLD:
                    self._dragging = True
                    self._start_ghost(pos)
                    self.viewport().setCursor(Qt.ClosedHandCursor)
            if self._dragging:
                self._update_marker(pos)
                self._move_ghost(pos)
                self.viewport().update()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and self._press_row is not None:
            # ⚠ 必须先取 from_row 再复位——复位会把 _press_row 清成 None，
            # 发 Signal(int,int) 时 None 被静默转 0 → 永远移动第 0 行
            # （「往后可以往前不行」的根因）
            from_row = self._press_row
            insert = self._insert_index()
            self._reset_drag()
            if insert is not None:
                self.row_move_requested.emit(from_row, insert)
                event.accept()
                return
        self._reset_drag()
        super().mouseReleaseEvent(event)

    def _update_marker(self, pos):
        """按光标位置更新指示线：行上半=该行之前，下半=该行之后；表格外贴边"""
        row = self.rowAt(pos.y())  # ⚠ QTableView.rowAt 收 y，非 QPoint
        if row < 0:
            if pos.y() < 0:
                row, after = 0, False
            else:
                row, after = max(0, self.rowCount() - 1), True
        else:
            rect = self.visualRect(self.model().index(row, 0))
            after = pos.y() > rect.center().y()
        self._marker_row, self._marker_after = row, after

    def _insert_index(self):
        """指示线边界 → 移除源行后的列表插入下标；原位返回 None"""
        if self._marker_row is None or self._press_row is None:
            return None
        idx = self._marker_row + 1 if self._marker_after else self._marker_row
        if self._press_row < idx:
            idx -= 1
        if idx == self._press_row:
            return None
        return idx

    def _reset_drag(self):
        changed = self._dragging or self._marker_row is not None
        self._press_row = None
        self._press_pos = None
        self._dragging = False
        self._marker_row = None
        self._marker_after = False
        self._close_ghost()
        self.viewport().unsetCursor()
        if changed:
            self.viewport().update()

    # ---- 拖动跟随（行幽灵快照）----

    def _start_ghost(self, pos):
        """拖拽开始：抓取源行快照，做成跟随光标的顶层半透明浮窗"""
        try:
            row_rect = self.visualRect(self.model().index(self._press_row, 0))
            full = QRect(0, row_rect.top(), self.viewport().width(),
                         row_rect.height())
            self._ghost_offset = QPoint(pos.x(), pos.y() - full.top())
            pix = self.viewport().grab(full)
            ghost = QLabel(self, Qt.ToolTip | Qt.FramelessWindowHint)
            ghost.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            ghost.setAttribute(Qt.WA_ShowWithoutActivating, True)
            ghost.setWindowOpacity(0.85)
            ghost.setPixmap(pix)
            ghost.move(self.viewport().mapToGlobal(
                QPoint(pos.x() - self._ghost_offset.x(), full.top())))
            ghost.show()
            self._ghost = ghost
        except Exception:
            self._ghost = None  # 快照失败不影响拖拽功能本身

    def _move_ghost(self, pos):
        """幽灵跟随光标（保持按下时相对行的偏移）"""
        if self._ghost is None or self._ghost_offset is None:
            return
        top_left = self.viewport().mapToGlobal(pos) - self._ghost_offset
        self._ghost.move(top_left)

    def _close_ghost(self):
        if self._ghost is not None:
            try:
                self._ghost.close()
                self._ghost.deleteLater()
            except RuntimeError:
                pass
            self._ghost = None
            self._ghost_offset = None

    # ---- 指示线绘制 ----

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._dragging and self._marker_row is not None):
            return
        index = self.model().index(self._marker_row, 0)
        rect = self.visualRect(index)
        if not rect.isValid():
            return
        painter = QPainter(self.viewport())
        pen = QPen(QColor(self._INDICATOR_COLOR), 3)
        painter.setPen(pen)
        y = rect.bottom() if self._marker_after else rect.top()
        painter.drawLine(rect.left(), y, rect.right(), y)
        # 端点圆点：指示线更醒目
        painter.setBrush(QColor(self._INDICATOR_COLOR))
        painter.drawEllipse(rect.left() + 1, y - 3, 6, 6)
        painter.drawEllipse(rect.right() - 7, y - 3, 6, 6)
        painter.end()


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
    # 计时：started_at=本次开始处理时刻（epoch 秒，进入 PROCESSING 时记录）；
    # elapsed=最终用时（完成/失败时定格并持久化；重置任务时清零）
    started_at: float = None
    elapsed: float = 0.0

    @cached_property
    def frame_count(self):
        """视频总帧数（预估处理时长用）；读取失败/图片任务返回 None"""
        if self.is_image:
            return None
        try:
            import cv2
            cap = cv2.VideoCapture(self.path)
            try:
                n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            finally:
                cap.release()
            return n if n > 0 else None
        except Exception:
            return None

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
            'elapsed': round(float(self.elapsed), 1) if self.elapsed else 0.0,
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
        try:
            elapsed = float(data.get('elapsed', 0.0))
        except (TypeError, ValueError):
            elapsed = 0.0
        return cls(path=path, name=name, progress=progress, status=status,
                   options=_options_from_json(data.get('options')),
                   _output_path=output_path, segment_info=segment_info,
                   elapsed=max(0.0, elapsed))

class TaskListComponent(QWidget):
    """任务列表组件"""
    
    # 定义信号
    task_selected = Signal(int, str)  # 任务被选中时发出信号，参数为任务索引和视频路径
    task_deleted = Signal(int)  # 任务被删除时发出信号，参数为任务索引
    task_moved = Signal(int, int)  # 拖拽重排：任务从 from_row 移动到 insert_index
    snapshot_log = Signal(str)  # 快照加载过程日志（GUI 线程接到监控输出）
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskListComponent")
        
        # 初始化变量
        self.tasks = []  # 存储任务列表
        self.current_task_index = -1  # 当前选中的任务索引
        # 快照管理：最近一次手动保存快照的时刻（状态行显示）
        self._last_snapshot_time = None

        # 计时/预估：历史处理速度 EMA（秒/帧，按完成任务回填）+ 秒级刷新定时器
        self._sec_per_frame_ema = 0.0
        self._time_timer = QTimer(self)
        self._time_timer.setInterval(1000)
        self._time_timer.timeout.connect(self._tick_time_labels)
        self._time_timer.start()

        # 创建布局
        self.__init_widgets()
        self._update_queue_status()
        
    def __init_widgets(self):
        """初始化组件"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 面板标题行：标题 + 手动快照按钮（保存/加载）
        header = QHBoxLayout()
        header.setContentsMargins(10, 8, 10, 4)
        title = QLabel(tr['TaskList']['Name'])
        title.setStyleSheet("font-size:12px; font-weight:500;")
        header.addWidget(title)
        header.addStretch(1)

        self.snapshot_save_btn = ToolButton(FluentIcon.SAVE, self)
        self.snapshot_save_btn.setToolTip(tr['TaskList']['SnapshotSaveTip'])
        self.snapshot_save_btn.clicked.connect(self.save_snapshot_dialog)
        header.addWidget(self.snapshot_save_btn)

        self.snapshot_load_btn = ToolButton(FluentIcon.FOLDER, self)
        self.snapshot_load_btn.setToolTip(tr['TaskList']['SnapshotLoadTip'])
        self.snapshot_load_btn.clicked.connect(self.load_snapshot_dialog)
        header.addWidget(self.snapshot_load_btn)
        layout.addLayout(header)

        # 任务队列持久化状态行（功能入口：点击打开队列文件所在文件夹）
        self.queue_status = _ClickableLabel('', self)
        self.queue_status.setStyleSheet(
            "color:#A9A8A0; font-size:11px; padding:0 10px 3px 10px;")
        self.queue_status.setCursor(Qt.PointingHandCursor)
        self.queue_status.clicked.connect(self._open_queue_dir)
        layout.addWidget(self.queue_status)

        # 创建表格：名称 | 进度·状态（卡片式单元格）；支持行拖拽排序
        self.table = _TaskTable(
            self,
            # 有任务处理中时禁止拖拽：运行中任务的索引被 worker 占用，重排会错位
            can_drag=lambda: not any(t.status == TaskStatus.PROCESSING
                                     for t in self.tasks))
        self.table.row_move_requested.connect(self.move_task)
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
        # 多选：Ctrl/Shift 点选 + 未选中行/空白处拖拽扫选行区间（QTableView 原生
        # ExtendedSelection 行为）。与行拖拽重排的冲突在 _TaskTable.mousePressEvent
        # 消歧：仅「无修饰键按下已选中的唯一行」才重排，其余交回 Qt 扫选
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        # 卡片列表形态（去二维表格感）：隐藏行号列与表头——
        # 列语义由单元格自解释（名称/百分比+状态字），列宽模式仍由 header 配置控制
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setVisible(False)

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

    def _add_task_row(self, task, row=None):
        """为任务创建表格行（add_task/持久化恢复追加末行；move_task 重建时显式传 row）

        ⚠ row 缺省=末行（len(tasks)-1），只对「任务已 append 到列表末尾」的
        调用方正确；整表重建必须逐行显式传 row，否则全部写进最后一行。
        """
        if row is None:
            row = len(self.tasks) - 1
        if self.table.rowCount() <= row:
            self.table.setRowCount(row + 1)

        item0 = QTableWidgetItem(task.name)
        item0.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        item0.setToolTip(task.path)  # 设置完整路径为工具提示
        # 设置表格的文本省略模式
        self.table.setTextElideMode(Qt.ElideMiddle)

        self.table.setItem(row, 0, item0)
        # 进度·状态卡片单元格
        self.table.setRowHeight(row, 46)
        cell = TaskProgressCell()
        # 百分比/时间/状态任一内容变化 → 列宽贴合内容自适应（时间文本变长不被截断）
        cell.content_changed.connect(self._fit_progress_column)
        self.table.setCellWidget(row, 1, cell)
        cell.set_progress(task.progress)
        cell.set_status(task.status)
        # 续跑角标：分段任务且有可续跑 checkpoint 时显示短文本，完整说明挂 tooltip
        info = getattr(task, 'segment_info', None)
        if info and info.get('can_resume') and info.get('total_segments'):
            cell.set_resume(
                tr['TaskList']['QueueResumeChip'].format(
                    info.get('done_segments', 0), info.get('total_segments', 0)),
                info.get('chip_text', ''))
        elif (info and info.get('total_segments')
                and not info.get('hash_match', True)):
            # 参数已变更：旧 checkpoint 与当前参数不一致，续跑必被拒——
            # 显示「参数已变」角标引导一键清理（右键菜单）
            cell.set_resume(tr['TaskList']['QueueParamChangedChip'],
                            info.get('chip_text', ''))
        # 时间标签：历史用时（完成/失败）或预估总时长（待处理，有速度模型时）
        self._refresh_time_label(len(self.tasks) - 1)

    def _fit_progress_column(self, *args):
        """「进度·状态」列宽贴合内容自适应（单元格 content_changed 触发）。

        延迟到事件循环：QLabel 文本刚变时布局 sizeHint 需要一轮重算。
        列 0（名称）是 Stretch 模式，本列变宽时名称列自动让位。
        """
        QTimer.singleShot(0, lambda: self.table.resizeColumnToContents(1))

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
            task = self.tasks[index]
            task.status = status
            # 计时：进入 PROCESSING 记起点；完成/失败定格用时；
            # 回到 PENDING（重置/停止）清零重计
            if status == TaskStatus.PROCESSING:
                task.started_at = time.time()
                task.elapsed = 0.0
            elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                if task.started_at is not None:
                    task.elapsed = max(0.0, time.time() - task.started_at)
                    task.started_at = None
                # 速度模型回填：完成任务按 秒/帧 更新 EMA，并刷新待处理任务预估
                if status == TaskStatus.COMPLETED:
                    self._update_speed_model(task)
            else:  # PENDING
                task.started_at = None
                task.elapsed = 0.0
            cell = self.table.cellWidget(index, 1)
            if cell:
                cell.set_status(status)
                # 续跑角标随状态刷新：仅待处理态保留（处理中/已完成/失败均清除）
                info = task.segment_info
                if (status == TaskStatus.PENDING and info
                        and info.get('can_resume') and info.get('total_segments')):
                    cell.set_resume(
                        tr['TaskList']['QueueResumeChip'].format(
                            info.get('done_segments', 0), info.get('total_segments', 0)),
                        info.get('chip_text', ''))
                elif (status == TaskStatus.PENDING and info
                        and info.get('total_segments')
                        and not info.get('hash_match', True)):
                    cell.set_resume(tr['TaskList']['QueueParamChangedChip'],
                                    info.get('chip_text', ''))
                else:
                    cell.set_resume('')
                self._refresh_time_label(index)
            # 选中当前行（保持原有行为）
            self.table.selectRow(index)
            self.save_tasks()
            # 完成后队列速度模型变化 → 其余待处理任务的预估随之刷新
            if status == TaskStatus.COMPLETED:
                self._refresh_pending_estimates()
    
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
        
    def _selected_rows(self):
        """当前多选的行号（升序）；无选择返回空列表"""
        if self.table.selectionModel() is None:
            return []
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def show_context_menu(self, pos):
        """显示右键菜单：多选 → 批量操作；单选/未命中行 → 原有单项操作

        Args:
            pos: 鼠标位置
        """
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        rows = self._selected_rows()
        if index.row() not in rows:
            rows = [index.row()]
        if len(rows) >= 2:
            self._show_batch_menu(rows, pos)
        else:
            self._show_single_menu(index.row(), pos)

    def _show_batch_menu(self, rows, pos):
        """批量操作菜单：打开源文件位置 / 重置状态 / 删除（带确认）"""
        tl = tr['TaskList']
        menu = QMenu(self)

        open_video_location_action = QAction(tl['OpenSourceVideoLocation'], self)
        open_video_location_action.triggered.connect(
            lambda: [self.open_file_location(self.tasks[r].path) for r in rows])
        menu.addAction(open_video_location_action)

        reset_action = QAction(tl['BatchReset'].format(len(rows)), self)
        reset_action.triggered.connect(
            lambda: [self._reset_task(r) for r in rows])
        menu.addAction(reset_action)

        menu.addSeparator()
        delete_action = QAction(tl['BatchDelete'].format(len(rows)), self)
        delete_action.triggered.connect(lambda: self.delete_tasks(rows))
        menu.addAction(delete_action)

        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _show_single_menu(self, row, pos):
        """单项右键菜单（原有逻辑，行为不变）"""
        menu = QMenu(self)

        # 打开视频文件位置
        open_video_location_action = QAction(tr['TaskList']['OpenSourceVideoLocation'], self)
        open_video_location_action.triggered.connect(lambda: self.open_file_location(self.tasks[row].path))
        menu.addAction(open_video_location_action)

        # 打开目标文件位置
        def open_target_location():
            task = self.tasks[row]
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
        reset_task_status_action.triggered.connect(lambda: self._reset_task(row))
        menu.addAction(reset_task_status_action)

        # 失败任务：直达错误现场（崩溃报告文件/输出目录）
        if self.tasks[row].status == TaskStatus.FAILED:
            report = self._find_crash_report(self.tasks[row])
            if report is not None:
                err_action = QAction(tr['TaskList']['OpenErrorLogLocation'], self)
                err_action.triggered.connect(
                    lambda checked, p=report: self.open_file_location(p))
                menu.addAction(err_action)

        # 删除旧分段进度（参数变更被拒续跑 / 手动清理）：列出目录并二次确认
        info = self.tasks[row].segment_info
        task_dir = (info or {}).get('task_dir')
        if task_dir and os.path.isdir(task_dir):
            delete_checkpoint_action = QAction(
                tr['TaskList']['DeleteCheckpointAction'], self)
            delete_checkpoint_action.triggered.connect(
                lambda: self._delete_checkpoint_and_reset(row))
            menu.addAction(delete_checkpoint_action)

        # 删除任务
        delete_action = QAction(tr['TaskList']['DeleteTask'], self)
        delete_action.triggered.connect(lambda: self.delete_task(row))
        menu.addAction(delete_action)

        # 显示菜单
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _reset_task(self, row):
        """重置单个任务状态与进度（单项菜单与批量重置共用）"""
        self.update_task_status(row, TaskStatus.PENDING)
        self.update_task_progress(row, 0)

    def delete_tasks(self, rows):
        """批量删除任务（倒序逐行删除保证索引不错位；>1 行先二次确认）"""
        tl = tr['TaskList']
        rows = sorted({r for r in rows if 0 <= r < len(self.tasks)}, reverse=True)
        if not rows:
            return
        if len(rows) > 1:
            box = MessageBox(tl['BatchDeleteTitle'],
                             tl['BatchDeleteBody'].format(len(rows)),
                             self.get_root_parent())
            if not box.exec():
                return
        for row in rows:
            self.delete_task(row)

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

    def move_task(self, from_row, insert_index):
        """拖拽重排：把 from_row 的任务移动到 insert_index（同步表格/索引/持久化）。

        insert_index 为「移除源行后」的列表插入位置（0..n，n=追加到末尾）；
        任务最终落位 final_row = min(insert_index, n-1)。表格整表重建——
        单元格内含进度/状态/角标/时间，整行重建最可靠。
        """
        n = len(self.tasks)
        if not (0 <= from_row < n) or not (0 <= insert_index <= n):
            return
        if insert_index == from_row:
            return  # 原位（⚠ from_row+1 是「下移一行」的真实移动，不是原位）
        task = self.tasks.pop(from_row)
        final_row = min(insert_index, len(self.tasks))
        self.tasks.insert(final_row, task)

        def _shift(i):
            if i == from_row:
                return final_row
            if from_row < i <= final_row:
                return i - 1
            if final_row <= i < from_row:
                return i + 1
            return i
        self.current_task_index = _shift(self.current_task_index)

        self.table.setRowCount(0)
        for i, t in enumerate(self.tasks):
            self._add_task_row(t, row=i)
        self.table.selectRow(final_row)
        self.task_moved.emit(from_row, final_row)
        self.save_tasks()

    def _find_crash_report(self, task):
        """定位失败任务的错误现场：优先最新崩溃报告文件，退回输出目录。

        崩溃报告由 write_crash_report 落在输出文件同目录（文件名含时间戳）。
        返回 None 表示输出目录不存在（无法直达）。
        """
        base = task.output_path or task.path
        out_dir = os.path.dirname(base)
        if not out_dir or not os.path.isdir(out_dir):
            return None
        try:
            reports = [f for f in os.listdir(out_dir)
                       if f.startswith('崩溃报告_') and f.endswith('.txt')]
            if reports:
                reports.sort(key=lambda f: os.path.getmtime(os.path.join(out_dir, f)),
                             reverse=True)
                return os.path.join(out_dir, reports[0])
        except OSError:
            pass
        return out_dir
    
    def _delete_checkpoint_and_reset(self, row):
        """删除任务的分段进度目录并重置为待处理（不可恢复，二次确认后执行）。

        用于：参数变更导致续跑被拒（checkpoint 哈希不匹配）、或用户想主动
        放弃旧分段从头重跑。目录删除走 Python（中文路径安全），删除后
        segment_info 重新扫描盘上 checkpoint 自然变为 None。
        """
        if not (0 <= row < len(self.tasks)):
            return
        task = self.tasks[row]
        info = task.segment_info or {}
        task_dir = info.get('task_dir')
        if not task_dir or not os.path.isdir(task_dir):
            InfoBar.warning(
                title=tr['TaskList']['Warning'],
                content=tr['TaskList']['DeleteCheckpointMissing'],
                parent=self.get_root_parent(), duration=3000)
            return
        tl = tr['TaskList']
        box = MessageBox(
            tl['DeleteCheckpointTitle'],
            tl['DeleteCheckpointBody'].format(
                task.name, info.get('done_segments', 0),
                info.get('total_segments', 0), task_dir),
            self.get_root_parent())
        if not box.exec():
            return
        try:
            shutil.rmtree(task_dir)
        except OSError as e:
            InfoBar.error(
                title=tl['Warning'],
                content=tl['DeleteCheckpointFailed'].format(e),
                parent=self.get_root_parent(), duration=5000)
            return
        # 重置任务态：状态/进度/角标。save_tasks 会重扫盘上 checkpoint（已删除→None）
        self.update_task_status(row, TaskStatus.PENDING)
        self.update_task_progress(row, 0)
        InfoBar.success(
            title=tl['DeleteCheckpointDoneTitle'],
            content=tl['DeleteCheckpointDone'].format(task.name),
            parent=self.get_root_parent(), duration=4000)

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
        """刷新任务队列状态（快照模式：不再自动落盘；path 参数仅为兼容旧签名）。

        v1.x 的「自动保存到 tasks.json + 启动自动恢复」机制已移除，任务队列
        改由用户通过「保存快照 / 加载快照」手动管理。此方法保留原调用点
        （增删任务/移动/选项变更处）并只做两件事：按盘上 checkpoint 刷新
        分段续跑信息 + 刷新状态行。
        """
        try:
            for task in self.tasks:
                task.segment_info = collect_segment_info(task)
            self._update_queue_status()
            return True
        except Exception as e:
            print(f'[任务持久化] 刷新失败: {e}')
            return False

    def _update_queue_status(self):
        """刷新任务队列状态行：快照模式说明 + 任务/可续跑计数 + 预估剩余 + 最近快照"""
        tl = tr['TaskList']
        n = len(self.tasks)
        resumable = sum(1 for t in self.tasks
                        if t.segment_info and t.segment_info.get('can_resume'))
        text = tl['QueueSnapshotHint']
        if n:
            text += ' · ' + tl['QueueTasksCount'].format(n)
            if resumable:
                text += ' · ' + tl['QueueResumeCount'].format(resumable)
            eta, _ = self._queue_eta_seconds()
            if eta:
                text += ' · ' + tl['QueueEta'].format(self._fmt_duration(eta))
            if self._last_snapshot_time:
                text += ' · ' + tl['QueueSavedAt'].format(self._last_snapshot_time)
        self.queue_status.setText(text)
        self.queue_status.setToolTip(tl['QueueSnapshotTip'])

    def _open_queue_dir(self):
        """状态行入口：打开快照目录（不存在则打开 config 目录）"""
        try:
            target = SNAPSHOTS_DIR if os.path.isdir(SNAPSHOTS_DIR) else os.path.dirname(TASKS_FILE)
            show_in_file_manager(target)
        except Exception as e:
            print(f'[任务持久化] 打开文件夹失败: {e}')

    # ------------------------------------------------------------------ #
    # 手动快照：保存 / 加载
    # ------------------------------------------------------------------ #

    def save_snapshot(self, path):
        """手动保存当前任务队列为快照文件（格式与旧 tasks.json 兼容）。

        返回写入路径；失败抛异常（对话框层捕获提示）。"""
        for task in self.tasks:
            task.segment_info = collect_segment_info(task)
        save_tasks_file(self.tasks, path)
        self._last_snapshot_time = datetime.now().strftime('%H:%M:%S')
        self._update_queue_status()
        return path

    def save_snapshot_dialog(self):
        """「保存快照」按钮：选择路径写入当前任务队列（格式与旧 tasks.json 兼容）"""
        tl = tr['TaskList']
        if not self.tasks:
            InfoBar.warning(title=tl['SnapshotSave'], content=tl['SnapshotEmpty'],
                            parent=self.get_root_parent(), duration=3000)
            return
        for task in self.tasks:
            task.segment_info = collect_segment_info(task)
        try:
            os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        except OSError:
            pass
        suggested = os.path.join(
            SNAPSHOTS_DIR, f'任务快照_{datetime.now():%Y%m%d_%H%M%S}.json')
        path, _ = QFileDialog.getSaveFileName(
            self, tl['SnapshotSave'], suggested, 'JSON (*.json);;All Files (*.*)')
        if not path:
            return
        try:
            self.save_snapshot(path)
        except OSError as e:
            InfoBar.error(title=tl['SnapshotSave'],
                          content=tl['SnapshotSaveFailed'].format(e),
                          parent=self.get_root_parent(), duration=5000)
            return
        self._last_snapshot_time = datetime.now().strftime('%H:%M:%S')
        self._update_queue_status()
        InfoBar.success(title=tl['SnapshotSave'],
                        content=tl['SnapshotSaved'].format(path),
                        parent=self.get_root_parent(), duration=4000)

    def load_snapshot_dialog(self):
        """「加载快照」按钮：选择快照文件替换当前任务列表"""
        tl = tr['TaskList']
        if any(t.status == TaskStatus.PROCESSING for t in self.tasks):
            InfoBar.warning(title=tl['SnapshotLoad'], content=tl['SnapshotBusy'],
                            parent=self.get_root_parent(), duration=3000)
            return
        start_dir = SNAPSHOTS_DIR if os.path.isdir(SNAPSHOTS_DIR) else os.path.dirname(TASKS_FILE)
        path, _ = QFileDialog.getOpenFileName(
            self, tl['SnapshotLoad'], start_dir, 'JSON (*.json);;All Files (*.*)')
        if not path:
            return
        if self.tasks:
            box = MessageBox(tl['SnapshotReplaceTitle'],
                             tl['SnapshotReplaceBody'].format(len(self.tasks)),
                             self.get_root_parent())
            if not box.exec():
                return
        try:
            restored, warnings = self.load_snapshot(path)
        except Exception as e:
            InfoBar.error(title=tl['SnapshotLoad'],
                          content=tl['SnapshotLoadFailed'].format(e),
                          parent=self.get_root_parent(), duration=5000)
            return
        for w in warnings:
            self.snapshot_log.emit(f'[快照加载] {w}')
        if restored:
            self.select_task(0)
        InfoBar.success(title=tl['SnapshotLoad'],
                        content=tl['SnapshotLoaded'].format(restored),
                        parent=self.get_root_parent(), duration=4000)

    def load_snapshot(self, path):
        """从快照文件恢复任务队列（替换当前列表）。

        - 上次运行中（PROCESSING）/失败（FAILED）的任务恢复为待运行态，
          不自动开跑（安全优先；分段任务点运行即走既有断点续接）；
        - 分段任务的续跑信息对接 segmenter.derive_resume_info 刷新；
        - 文件损坏/字段缺失时逐条降级跳过，不崩。
        Returns:
            (restored_count, warnings)
        """
        tasks, warnings = load_tasks_file(path)
        # 整表替换：不发逐条 task_deleted（调用方保证不在处理中）
        self.table.setRowCount(0)
        self.tasks = []
        self.current_task_index = -1
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
                self.snapshot_log.emit(f'[快照加载] {task.name} → {task.status.value}'
                                       f'（源文件不存在，可能已被移动或删除）')
            else:
                msg = f'[快照加载] {task.name} → {task.status.value}'
                if info is not None and info.get('chip_text'):
                    msg += f' · {info["chip_text"]}'
                    if info.get('can_resume'):
                        resumable += 1
                self.snapshot_log.emit(msg)
            self.tasks.append(task)
            self._add_task_row(task)
            restored += 1
        if restored:
            self.snapshot_log.emit(f'[快照加载] 共加载 {restored} 个任务'
                                   f'（{resumable} 个分段任务可断点续跑）；'
                                   f'不会自动开始运行，点击「开始处理」后启动')
        self._update_queue_status()
        return restored, warnings

    def set_task_output_path(self, index, output_path):
        """设置任务输出路径（任务完成后落库并触发持久化）"""
        if 0 <= index < len(self.tasks):
            self.tasks[index].output_path = output_path
            self.save_tasks()

    # ------------------------------------------------------------------ #
    # 计时与时长预估
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fmt_duration(seconds):
        """秒 → mm:ss（满 1 小时转 h:mm:ss）"""
        seconds = max(0, int(round(seconds)))
        h, rest = divmod(seconds, 3600)
        m, s = divmod(rest, 60)
        return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'

    def _estimate_seconds(self, task):
        """按历史速度 EMA 预估任务总时长（秒）；无法预估返回 None。

        前提：已有速度模型（EMA>0）且视频帧数可读取。图片任务直接 None。
        """
        if self._sec_per_frame_ema <= 0:
            return None
        n = task.frame_count
        if not n:
            return None
        return n * self._sec_per_frame_ema

    def _update_speed_model(self, task):
        """任务完成后用其实际用时/帧数更新速度 EMA（0.3 新样本权重）。"""
        try:
            if task.elapsed < 5:
                return  # 样本过短（极小图/秒完成）不具代表性
            n = task.frame_count
            if not n:
                return
            new = task.elapsed / n
            self._sec_per_frame_ema = (new if self._sec_per_frame_ema <= 0
                                       else 0.3 * new + 0.7 * self._sec_per_frame_ema)
        except Exception:
            pass

    def _refresh_time_label(self, index):
        """刷新单行时间标签（按状态给文案）"""
        if not (0 <= index < len(self.tasks)):
            return
        task = self.tasks[index]
        cell = self.table.cellWidget(index, 1)
        if cell is None:
            return
        tl = tr['TaskList']
        text = ''
        if task.status == TaskStatus.PROCESSING and task.started_at is not None:
            elapsed = time.time() - task.started_at
            # 总预估：进度 ≥3% 用本任务实测外推（最准）；
            # 早期外推不稳则退回历史速度 EMA；均不可得用 -- 占位保持格式可见
            total = None
            if task.progress >= 3:
                total = elapsed * 100.0 / task.progress
            else:
                total = self._estimate_seconds(task)
            text = tl['TimeRunFmt'].format(
                self._fmt_duration(elapsed),
                self._fmt_duration(total) if total else '--')
        elif task.status == TaskStatus.PENDING:
            est = self._estimate_seconds(task)
            if est:
                text = tl['TimeEst'].format(self._fmt_duration(est))
        elif task.elapsed > 0:
            text = tl['TimeUsed'].format(self._fmt_duration(task.elapsed))
        cell.set_time(text)

    def _refresh_pending_estimates(self):
        """速度模型变化后刷新所有待处理行的预估"""
        for i, task in enumerate(self.tasks):
            if task.status == TaskStatus.PENDING:
                self._refresh_time_label(i)

    def _tick_time_labels(self):
        """每秒刷新：处理中任务的已用/预计 + 队列状态行的剩余预估"""
        processing = [i for i, t in enumerate(self.tasks)
                      if t.status == TaskStatus.PROCESSING]
        for i in processing:
            self._refresh_time_label(i)
        self._update_queue_status()

    def _queue_eta_seconds(self):
        """队列剩余处理时长预估（秒）：当前任务剩余 + 待处理预估之和。

        Returns:
            (seconds, known): seconds 为 None 表示完全无法预估
        """
        known_total, has_unknown = 0.0, False
        processing = [(i, t) for i, t in enumerate(self.tasks)
                      if t.status == TaskStatus.PROCESSING]
        pending = [(i, t) for i, t in enumerate(self.tasks)
                   if t.status == TaskStatus.PENDING]
        if not processing and not pending:
            return None, True
        for _, t in processing:
            if t.started_at is None:
                has_unknown = True
                continue
            elapsed = time.time() - t.started_at
            if t.progress >= 3:
                known_total += elapsed * (100.0 - t.progress) / t.progress
            else:
                est = self._estimate_seconds(t)
                if est:
                    known_total += est
                else:
                    has_unknown = True
        for _, t in pending:
            est = self._estimate_seconds(t)
            if est:
                known_total += est
            else:
                has_unknown = True
        return (None if has_unknown and known_total <= 0 else known_total), not has_unknown