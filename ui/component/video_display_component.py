import cv2
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QMenu, QBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal, QRect, QRectF, QObject, QEvent, QTimer
from PySide6.QtGui import QAction, QShortcut, QCursor
from PySide6 import QtCore, QtWidgets, QtGui
from qfluentwidgets import qconfig, CardWidget, HollowHandleStyle, ToolButton, FluentIcon

from backend.config import config, tr

class VideoDisplayComponent(QWidget):
    size_changed = Signal(int)  # 预览区需按窗口宽度重设尺寸
    """视频显示组件，包含视频预览和选择框功能"""
    
    # 定义信号
    selections_changed = Signal(list)  # 选择框变化信号
    ab_sections_changed = Signal(list)  # AB分区变化信号
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        
        # 初始化变量
        self.is_drawing = False
        self.selection_rect = (0, 0, 0, 0)  # 当前正在绘制或调整的选区 (ymin, ymax, xmin, xmax)
        self.selection_rects = []  # 存储多个选区，每个元素为 (ymin, ymax, xmin, xmax)
        self.active_selection_index = -1  # 当前活动选区的索引
        self.drag_start_pos = None
        self.resize_edge = None
        self.edge_size = 10  # 调整大小的边缘区域
        self.enable_mouse_events = True  # 控制是否启用鼠标事件
        
        # AB分区标记相关变量
        self.ab_sections = []  # 存储AB分区标记 [range(start, end), ...]
        self.current_ab_start = -1  # 当前AB分区的起点
        
        # 创建右键菜单
        self.__init_context_menu()
        
        # 获取屏幕大小
        screen = QtWidgets.QApplication.primaryScreen().size()
        self.screen_width = screen.width()
        self.screen_height = screen.height()
        
        # 视频预览区域：初始 640x360（单框模式，视频加载后按宽高比自适应）
        self.video_preview_width = 640
        self.video_preview_height = 360
        # A/B 双框各自的尺寸（video_preview_* 是 A+B 总占位）
        self._a_box_size = (640, 360)
        self._b_box_size = (640, 360)
        # 显示模式：False=单框（未处理，画面最大化）；True=A/B 对比（处理中/结果对比）
        self._compare_mode = False
        self._apply_compare = None  # 上次应用到布局的模式（None=尚未应用）
        # A/B 双框间距
        self._box_spacing = 4
        # 纵向预算上限的宿主注入点：callable -> 像素。竖屏视频的预览高度受
        # 「窗口实际可用高度」约束（写死 640 会在矮窗口把日志卡挤出可视区，
        # 高窗口又白白留黑边），由 HomeInterface 注入实时高度预算
        self.preview_height_budget = None
        # 预览区自适应窗口：尺寸变化防抖后通知外层重渲染
        self._pending_width = self.video_preview_width
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(lambda: self.size_changed.emit(self._pending_width))
        # 视频相关参数
        self.frame_width = None
        self.frame_height = None
        self.fps = 30
        # letterbox 几何：视频帧在 video_display 控件内的实际渲染区域（单位：控件像素）。
        # 渲染按 contain 规则等比缩放并居中，剩余区域为黑边；坐标映射全部基于该矩形。
        # 每次 update_video_display 渲染时按控件实际尺寸重算，保证映射与显示一致。
        self.render_width = 0
        self.render_height = 0
        self.render_offset_x = 0
        self.render_offset_y = 0
        self.render_scale = 0.0  # render_width / frame_width（等比，纵横一致）
        self._current_frame = None  # 最近一次渲染的原始帧（控件尺寸变化时按新尺寸重渲染）
        self._current_b_frame = None  # B 框最近一次渲染的结果帧

        self.__init_widgets()
        self.__init_shotcuts()
        
    def __init_widgets(self):
        """初始化组件"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # 视频预览区域和进度条容器
        self.video_container = CardWidget(self)
        self.video_container.setObjectName('videoContainer')
        video_layout = QVBoxLayout()
        video_layout.setSpacing(0)
        video_layout.setContentsMargins(2, 2, 2, 2)
        video_layout.setAlignment(Qt.AlignCenter)
        
        # 创建内部黑色背景容器
        self.black_container = QWidget(self)
        self.black_container.setObjectName('blackContainer')
        self.black_container.setStyleSheet("""
            #blackContainer {
                background-color: black;
                border-radius: 10px;
                border: 0px solid transparent;
            }
        """)
        black_layout = QVBoxLayout()
        black_layout.setContentsMargins(0, 0, 0, 0)
        black_layout.setSpacing(0)
        black_layout.setAlignment(Qt.AlignCenter)
        
        # 视频显示标签
        self.video_display = QtWidgets.QLabel()
        self.video_display.setStyleSheet("""
            background-color: black;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            border: 0px solid transparent;
        """)
        self.video_display.setMinimumSize(640, 360)
        
        self.video_display.setMouseTracking(True)
        self.video_display.setScaledContents(True)
        self.video_display.setAlignment(Qt.AlignCenter)
        self.video_display.mousePressEvent = self.selection_mouse_press
        self.video_display.mouseMoveEvent = self.selection_mouse_move
        self.video_display.mouseReleaseEvent = self.selection_mouse_release
        
        # 视频滑块
        self.video_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.video_slider.setMinimum(1)
        self.video_slider.setFixedHeight(22)
        self.video_slider.setMaximum(100)  # 默认最大值设为100，与进度百分比一致
        self.video_slider.setValue(1)
        self.video_slider.setStyle(HollowHandleStyle({
            "handle.color": QtGui.QColor(255, 255, 255),
            "handle.ring-width": 4,
            "handle.hollow-radius": 6,
            "handle.margin": 1
        }))
        
        # 视频预览区域
        self.video_display.setObjectName('videoDisplay')
        # 创建一个容器来保持比例（A 框：源视频，含字幕框选）
        self.ratio_container = ratio_container = QWidget()
        ratio_layout = QVBoxLayout(ratio_container)
        ratio_layout.setContentsMargins(0, 0, 0, 0)
        ratio_layout.addWidget(self.video_display)

        # B 框：处理结果（处理中实时预览 / 完成后同步播放对比）
        self.b_video_display = QtWidgets.QLabel()
        self.b_video_display.setStyleSheet("background-color: black;")
        self.b_video_display.setMinimumSize(240, 135)
        self.b_video_display.setScaledContents(True)
        self.b_video_display.setAlignment(Qt.AlignCenter)

        self.b_ratio_container = QWidget()
        b_ratio_layout = QVBoxLayout(self.b_ratio_container)
        b_ratio_layout.setContentsMargins(0, 0, 0, 0)
        b_ratio_layout.addWidget(self.b_video_display)

        # B 框占位提示（无结果帧时显示；有帧即隐藏）
        self.b_placeholder = QLabel(
            tr['SubtitleExtractorGUI']['ComparePlaceholder'], self.b_ratio_container)
        self.b_placeholder.setAlignment(Qt.AlignCenter)
        self.b_placeholder.setWordWrap(True)
        self.b_placeholder.setStyleSheet(
            "color:#9A9A9A; background-color:rgba(0,0,0,0); font-size:12px; padding:8px;")
        self.b_placeholder.setGeometry(0, 0, *self._b_box_size)
        self.b_placeholder.raise_()

        # A/B 双框容器：方向按视频宽高比在 update_preview_size 里切换
        # （横屏上下排 / 竖屏左右排）；总占位不变，窗口不因布局跳变
        self.boxes_widget = QWidget()
        self.boxes_layout = QBoxLayout(QBoxLayout.TopToBottom)
        self.boxes_layout.setContentsMargins(0, 0, 0, 0)
        self.boxes_layout.setSpacing(self._box_spacing)
        self.boxes_layout.addWidget(ratio_container)
        self.boxes_layout.addWidget(self.b_ratio_container)
        self.boxes_widget.setLayout(self.boxes_layout)

        # 设置固定的宽高比：初始 16:9（视频加载后按实际宽高比自适应重排，
        # 见 set_video_parameters → update_preview_size）
        ratio_container.setFixedSize(*self._a_box_size)
        self.b_ratio_container.setFixedSize(*self._b_box_size)

        # 添加到布局
        black_layout.addWidget(self.boxes_widget)

        # 添加一个事件过滤器来处理大小变化
        class RatioEventFilter(QObject):
            def __init__(self, owner, get_size):
                super().__init__()
                self._owner = owner
                self._get_size = get_size  # callable -> (w, h)

            def eventFilter(self, obj, event):
                if event.type() == QEvent.Resize:
                    # 容器比例随视频宽高比自适应（旧实现硬编码 16:9，导致竖屏/4:3
                    # 的 1080 视频在预览里缩成小条）。此处守护 update_preview_size
                    # 算出的目标尺寸，防止布局漂移破坏容器几何；尺寸已对齐时不动作
                    # （避免 setFixedSize 递归触发 Resize）。
                    w, h = self._get_size()
                    if obj.width() != w or obj.height() != h:
                        obj.setFixedSize(w, h)
                return False

        # 注意：过滤器必须由组件持有引用，否则被 GC 后失效（PySide6 陷阱）
        self._ratio_filter = RatioEventFilter(self, lambda: self._a_box_size)
        ratio_container.installEventFilter(self._ratio_filter)
        self._b_ratio_filter = RatioEventFilter(self, lambda: self._b_box_size)
        self.b_ratio_container.installEventFilter(self._b_ratio_filter)

        # B 框尺寸变化（窗口缩放/布局重排）时重渲染当前结果帧
        class BLabelResizeEventFilter(QObject):
            def __init__(self, owner):
                super().__init__()
                self._owner = owner

            def eventFilter(self, obj, event):
                if (event.type() == QEvent.Resize
                        and self._owner._current_b_frame is not None):
                    self._owner._render_frame_b(self._owner._current_b_frame)
                return False

        self._b_label_resize_filter = BLabelResizeEventFilter(self)
        self.b_video_display.installEventFilter(self._b_label_resize_filter)

        # 预览标签尺寸变化（如窗口布局重排、比例容器高度跟进）时，若 pixmap 与
        # 控件尺寸不一致，按新尺寸重渲染当前帧——否则 setScaledContents 会把旧
        # pixmap 拉伸变形（letterbox 后更明显），且映射几何与显示不一致
        class LabelResizeEventFilter(QObject):
            def __init__(self, owner):
                super().__init__()
                self._owner = owner

            def eventFilter(self, obj, event):
                if event.type() == QEvent.Resize and self._owner._current_frame is not None:
                    if (not hasattr(self._owner, 'current_pixmap')
                            or self._owner.current_pixmap is None
                            or self._owner.current_pixmap.size() != obj.size()):
                        self._owner._render_frame(self._owner._current_frame)
                        self._owner.update_preview_with_rect()
                return False

        # 注意：过滤器必须由组件持有引用，否则被 GC 后失效（PySide6 陷阱）
        self.label_resize_filter = LabelResizeEventFilter(self)
        self.video_display.installEventFilter(self.label_resize_filter)

        # 进度条和滑块容器（含 A/B 同步播放按钮：常驻可见，就绪与否由宿主 enable）
        control_container = QWidget(self)
        control_layout = QVBoxLayout()
        control_layout.setContentsMargins(8, 8, 8, 8)
        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)
        self.play_button = ToolButton(FluentIcon.PLAY, self)
        self.play_button.setFixedSize(28, 28)
        self.play_button.setToolTip(tr['SubtitleExtractorGUI']['ComparePlayTip'])
        # 常驻可见 + 未就绪置灰：用户始终知道这里有 A/B 同步播放入口
        # （旧实现隐藏按钮，完成任务后因 A 路源已释放永远不显示，被报「没有播放按钮」）
        self.play_button.setEnabled(False)
        slider_row.addWidget(self.video_slider, 1)
        slider_row.addWidget(self.play_button)
        control_layout.addLayout(slider_row)
        
        control_container.setLayout(control_layout)
        control_container.setStyleSheet("""
            background-color: black;
            border-bottom-left-radius: 8px;
            border-bottom-right-radius: 8px;        
        """)
        black_layout.addWidget(control_container)
        
        self.black_container.setLayout(black_layout)
        self.black_container.setFixedWidth(self.video_preview_width + 4)
        video_layout.addWidget(self.black_container)
        self.video_container.setLayout(video_layout)
        main_layout.addWidget(self.video_container)
    
    def __init_shotcuts(self):
        """初始化快捷键"""
        self.shortcut_ab_start = QShortcut(QtGui.QKeySequence("["), self)
        self.shortcut_ab_start.activated.connect(self.__handle_mark_for_ab_start)
        self.shortcut_ab_start.setContext(Qt.ApplicationShortcut)

        self.shortcut_ab_end = QShortcut(QtGui.QKeySequence("]"), self)
        self.shortcut_ab_end.activated.connect(self.__handle_mark_for_ab_end)
        self.shortcut_ab_end.setContext(Qt.ApplicationShortcut)

        self.shortcut_ab_delete = QShortcut(QtGui.QKeySequence("\\"), self)
        self.shortcut_ab_delete.activated.connect(self.__handle_delete_ab_section)
        self.shortcut_ab_delete.setContext(Qt.ApplicationShortcut)

        self.shortcut_delete_selection = QShortcut(QtGui.QKeySequence.Delete, self)
        self.shortcut_delete_selection.activated.connect(self.__handle_delete_selection)
        self.shortcut_delete_selection.setContext(Qt.ApplicationShortcut)
        # 选框时间范围（per-box）：Alt+[ 起点 / Alt+] 终点 / Alt+\ 清除。
        # 修饰键与全局 AB 的 [/]/\ 区分——教程的任务串联工作流同时用到
        # 「选框 + 全局 AB」，两者必须能同时标记
        self.shortcut_box_time_start = QShortcut(QtGui.QKeySequence("Alt+["), self)
        self.shortcut_box_time_start.activated.connect(self.__handle_box_time_start)
        self.shortcut_box_time_end = QShortcut(QtGui.QKeySequence("Alt+]"), self)
        self.shortcut_box_time_end.activated.connect(self.__handle_box_time_end)
        self.shortcut_box_time_clear = QShortcut(QtGui.QKeySequence("Alt+\\"), self)
        self.shortcut_box_time_clear.activated.connect(self.__handle_box_time_clear)

        # 添加左右键控制slider的快捷键
        self.shortcut_right = QShortcut(QtGui.QKeySequence(Qt.Key_Right), self)
        self.shortcut_right.activated.connect(lambda: self.__adjust_slider_value(self.fps))
        self.shortcut_right.setContext(Qt.ApplicationShortcut)
        
        self.shortcut_left = QShortcut(QtGui.QKeySequence(Qt.Key_Left), self)
        self.shortcut_left.activated.connect(lambda: self.__adjust_slider_value(-self.fps))
        self.shortcut_left.setContext(Qt.ApplicationShortcut)
        
        # 添加Ctrl+左右键控制slider的快捷键
        self.shortcut_ctrl_right = QShortcut(QtGui.QKeySequence("Ctrl+Right"), self)
        self.shortcut_ctrl_right.activated.connect(lambda: self.__adjust_slider_value(self.fps*5))
        self.shortcut_ctrl_right.setContext(Qt.ApplicationShortcut)
        
        self.shortcut_ctrl_left = QShortcut(QtGui.QKeySequence("Ctrl+Left"), self)
        self.shortcut_ctrl_left.activated.connect(lambda: self.__adjust_slider_value(-self.fps*5))
        self.shortcut_ctrl_left.setContext(Qt.ApplicationShortcut)
        
        # 添加Shift+左右键控制slider的快捷键
        self.shortcut_shift_right = QShortcut(QtGui.QKeySequence("Shift+Right"), self)
        self.shortcut_shift_right.activated.connect(lambda: self.__adjust_slider_value(1))
        self.shortcut_shift_right.setContext(Qt.ApplicationShortcut)
        
        self.shortcut_shift_left = QShortcut(QtGui.QKeySequence("Shift+Left"), self)
        self.shortcut_shift_left.activated.connect(lambda: self.__adjust_slider_value(-1))
        self.shortcut_shift_left.setContext(Qt.ApplicationShortcut)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width() - 24  # 去除布局边距
        if w > 320 and abs(w - self.video_preview_width) >= 40:
            self._pending_width = w
            self._resize_timer.start()
            return
        # 纵向重排触发：本组件可用高度（扣除滑块容器 ~38px）与当前预览高度
        # 差距过大时，说明窗口高度预算变了（竖屏预览高度完全由它决定），
        # 防抖后重排——预览高度会重新贴合可用高度，不溢出也不浪费
        avail_h = self.height() - 38
        if avail_h > 240 and abs(avail_h - self.video_preview_height) >= 40:
            self._pending_width = max(self._pending_width, self.video_preview_width)
            self._resize_timer.start()

    def update_preview_size(self, width):
        """按「视频宽高比 + 窗口预算」计算预览布局，使画面最大化、框内零黑边。

        两档模式（set_compare_mode 切换）：
        - 单框（未处理）：视频 contain 进预算框，最长边贴满窗口；
        - A/B 对比（处理中/结果对比）：**始终左右排列**（用户定版，与原版
          hconcat 预览一致），每框按视频真实宽高比计算，先按预算宽度对半、
          高超预算再收，框内零 letterbox。

        横向预算 [640, 960]；纵向预算优先取宿主注入的可用高度
        （preview_height_budget，随窗口高度实时变化），未注入时退回 640。
        尺寸/模式均无变化时返回 False（跳过重排）。
        """
        budget_w = max(640, min(width, 960))
        if self.preview_height_budget is not None:
            try:
                budget_h = max(260, int(self.preview_height_budget()))
            except Exception:
                budget_h = 640
        else:
            budget_h = 640
        f_w = self.frame_width or 16
        f_h = self.frame_height or 9
        sp = self._box_spacing
        if not self._compare_mode:
            # 单框：视频宽高比 = 框宽高比，画面铺满框（框内零黑边）
            scale = min(budget_w / f_w, budget_h / f_h)
            total_w = max(1, int(round(f_w * scale)))
            total_h = max(1, int(round(f_h * scale)))
            a_size = b_size = (total_w, total_h)
        else:
            # A/B 左右排：先按预算宽度对半（每框=视频宽高比），高超预算再收
            bw = max(1, (budget_w - sp) // 2)
            bh = int(round(bw * f_h / f_w))
            if bh > budget_h:
                bh = budget_h
                bw = max(1, int(round(bh * f_w / f_h)))
            a_size = b_size = (bw, bh)
            total_w, total_h = bw * 2 + sp, bh

        b_visible = self._compare_mode
        changed = ((total_w, total_h) != (self.video_preview_width, self.video_preview_height)
                   or a_size != self._a_box_size or b_size != self._b_box_size
                   or self._apply_compare != self._compare_mode)
        if not changed:
            return False
        self._apply_compare = self._compare_mode
        self.video_preview_width = total_w
        self.video_preview_height = total_h
        self.boxes_layout.setDirection(QBoxLayout.LeftToRight)
        # 单框模式隐藏 B 框（A 独占），对比模式恢复
        self.b_ratio_container.setVisible(b_visible)
        self._a_box_size = a_size
        self._b_box_size = b_size
        self.ratio_container.setFixedSize(*a_size)
        self.b_ratio_container.setFixedSize(*b_size)
        self.b_placeholder.setGeometry(0, 0, b_size[0], b_size[1])
        self.video_display.setMinimumSize(min(320, a_size[0]), min(180, a_size[1]))
        self.b_video_display.setMinimumSize(min(240, b_size[0]), min(135, b_size[1]))
        self.black_container.setFixedWidth(total_w + 4)
        return True

    def set_compare_mode(self, enabled):
        """切换单框 / A/B 对比布局。

        未处理（加载视频、无结果源）→ False：A 框独占，画面按最长边撑满窗口；
        开始处理或打开结果对比源 → True：A/B 双框左右排列。
        切换后按当前视频宽高比立即重排，A/B 两帧由各自的 Resize 过滤器重渲染。
        """
        enabled = bool(enabled)
        if enabled == self._compare_mode:
            return
        self._compare_mode = enabled
        self._reflow_preview()

    def update_video_display(self, frame, draw_selection=True):
        """更新视频显示：帧按 contain 规则等比缩放居中（letterbox 黑边），
        不拉伸填充预览框；黑边由黑色画布提供。"""
        if frame is None:
            return
        self._current_frame = frame
        self._render_frame(frame)
        # 更新视频显示
        self.update_preview_with_rect(draw_selection=draw_selection)

    def _render_frame(self, frame):
        """把原始帧按 contain 规则渲染到 video_display 控件尺寸的黑色画布上，
        并记录 letterbox 几何（渲染矩形 + 偏移 + 缩放），供坐标映射使用。"""
        disp_w = self.video_display.width()
        disp_h = self.video_display.height()
        f_h, f_w = frame.shape[:2]
        if disp_w <= 0 or disp_h <= 0 or f_w <= 0 or f_h <= 0:
            return

        # contain 适配：保持原始比例缩放进控件，居中，剩余区域黑底
        scale = min(disp_w / f_w, disp_h / f_h)
        render_w = min(disp_w, max(1, int(round(f_w * scale))))
        render_h = min(disp_h, max(1, int(round(f_h * scale))))
        # 映射用精确适配比例与浮点居中偏移；渲染才用取整后的像素尺寸。
        # 两者最多差 0.5px（不可感知），但保证 orig→display→orig 往返严格一致。
        self.frame_width = f_w
        self.frame_height = f_h
        self.render_width = render_w
        self.render_height = render_h
        self.render_scale = scale
        self.render_offset_x = (disp_w - f_w * scale) / 2.0
        self.render_offset_y = (disp_h - f_h * scale) / 2.0

        # 与控件同尺寸的黑色画布：letterbox 黑边；圆角裁剪只作用于图像矩形（左上/右上）
        canvas = self._make_canvas(
            frame, disp_w, disp_h, int(round(self.render_offset_x)),
            int(round(self.render_offset_y)), render_w, render_h, rounded='top')

        # 保存当前的pixmap用于绘制选择框
        self.current_pixmap = canvas
        self.video_display.setPixmap(canvas)

    def _render_frame_b(self, frame):
        """B 框渲染：与 A 同规则 contain 缩放 + letterbox，独立几何（不参与选区映射）"""
        disp_w = self.b_video_display.width()
        disp_h = self.b_video_display.height()
        f_h, f_w = frame.shape[:2]
        if disp_w <= 0 or disp_h <= 0 or f_w <= 0 or f_h <= 0:
            return
        scale = min(disp_w / f_w, disp_h / f_h)
        render_w = min(disp_w, max(1, int(round(f_w * scale))))
        render_h = min(disp_h, max(1, int(round(f_h * scale))))
        off_x = int(round((disp_w - f_w * scale) / 2.0))
        off_y = int(round((disp_h - f_h * scale) / 2.0))
        canvas = self._make_canvas(frame, disp_w, disp_h, off_x, off_y,
                                   render_w, render_h, rounded='top')
        self.b_video_display.setPixmap(canvas)

    @staticmethod
    def _make_canvas(frame, disp_w, disp_h, img_x, img_y, render_w, render_h, rounded=None):
        """帧 → 控件尺寸黑色画布（contain 缩放 + 居中）；rounded='top' 时图像
        矩形左上/右上角做 8px 圆角裁剪。"""
        if (render_w, render_h) != (frame.shape[1], frame.shape[0]):
            frame = cv2.resize(frame, (render_w, render_h))
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_frame.shape
        bytes_per_line = ch * w
        image = QtGui.QImage(rgb_frame.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888)
        pix = QtGui.QPixmap.fromImage(image)

        canvas = QtGui.QPixmap(disp_w, disp_h)
        canvas.fill(QtGui.QColor(0, 0, 0))

        painter = QtGui.QPainter(canvas)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)  # 抗锯齿
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)

        if rounded == 'top':
            radius = 8
            path = QtGui.QPainterPath()
            path.moveTo(img_x + radius, img_y)
            path.lineTo(img_x + pix.width() - radius, img_y)
            path.arcTo(img_x + pix.width() - radius * 2, img_y, radius * 2, radius * 2, 90, -90)
            path.lineTo(img_x + pix.width(), img_y + pix.height())
            path.lineTo(img_x, img_y + pix.height())
            path.lineTo(img_x, img_y + radius)
            path.arcTo(img_x, img_y, radius * 2, radius * 2, 180, -90)
            path.closeSubpath()
            painter.setClipPath(path)
        painter.drawPixmap(img_x, img_y, pix)
        painter.end()
        return canvas

    def set_b_frame(self, frame):
        """B 框内容：None → 显示占位提示；有帧 → 渲染结果（处理中实时 / 对比播放）"""
        self._current_b_frame = frame
        if frame is None:
            self.b_video_display.clear()
            self.b_placeholder.setVisible(True)
            self.b_placeholder.raise_()
            return
        self.b_placeholder.setVisible(False)
        self._render_frame_b(frame)

    def seed_b_from_a(self):
        """用当前 A 框画面预填 B 框：检测阶段（尚无修复帧发来）B 框
        不再是黑底占位，而是与 A 框相同的画面；worker 的修复帧到达后自然接管。
        两个调用时机：点「运行」立即预填（已预览过同视频时秒出画面）；
        worker 载入任务视频后经 seed_b_signal 再填一次（未预览直接运行的场景，
        此时 _current_frame 已是当前任务首帧，修复黑窗问题见 home_interface）。"""
        if getattr(self, '_current_frame', None) is not None:
            self.set_b_frame(self._current_frame.copy())

    def display_to_original(self, x, y):
        """显示坐标（相对 video_display 控件）→ 原图像素坐标。
        orig = (disp - offset) / scale；黑边区域钳制到图像边缘，
        保证选区不会落到 letterbox 黑边区。"""
        if not self.render_scale or self.frame_width is None:
            return (0, 0)
        ox = int(round((float(x) - self.render_offset_x) / self.render_scale))
        oy = int(round((float(y) - self.render_offset_y) / self.render_scale))
        ox = max(0, min(int(self.frame_width), ox))
        oy = max(0, min(int(self.frame_height), oy))
        return (ox, oy)

    def original_to_display(self, ox, oy):
        """原图像素坐标 → 显示坐标（相对 video_display 控件），display_to_original 的逆运算"""
        dx = float(ox) * self.render_scale + self.render_offset_x
        dy = float(oy) * self.render_scale + self.render_offset_y
        return (dx, dy)

    def is_inside_image(self, x, y):
        """显示坐标是否落在 letterbox 后的图像矩形内（黑边内为 False）"""
        return (self.render_offset_x <= x <= self.render_offset_x + self.render_width
                and self.render_offset_y <= y <= self.render_offset_y + self.render_height)
    
    def _rect_to_display(self, rect):
        """选区（原图像素 ymin,ymax,xmin,xmax[,t0,t1]）→ 控件显示像素 QRect"""
        ymin, ymax, xmin, xmax = rect[:4]
        if ymin > ymax:
            ymin, ymax = ymax, ymin
        if xmin > xmax:
            xmin, xmax = xmax, xmin
        x0d, y0d = self.original_to_display(xmin, ymin)
        x1d, y1d = self.original_to_display(xmax, ymax)
        return QRect(int(x0d), int(y0d), int(x1d - x0d), int(y1d - y0d))

    def update_preview_with_rect(self, rect=None, draw_selection=True):
        """更新带有选择框的预览"""
        if not hasattr(self, 'current_pixmap') or self.current_pixmap is None:
            return
        
        # 控件尺寸变化（如窗口缩放后布局重排）但尚未重渲染时，按新尺寸重渲染，
        # 避免 pixmap 与控件尺寸不一致被 setScaledContents 拉伸变形
        if self._current_frame is not None and self.current_pixmap.size() != self.video_display.size():
            self._render_frame(self._current_frame)
            
        # 如果提供了新的矩形，使用它
        if rect is not None and self.active_selection_index >= 0:
            self.selection_rects[self.active_selection_index] = rect
            
        # 创建一个副本用于绘制
        pixmap_copy = self.current_pixmap.copy()
        painter = QtGui.QPainter(pixmap_copy)
        
        # 绘制所有选区
        if draw_selection:
            for i, rect in enumerate(self.selection_rects):
                # 设置选择框样式
                if i == self.active_selection_index:
                    # 活动选区使用绿色
                    pen = QtGui.QPen(QtGui.QColor(0, 255, 0))
                else:
                    # 非活动选区使用黄色
                    pen = QtGui.QPen(QtGui.QColor(255, 255, 0))
                pen.setWidth(2)
                painter.setPen(pen)
                
                # 将原图像素坐标转换为控件上的显示坐标
                pixel_rect = self._rect_to_display(rect)
                
                # 绘制选择框
                painter.drawRect(pixel_rect)
            
            # 如果正在绘制新选区，也绘制它
            if self.is_drawing and any(self.selection_rect):
                pen = QtGui.QPen(QtGui.QColor(0, 255, 0))  # 绿色
                pen.setWidth(2)
                painter.setPen(pen)
                
                # 将原图像素坐标转换为控件上的显示坐标
                pixel_rect = self._rect_to_display(self.selection_rect)
                
                painter.drawRect(pixel_rect)
            
        # 绘制AB分区标记
        total_frames = self.video_slider.maximum()
        if total_frames > 0 and self.ab_sections:
            # 在视频显示区域下方5像素处绘制AB分区标记
            ab_rect_height = 5
            ab_rect_y = pixmap_copy.height() - ab_rect_height
            
            # 设置半透明白色画刷
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, 128))  # 半透明白色
            
            # 计算可用宽度（考虑左右边距）
            left_margin = 15
            right_margin = 15
            available_width = pixmap_copy.width() - left_margin - right_margin
            
            for section_range in self.ab_sections:
                # 计算相对位置
                start_x = left_margin + int((section_range.start / total_frames) * available_width)
                end_x = left_margin + int((section_range.stop / total_frames) * available_width)
                
                # 绘制AB分区矩形
                painter.drawRect(start_x, ab_rect_y, end_x - start_x, ab_rect_height)
        
        # 绘制current_ab_start的高亮竖线
        if self.current_ab_start >= 0 and total_frames > 0:
            # 计算可用宽度（考虑左右边距）
            left_margin = 15
            right_margin = 15
            available_width = pixmap_copy.width() - left_margin - right_margin
            
            # 计算current_ab_start的相对位置
            start_x = left_margin + int((self.current_ab_start / total_frames) * available_width)
            
            # 设置高亮白色画笔
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255))  # 纯白色
            pen.setWidth(2)
            painter.setPen(pen)
            
            # 绘制高亮竖线，高度为5像素
            ab_line_height = 5
            ab_line_y = pixmap_copy.height() - ab_line_height
            painter.drawLine(start_x, ab_line_y, start_x, pixmap_copy.height())

        # 绘制选框时间范围条带（AB 行上方的第二行，彩色，活动选框高亮）。
        # 每个设了时间范围（6元组）的选框一条；未设时间的选框=全程，不绘制
        if total_frames > 0 and any(len(r) >= 6 for r in self.selection_rects):
            band_h = 4
            band_y = pixmap_copy.height() - 10   # AB 行（底部5px）上方
            colors = [(66, 133, 244), (219, 68, 55), (244, 180, 0), (15, 157, 88), (140, 90, 220)]
            left_margin = 15
            right_margin = 15
            available_width = pixmap_copy.width() - left_margin - right_margin
            painter.setPen(Qt.NoPen)
            for i, rect in enumerate(self.selection_rects):
                if len(rect) < 6:
                    continue
                t0, t1 = rect[4], rect[5]
                x0 = left_margin + int((t0 / total_frames) * available_width)
                x1 = left_margin + int((t1 / total_frames) * available_width)
                cr, cg, cb = colors[i % len(colors)]
                alpha = 230 if i == self.active_selection_index else 130
                painter.setBrush(QtGui.QColor(cr, cg, cb, alpha))
                painter.drawRect(x0, band_y, max(2, x1 - x0), band_h)

        painter.end()
        
        # 更新显示
        self.video_display.setPixmap(pixmap_copy)
    
    def selection_mouse_press(self, event):
        """鼠标按下事件处理"""
        if not self.enable_mouse_events:
            return
        
        # 右键点击显示上下文菜单
        if event.button() == Qt.RightButton:
            self.context_menu.exec_(event.globalPos())
            return
        
        # 开始绘制新选区
        if event.modifiers() & Qt.ControlModifier:
            self.is_drawing = True
            pos = event.pos()
            
            # 显示坐标 → 原图像素坐标（黑边区域钳制到图像边缘）
            orig_x, orig_y = self.display_to_original(pos.x(), pos.y())
            
            # 初始化选区为单点（原图像素坐标）
            self.selection_rect = (orig_y, orig_y, orig_x, orig_x)
            self.drag_start_pos = (orig_y, orig_x)  # 保存起始点的原图坐标
            self.resize_edge = None
            self.active_selection_index = -1
            return
        
        # 双击重置所有选区
        if event.type() == QEvent.MouseButtonDblClick:
            self.clear_selections()
            return
        
        # 检查是否点击了已有选区
        pos = event.pos()
        orig_x, orig_y = self.display_to_original(pos.x(), pos.y())
        
        clicked_index = -1
        for i, rect in enumerate(self.selection_rects):
            # 将原图像素坐标转换为控件上的显示坐标用于检测
            pixel_rect = self._rect_to_display(rect)
            
            # 检查是否在选区边缘（用于调整大小）
            if self.is_on_rect_edge(pos, pixel_rect):
                clicked_index = i
                self.active_selection_index = i
                self.resize_edge = self.get_resize_edge(pos, pixel_rect)
                self.drag_start_pos = (orig_y, orig_x)
                self.update_preview_with_rect()
                return
            # 检查是否在选区内部（用于移动）
            elif pixel_rect.contains(pos):
                clicked_index = i
                self.active_selection_index = i
                self.resize_edge = "move"
                self.drag_start_pos = (orig_y, orig_x)
                self.update_preview_with_rect()
                return
        
        # 如果没有点击任何选区，开始绘制新选区
        if clicked_index == -1:
            self.is_drawing = True
            self.selection_rect = (orig_y, orig_y, orig_x, orig_x)
            self.drag_start_pos = (orig_y, orig_x)
            self.resize_edge = None
            self.active_selection_index = -1

    def is_on_rect_edge(self, pos, pixel_rect):
        """检查点是否在矩形边缘
        注意：这里的pixel_rect是已经转换为像素坐标的QRect对象
        """
        # 右下角
        if abs(pos.x() - pixel_rect.right()) <= self.edge_size and abs(pos.y() - pixel_rect.bottom()) <= self.edge_size:
            return True
        # 右上角
        elif abs(pos.x() - pixel_rect.right()) <= self.edge_size and abs(pos.y() - pixel_rect.top()) <= self.edge_size:
            return True
        # 左下角
        elif abs(pos.x() - pixel_rect.left()) <= self.edge_size and abs(pos.y() - pixel_rect.bottom()) <= self.edge_size:
            return True
        # 左上角
        elif abs(pos.x() - pixel_rect.left()) <= self.edge_size and abs(pos.y() - pixel_rect.top()) <= self.edge_size:
            return True
        # 左边缘
        elif abs(pos.x() - pixel_rect.left()) <= self.edge_size and pixel_rect.top() <= pos.y() <= pixel_rect.bottom():
            return True
        # 右边缘
        elif abs(pos.x() - pixel_rect.right()) <= self.edge_size and pixel_rect.top() <= pos.y() <= pixel_rect.bottom():
            return True
        # 上边缘
        elif abs(pos.y() - pixel_rect.top()) <= self.edge_size and pixel_rect.left() <= pos.x() <= pixel_rect.right():
            return True
        # 下边缘
        elif abs(pos.y() - pixel_rect.bottom()) <= self.edge_size and pixel_rect.left() <= pos.x() <= pixel_rect.right():
            return True
        return False

    def get_resize_edge(self, pos, rect):
        """获取调整大小的边缘类型"""
        # 右下角
        if abs(pos.x() - rect.right()) <= self.edge_size and abs(pos.y() - rect.bottom()) <= self.edge_size:
            return "bottomright"
        # 右上角
        elif abs(pos.x() - rect.right()) <= self.edge_size and abs(pos.y() - rect.top()) <= self.edge_size:
            return "topright"
        # 左下角
        elif abs(pos.x() - rect.left()) <= self.edge_size and abs(pos.y() - rect.bottom()) <= self.edge_size:
            return "bottomleft"
        # 左上角
        elif abs(pos.x() - rect.left()) <= self.edge_size and abs(pos.y() - rect.top()) <= self.edge_size:
            return "topleft"
        # 左边缘
        elif abs(pos.x() - rect.left()) <= self.edge_size and rect.top() <= pos.y() <= rect.bottom():
            return "left"
        # 右边缘
        elif abs(pos.x() - rect.right()) <= self.edge_size and rect.top() <= pos.y() <= rect.bottom():
            return "right"
        # 上边缘
        elif abs(pos.y() - rect.top()) <= self.edge_size and rect.left() <= pos.x() <= rect.right():
            return "top"
        # 下边缘
        elif abs(pos.y() - rect.bottom()) <= self.edge_size and rect.left() <= pos.x() <= rect.right():
            return "bottom"
        return None

    def selection_mouse_move(self, event):
        """鼠标移动事件处理"""
        if not self.enable_mouse_events:
            return
        
        pos = event.pos()
        # 显示坐标 → 原图像素坐标（内部已钳制在图像区域内，黑边上拖拽不会越界）
        orig_x, orig_y = self.display_to_original(pos.x(), pos.y())
        
        # 根据不同的操作模式处理鼠标移动
        if self.is_drawing:  # 绘制新选择框
            # 更新选择框的右下角，保留原始拖动方向
            start_y, _, start_x, _ = self.selection_rect
            self.selection_rect = (start_y, orig_y, start_x, orig_x)
            self.update_preview_with_rect()
        elif self.resize_edge and self.active_selection_index >= 0:  # 调整选择框大小或位置
            ymin, ymax, xmin, xmax = self.selection_rects[self.active_selection_index][:4]
            start_y, start_x = self.drag_start_pos

            if self.resize_edge == "move":
                # 移动整个选择框
                dy = orig_y - start_y
                dx = orig_x - start_x

                # 计算新位置，确保不超出图像边界
                new_ymin = max(0, min(self.frame_height - (ymax - ymin), ymin + dy))
                new_ymax = min(self.frame_height, max(new_ymin + (ymax - ymin), new_ymin))
                new_xmin = max(0, min(self.frame_width - (xmax - xmin), xmin + dx))
                new_xmax = min(self.frame_width, max(new_xmin + (xmax - xmin), new_xmin))

                self.selection_rects[self.active_selection_index] = \
                    (new_ymin, new_ymax, new_xmin, new_xmax) + self.selection_rects[self.active_selection_index][4:]
                self.drag_start_pos = (orig_y, orig_x)
            else:
                # 调整选择框大小（原图像素坐标，最小边距 1px）
                if "left" in self.resize_edge:
                    xmin = min(xmax - 1, orig_x)
                if "right" in self.resize_edge:
                    xmax = max(xmin + 1, orig_x)
                if "top" in self.resize_edge:
                    ymin = min(ymax - 1, orig_y)
                if "bottom" in self.resize_edge:
                    ymax = max(ymin + 1, orig_y)
                
                # 确保选择框在图像有效范围内
                xmin = max(0, min(xmin, self.frame_width))
                xmax = max(0, min(xmax, self.frame_width))
                ymin = max(0, min(ymin, self.frame_height))
                ymax = max(0, min(ymax, self.frame_height))
                
                # 确保xmin < xmax, ymin < ymax
                if xmin > xmax:
                    xmin, xmax = xmax, xmin
                if ymin > ymax:
                    ymin, ymax = ymax, ymin

                # 保留时间字段（若有）：调整大小/移动不改时间范围
                tail = self.selection_rects[self.active_selection_index][4:] \
                    if len(self.selection_rects[self.active_selection_index]) >= 6 else ()
                self.selection_rects[self.active_selection_index] = \
                    (ymin, ymax, xmin, xmax) + tail

            self.update_preview_with_rect()
        else:
            # 更新鼠标光标形状
            self.update_cursor_shape(pos)
    
    def selection_mouse_release(self, event):
        """鼠标释放事件处理"""
        if not self.enable_mouse_events:
            return
            
        # 结束绘制或调整
        if self.is_drawing:
            # 标准化选择框（确保ymin < ymax, xmin < xmax）
            ymin, ymax, xmin, xmax = self.selection_rect
            if ymin > ymax:
                ymin, ymax = ymax, ymin
            if xmin > xmax:
                xmin, xmax = xmax, xmin
            
            # 更新标准化后的选区
            self.selection_rect = (ymin, ymax, xmin, xmax)
            
            # 如果选择框有效（不是点击），添加到选区列表
            # 按控件上的显示尺寸判断（原图坐标换算到显示像素）
            x0d, y0d = self.original_to_display(xmin, ymin)
            x1d, y1d = self.original_to_display(xmax, ymax)
            
            if (x1d - x0d) > 5 and (y1d - y0d) > 5:
                self.selection_rects.append(self.selection_rect)
                self.active_selection_index = len(self.selection_rects) - 1
                
                # 发送选择框变化信号
                self.selections_changed.emit(self.selection_rects)
            
            self.is_drawing = False
            self.selection_rect = (0, 0, 0, 0)  # 重置为空选区
        elif self.resize_edge and self.active_selection_index >= 0:
            # 标准化选择框
            ymin, ymax, xmin, xmax = self.selection_rects[self.active_selection_index][:4]
            if ymin > ymax:
                ymin, ymax = ymax, ymin
            if xmin > xmax:
                xmin, xmax = xmax, xmin

            # 更新标准化后的选区（保留时间字段）
            tail = self.selection_rects[self.active_selection_index][4:] \
                if len(self.selection_rects[self.active_selection_index]) >= 6 else ()
            self.selection_rects[self.active_selection_index] = \
                (ymin, ymax, xmin, xmax) + tail
                        
            # 发送选择框变化信号
            self.selections_changed.emit(self.selection_rects)
            
            self.resize_edge = None
        
    def update_cursor_shape(self, pos):
        """根据鼠标位置更新光标形状"""
        # 如果有活动选区，优先检查活动选区
        if self.active_selection_index >= 0 and self.active_selection_index < len(self.selection_rects):
            # 获取活动选区
            # 将原图像素坐标转换为控件上的显示坐标
            pixel_rect = self._rect_to_display(self.selection_rects[self.active_selection_index])
            
            # 检查鼠标是否在选择框边缘
            if self.is_on_rect_edge(pos, pixel_rect):
                # 根据边缘类型设置光标
                edge_type = self.get_resize_edge(pos, pixel_rect)
                if edge_type == "left" or edge_type == "right":
                    self.video_display.setCursor(Qt.SizeHorCursor)
                    return
                elif edge_type == "top" or edge_type == "bottom":
                    self.video_display.setCursor(Qt.SizeVerCursor)
                    return
                elif edge_type == "topleft" or edge_type == "bottomright":
                    self.video_display.setCursor(Qt.SizeFDiagCursor)
                    return
                elif edge_type == "topright" or edge_type == "bottomleft":
                    self.video_display.setCursor(Qt.SizeBDiagCursor)
                    return
            elif pixel_rect.contains(pos):
                self.video_display.setCursor(Qt.SizeAllCursor)
                return
        
        # 如果没有活动选区或鼠标不在活动选区上，检查所有其他选区
        for rect in self.selection_rects:
            # 将原图像素坐标转换为控件上的显示坐标
            pixel_rect = self._rect_to_display(rect)
            
            # 检查鼠标是否在选择框边缘
            if self.is_on_rect_edge(pos, pixel_rect):
                # 根据边缘类型设置光标
                edge_type = self.get_resize_edge(pos, pixel_rect)
                if edge_type == "left" or edge_type == "right":
                    self.video_display.setCursor(Qt.SizeHorCursor)
                    return
                elif edge_type == "top" or edge_type == "bottom":
                    self.video_display.setCursor(Qt.SizeVerCursor)
                    return
                elif edge_type == "topleft" or edge_type == "bottomright":
                    self.video_display.setCursor(Qt.SizeFDiagCursor)
                    return
                elif edge_type == "topright" or edge_type == "bottomleft":
                    self.video_display.setCursor(Qt.SizeBDiagCursor)
                    return
            # 检查鼠标是否在选择框内部
            elif pixel_rect.contains(pos):
                self.video_display.setCursor(Qt.SizeAllCursor)
                return
        
        # 如果鼠标不在任何选区上，设置为默认光标
        self.video_display.setCursor(Qt.ArrowCursor)
    
    def set_video_parameters(self, frame_width, frame_height, fps=30):
        """设置视频参数（原图尺寸），并按视频宽高比重排预览容器使其自适应
        （竖屏 1080x1920 / 4:3 / 超宽均铺满预览框，不再固定 16:9 容器）。
        letterbox 几何（渲染矩形/偏移/缩放）在每次渲染时按控件实际尺寸计算，
        随 update_video_display 更新，坐标映射始终与当前显示一致。"""
        aspect_changed = (frame_width, frame_height) != (self.frame_width, self.frame_height)
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.fps = fps
        if aspect_changed and frame_width > 0 and frame_height > 0:
            self._reflow_preview()

    def _reflow_preview(self):
        """按当前视频宽高比重排预览容器（加载新视频时免防抖阈值立即生效）。
        显式同步 A/B 两框几何：load_video 在 set_video_parameters 后立即渲染首帧，
        嵌套布局异步生效来不及——否则首帧按旧控件尺寸渲染（1080 竖屏首帧缩成小条）。
        布局随后会按容器新尺寸重申同一几何，此处提前 resize 无副作用。"""
        budget = self.width() - 24 if self.width() > 344 else self.video_preview_width
        self.update_preview_size(max(640, budget))
        self.video_display.resize(*self._a_box_size)
        self.b_video_display.resize(*self._b_box_size)
    
    def get_selection_coordinates(self):
        """获取选择框坐标（原图像素）"""
        return self.selection_rect
    
    def set_selection_rects(self, rects):
        """设置选择框（原图像素坐标；条目可为 4 元组或带时间范围的 6 元组）"""
        self.selection_rects = list(rects)
        # 进行中的拖拽矩形恒为 4 元组（时间范围不属于拖拽状态）
        self.selection_rect = (rects[-1][:4] if rects else (0, 0, 0, 0))
        self.active_selection_index = len(rects) - 1
        self.update_preview_with_rect()
    
    def load_selections_from_config(self):
        """从配置中加载选择框。配置中保存的是相对图像的比率（0-1，与分辨率无关），
        这里按当前视频尺寸换算回原图像素坐标。"""
        # 从配置中读取选择框的相对位置和大小
        areas_str = config.subtitleSelectionAreas.value
        
        # 检查配置值是否有效
        if not areas_str:
            return False
        # 没有加载视频时无法换算像素坐标
        if not self.frame_width or not self.frame_height:
            return False

        # 清空现有选区
        self.selection_rects = []
        
        # 解析配置字符串
        areas = areas_str.split(";")
        for area in areas:
            try:
                parts = area.split(",")
                ymin, ymax, xmin, xmax = map(float, parts)
                # 比率 → 原图像素（取整，选区坐标必须是整数——下游 cv2.rectangle
                # 不接受 float），并钳制在有效范围内
                ymin = int(round(max(0.0, min(1.0, ymin)) * self.frame_height))
                ymax = int(round(max(0.0, min(1.0, ymax)) * self.frame_height))
                xmin = int(round(max(0.0, min(1.0, xmin)) * self.frame_width))
                xmax = int(round(max(0.0, min(1.0, xmax)) * self.frame_width))
                self.selection_rects.append((ymin, ymax, xmin, xmax))
            except ValueError:
                continue
        
        # 如果有选区，设置最后一个为活动选区
        if self.selection_rects:
            self.active_selection_index = len(self.selection_rects) - 1
        else:
            self.active_selection_index = -1
        self.selections_changed.emit(self.selection_rects)

        # 更新预览
        self.update_preview_with_rect()
        
        return len(self.selection_rects) > 0
    
    def set_dragger_enabled(self, enabled):
        """设置拖动器是否可用"""
        self.enable_mouse_events = enabled
        self.video_display.setMouseTracking(enabled)
        self.video_display.setCursor(Qt.ArrowCursor)
    
    def save_selections_to_config(self):
        """保存所有选择框到配置。保存为相对图像的比率（0-1），与视频分辨率无关。"""
        areas_str_parts = []
        
        if self.frame_width and self.frame_height:
            for rect in self.selection_rects:
                ymin, ymax, xmin, xmax = rect[:4]
                # 原图像素 → 相对图像的比率，四舍五入到4位小数
                areas_str_parts.append(
                    f"{round(ymin / self.frame_height, 4)},{round(ymax / self.frame_height, 4)},"
                    f"{round(xmin / self.frame_width, 4)},{round(xmax / self.frame_width, 4)}")
        # 注：config 默认选区只存空间比率；选框时间范围是任务级属性（任务选项 JSON），
        # 不落入全局配置
        
        # 更新配置
        config.subtitleSelectionAreas.value = ";".join(areas_str_parts)
        if len(config.subtitleSelectionAreas.value) <= 0:
            config.subtitleSelectionAreas.value = config.subtitleSelectionAreas.defaultValue
        qconfig.save()
    
    def get_selection_rects(self):
        """获取所有选区"""
        return self.selection_rects
    
    def clear_selections(self):
        """清除所有选区"""
        self.selection_rects = []
        self.active_selection_index = -1
        self.update_preview_with_rect()
        self.selections_changed.emit(self.selection_rects)

    def __handle_delete_selection(self):
        """处理删除当前选区的逻辑"""
        try:
            if self.active_selection_index >= 0 and self.selection_rects:
                # 删除当前活跃选区
                self.selection_rects.pop(self.active_selection_index)
                
                # 如果还有其他选区，将最后一个选区设为活跃选区
                if self.selection_rects:
                    self.active_selection_index = len(self.selection_rects) - 1
                else:
                    self.active_selection_index = -1
                
                # 更新显示
                self.update_preview_with_rect()
                
                # 发送选区变化信号
                self.selections_changed.emit(self.selection_rects)
                return True
            return False
        finally:
            # 获取当前鼠标位置
            global_pos = QCursor.pos()
            pos = self.video_display.mapFromGlobal(global_pos)
            self.update_cursor_shape(pos)

    # ---------- 选框时间范围（per-box） ----------

    def _set_box_time(self, index, t0, t1):
        """设置活动选框的时间范围（0-based、end 排他，与 AB 区块同口径）；
        t1 <= t0 视为无效忽略。选框存为 6 元组 (…,t0,t1)"""
        if not (0 <= index < len(self.selection_rects)) or t1 <= t0:
            return
        rect = self.selection_rects[index]
        self.selection_rects[index] = (rect[0], rect[1], rect[2], rect[3], int(t0), int(t1))
        self.update_preview_with_rect()
        self.selections_changed.emit(self.selection_rects)

    def __handle_box_time_start(self):
        """Alt+[：当前帧设为活动选框的时间起点（已有有效终点则保留，否则到片尾）"""
        i = self.active_selection_index
        if not (0 <= i < len(self.selection_rects)):
            return
        cur = self.video_slider.value()
        if cur < 0:
            return
        old = self.selection_rects[i]
        old_t1 = old[5] if len(old) >= 6 else None
        t1 = old_t1 if (old_t1 is not None and old_t1 > cur) else self.video_slider.maximum()
        self._set_box_time(i, cur, t1)

    def __handle_box_time_end(self):
        """Alt+]：当前帧+1 设为活动选框的时间终点（已有有效起点则保留，否则从头）"""
        i = self.active_selection_index
        if not (0 <= i < len(self.selection_rects)):
            return
        cur = self.video_slider.value()
        if cur < 0:
            return
        old = self.selection_rects[i]
        old_t0 = old[4] if len(old) >= 6 else None
        t0 = old_t0 if (old_t0 is not None and old_t0 < cur + 1) else 0
        self._set_box_time(i, t0, cur + 1)

    def __handle_box_time_clear(self):
        """Alt+\\：清除活动选框的时间范围（恢复全程有效）"""
        i = self.active_selection_index
        if not (0 <= i < len(self.selection_rects)):
            return
        rect = self.selection_rects[i]
        if len(rect) < 6:
            return
        self.selection_rects[i] = rect[:4]
        self.update_preview_with_rect()
        self.selections_changed.emit(self.selection_rects)

    def __handle_mark_for_ab_start(self):
        """处理标记AB分区起点的逻辑"""
        current_frame = self.video_slider.value()
        if current_frame >= 0:
            # 检查是否需要调整已有区间
            adjusted = False
            for i, section_range in enumerate(self.ab_sections):
                if current_frame in section_range:
                    # 调整已有区间的起点
                    self.ab_sections[i] = range(current_frame, section_range.stop)
                    adjusted = True
                    break
            
            if not adjusted:
                # 记录新的AB分区起点
                self.current_ab_start = current_frame
            
            # 更新显示
            self.update_preview_with_rect()
            return True
        return False

    def __handle_mark_for_ab_end(self):
        """处理标记AB分区终点的逻辑"""
        current_frame = self.video_slider.value()
        if current_frame >= 0 and self.current_ab_start >= 0:
            # 检查是否需要调整已有区间
            adjusted = False
            for i, section_range in enumerate(self.ab_sections):
                if current_frame in section_range:
                    # 调整已有区间的终点
                    self.ab_sections[i] = range(section_range.start, current_frame + 1)
                    adjusted = True
                    break
            
            if not adjusted and self.current_ab_start != current_frame:
                # 添加新的AB分区
                self.ab_sections.append(range(self.current_ab_start, current_frame + 1))
                self.current_ab_start = -1  # 重置起点
                self.ab_sections_changed.emit(self.ab_sections)
            
            # 更新显示
            self.update_preview_with_rect()
            return True
        return False

    def __handle_delete_ab_section(self):
        """处理删除当前AB区块的逻辑"""
        current_frame = self.video_slider.value()
        if current_frame >= 0 and self.ab_sections:
            # 查找当前帧所在的AB区块
            for i, section_range in enumerate(self.ab_sections):
                if current_frame in section_range:
                    # 删除该AB区块
                    self.ab_sections.pop(i)
                    
                    # 如果当前有标记的起点，且在被删除的区块内，重置起点
                    if self.current_ab_start in section_range:
                        self.current_ab_start = -1
                    
                    # 发送AB区块变化信号
                    self.ab_sections_changed.emit(self.ab_sections)
                    
                    # 更新显示
                    self.update_preview_with_rect()
                    return True
        return False
    
    def __adjust_slider_value(self, delta):
        """调整视频滑块的值"""
        current_value = self.video_slider.value()
        max_value = self.video_slider.maximum()
        new_value = current_value + int(delta)
        
        # 确保新值在有效范围内
        if new_value < self.video_slider.minimum():
            new_value = self.video_slider.minimum()
        elif new_value > max_value:
            new_value = max_value
            
        # 设置新值
        self.video_slider.setValue(new_value)

    def eventFilter(self, obj, event):
        """事件过滤器，处理键盘事件"""
        if event.type() == QEvent.KeyPress:
            # 处理退格键和删除键
            if event.key() == Qt.Key_Backspace or event.key() == Qt.Key_Delete:
                if self.__handle_delete_selection():
                    return True
        # 对于其他事件，继续传递给父类处理
        return super().eventFilter(obj, event)

    def __init_context_menu(self):
        """初始化右键菜单"""
        self.context_menu = QMenu(self)
        
        # 设定区块起点动作
        self.action_mark_ab_start = QAction(tr['SubtitleExtractorGUI']['MarkABStart'], self)
        self.action_mark_ab_start.setShortcut("[")
        self.action_mark_ab_start.triggered.connect(self.__handle_mark_for_ab_start)
        self.context_menu.addAction(self.action_mark_ab_start)
        
        # 设定区块终点动作
        self.action_mark_ab_end = QAction(tr['SubtitleExtractorGUI']['MarkABEnd'], self)
        self.action_mark_ab_end.setShortcut("]")
        self.action_mark_ab_end.triggered.connect(self.__handle_mark_for_ab_end)
        self.context_menu.addAction(self.action_mark_ab_end)

        self.action_mark_ab_delete = QAction(tr['SubtitleExtractorGUI']['DeleteABSection'], self)
        self.action_mark_ab_delete.setShortcut("\\")
        self.action_mark_ab_delete.triggered.connect(self.__handle_delete_ab_section)
        self.context_menu.addAction(self.action_mark_ab_delete)
        # 选框时间范围（per-box）：仅对当前选中（活动）的选框生效
        self.action_box_time_start = QAction(tr['SubtitleExtractorGUI']['MarkBoxTimeStart'], self)
        self.action_box_time_start.setShortcut("Alt+[")
        self.action_box_time_start.triggered.connect(self.__handle_box_time_start)
        self.context_menu.addAction(self.action_box_time_start)
        self.action_box_time_end = QAction(tr['SubtitleExtractorGUI']['MarkBoxTimeEnd'], self)
        self.action_box_time_end.setShortcut("Alt+]")
        self.action_box_time_end.triggered.connect(self.__handle_box_time_end)
        self.context_menu.addAction(self.action_box_time_end)
        self.action_box_time_clear = QAction(tr['SubtitleExtractorGUI']['ClearBoxTime'], self)
        self.action_box_time_clear.setShortcut("Alt+\\")
        self.action_box_time_clear.triggered.connect(self.__handle_box_time_clear)
        self.context_menu.addAction(self.action_box_time_clear)

        self.action_delete_selection = QAction(tr['SubtitleExtractorGUI']['DeleteSelection'], self)
        self.action_delete_selection.setShortcut("DELETE")
        self.action_delete_selection.triggered.connect(self.__handle_delete_selection)
        self.context_menu.addAction(self.action_delete_selection)

    def get_ab_sections(self):
        """获取AB分区标记"""
        return self.ab_sections

    def set_ab_sections(self, sections):
        """设置AB分区标记"""
        self.ab_sections = sections
        self.update_preview_with_rect()

    def clear_ab_sections(self):
        """清除所有AB分区标记"""
        self.ab_sections = []
        self.current_ab_start = -1
        self.update_preview_with_rect()

    def closeEvent(self, event):
        """窗口关闭时断开信号连接"""
        try:
            # 断开信号连接
            self.shortcut_ab_start.activated.disconnect(self.__handle_mark_for_ab_start)
            self.shortcut_ab_end.activated.disconnect(self.__handle_mark_for_ab_end)
            self.shortcut_ab_delete.activated.disconnect(self.__handle_delete_ab_section)
            self.action_mark_ab_start.triggered.disconnect(self.__handle_mark_for_ab_start)
            self.action_mark_ab_end.triggered.disconnect(self.__handle_mark_for_ab_end)
            self.action_mark_ab_delete.triggered.disconnect(self.__handle_delete_ab_section)
            self.action_delete_selection.triggered.disconnect(self.__handle_delete_selection)
            self.shortcut_delete_selection.activated.disconnect(self.__handle_delete_selection)
            self.action_box_time_start.triggered.disconnect(self.__handle_box_time_start)
            self.action_box_time_end.triggered.disconnect(self.__handle_box_time_end)
            self.action_box_time_clear.triggered.disconnect(self.__handle_box_time_clear)
        except Exception as e:
            print("Error during close window:", e)
        super().closeEvent(event)