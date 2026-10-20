# -*- coding: utf-8 -*-
"""全屏 A/B 同步播放窗口（独立顶层无边框窗口）。

不持有任何视频源：帧与进度由宿主（home_interface）经
set_frames / set_progress 推送；播放/暂停/跳转以信号回传宿主，
复用现有同步播放引擎（compare timer + 双 cap）。
"""
import numpy as np
from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Qt, Signal

try:
    from qfluentwidgets import ToolButton, FluentIcon
    _HAS_FW = True
except Exception:  # 允许在无 qfluentwidgets 的降级环境导入
    _HAS_FW = False

_HINT = 'Space play/pause · ←/→ frame · Ctrl+←/→ 1s · F fullscreen · Esc exit'


def _fmt_time(frame_no, fps):
    if fps and fps > 0:
        sec = frame_no / float(fps)
    else:
        sec = frame_no
    sec = max(0, int(round(sec)))
    return f'{sec // 60:02d}:{sec % 60:02d}'


class AbFullscreenWindow(QtWidgets.QWidget):
    """A/B 原片-成片 全屏同步播放窗：双画面 + 底部控制浮层。"""

    play_toggled = Signal(bool)     # True=播放 False=暂停（宿主接 compare 引擎）
    seek_requested = Signal(int)    # 目标帧号（1-based，与滑块同口径）
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('A/B Sync')
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setStyleSheet('background-color: black;')
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._fps = 30
        self._total = 0
        self._playing = False
        self._seeking = False

        # ── 双画面：按视频宽高比在 set_layout 里横/纵排（两套标签，各占一种布局） ──
        self._views = QtWidgets.QStackedWidget()
        h_split, v_split = QtWidgets.QWidget(), QtWidgets.QWidget()
        h_box = QtWidgets.QHBoxLayout(h_split)
        v_box = QtWidgets.QVBoxLayout(v_split)
        for box in (h_box, v_box):
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
        self._labels_h, self._labels_v = {}, {}
        for side, box, store in (('A', h_box, self._labels_h), ('B', h_box, self._labels_h),
                                 ('A', v_box, self._labels_v), ('B', v_box, self._labels_v)):
            lab = QtWidgets.QLabel()
            lab.setStyleSheet('background-color: black;')
            lab.setAlignment(Qt.AlignCenter)
            lab.setMinimumSize(160, 90)
            box.addWidget(lab)
            store[side] = lab
        self._labels = self._labels_h   # 当前布局的标签集（set_layout 切换）
        self._last_frames = (None, None)
        self._h_split, self._v_split = h_split, v_split
        self._views.addWidget(h_split)
        self._views.addWidget(v_split)

        # ── 右上角按钮层（网格叠加在视图上） ──
        corner = QtWidgets.QHBoxLayout()
        corner.setContentsMargins(0, 8, 12, 0)
        corner.addStretch(1)
        self._fs_btn = self._mk_btn('\u26f6', 30, tip='F')
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        self._exit_btn = self._mk_btn('\u2715', 30, tip='Esc')
        self._exit_btn.clicked.connect(self.close)
        corner.addWidget(self._fs_btn)
        corner.addWidget(self._exit_btn)

        # ── 底部控制浮层 ──
        bar = QtWidgets.QWidget(self)
        bar_v = QtWidgets.QVBoxLayout(bar)
        bar_v.setContentsMargins(24, 6, 24, 10)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self._play_btn = self._mk_btn('\u25b6', 36, tip='Space')
        self._play_btn.clicked.connect(self._emit_play_toggle)
        row.addWidget(self._play_btn)
        self._slider = QtWidgets.QSlider(Qt.Horizontal)
        self._slider.setMinimum(1)
        self._slider.setMaximum(100)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._time = QtWidgets.QLabel('00:00 / 00:00')
        self._time.setStyleSheet('color: white;')
        row.addWidget(self._slider, 1)
        row.addWidget(self._time)
        bar_v.addLayout(row)
        hint = QtWidgets.QLabel(_HINT)
        hint.setStyleSheet('color: rgba(255,255,255,160);')
        hint.setAlignment(Qt.AlignCenter)
        bar_v.addWidget(hint)
        bar.setStyleSheet(
            'background-color: rgba(0,0,0,150); border-top-left-radius: 6px; border-top-right-radius: 6px;')

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self._views, 0, 0)
        grid.addLayout(corner, 0, 0, alignment=Qt.AlignTop | Qt.AlignRight)
        grid.addWidget(bar, 1, 0)

        # 快捷键
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence(Qt.Key_Space), self, self._emit_play_toggle)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)
        QShortcut(QKeySequence(Qt.Key_F), self, self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self._nudge(1))
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self._nudge(-1))
        QShortcut(QKeySequence('Ctrl+Right'), self, lambda: self._nudge(30))
        QShortcut(QKeySequence('Ctrl+Left'), self, lambda: self._nudge(-30))

    @staticmethod
    def _mk_btn(text, size, tip=''):
        """黑底文字按钮（不依赖图标引擎：本机 SvgIconEngine 渲染为空）"""
        b = QtWidgets.QPushButton(text)
        b.setFixedSize(size, size)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setStyleSheet(
            'QPushButton{background:rgba(255,255,255,28);color:white;border:none;'
            'border-radius:6px;font-size:%dpx;}'
            'QPushButton:hover{background:rgba(255,255,255,70);}' % max(14, size // 2))
        return b

    # ---------- 布局与画面 ----------

    def set_layout(self, landscape: bool):
        """landscape=True 左右分屏，False 上下分屏（切换后重推最后一帧）"""
        self._labels = self._labels_h if landscape else self._labels_v
        self._views.setCurrentWidget(self._h_split if landscape else self._v_split)
        if self._last_frames != (None, None):
            self.set_frames(*self._last_frames)

    @staticmethod
    def _frame_to_pixmap(frame, label_size):
        if frame is None or not isinstance(frame, np.ndarray):
            return None
        if frame.ndim != 3 or frame.shape[2] < 3:
            return None
        h, w = frame.shape[:2]
        img = QtGui.QImage(frame.data, w, h, frame.strides[0],
                           QtGui.QImage.Format_BGR888).copy()
        return QtGui.QPixmap.fromImage(img).scaled(
            max(1, label_size.width() - 2), max(1, label_size.height() - 2),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def set_frames(self, frame_a, frame_b):
        self._last_frames = (frame_a, frame_b)
        for side, frame in (('A', frame_a), ('B', frame_b)):
            lab = self._labels[side]
            pm = self._frame_to_pixmap(frame, lab.size())
            lab.setPixmap(pm if pm is not None else QtGui.QPixmap())

    def set_total(self, total_frames):
        self._total = total_frames or 0
        self._slider.setMaximum(max(2, self._total + 1))
        self._refresh_time()

    def set_playing(self, playing: bool):
        self._playing = playing
        if self._play_btn is not None:
            self._play_btn.setText('\u23f8' if playing else '\u25b6')

    # ---------- 进度/跳转 ----------

    def set_progress(self, frame_no, total=None):
        if total is not None and total != self._total:
            self.set_total(total)
        if self._seeking:
            return
        self._slider.blockSignals(True)
        self._slider.setValue(max(1, min(self._slider.maximum(), int(frame_no))))
        self._slider.blockSignals(False)
        self._refresh_time()

    def _refresh_time(self):
        cur = self._slider.value()
        self._time.setText(
            f'{_fmt_time(cur - 1, self._fps)} / {_fmt_time(self._total, self._fps)}')

    def _on_slider_pressed(self):
        self._seeking = True

    def _on_slider_released(self):
        self._seeking = False
        self.seek_requested.emit(self._slider.value())
        self._refresh_time()

    def _nudge(self, delta_frames):
        self._seeking = True
        self._slider.setValue(
            max(1, min(self._slider.maximum(), self._slider.value() + delta_frames)))
        self._seeking = False
        self.seek_requested.emit(self._slider.value())
        self._refresh_time()

    def _emit_play_toggle(self):
        self.play_toggled.emit(not self._playing)

    # ---------- 窗口行为 ----------

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def set_fps(self, fps):
        self._fps = fps or 30

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
