# -*- coding: utf-8 -*-
"""紧凑参数行组件（主页/设置页共用）

- CompactRangeRow：数值参数（数字框精确键入与步进，越界自动钳制）
- CompactSwitchRow：开关参数
- CompactComboRow：下拉参数

统一「图示 + 参数名 | 控件」单行结构；说明不占行内空间，挂 tooltip 悬停显示；
数值行值偏离出厂默认时标题下显示「已修改 · 默认 N」小字标记。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (SpinBox, ComboBox, SwitchButton, IconWidget,
                           StrongBodyLabel, CaptionLabel, isDarkTheme)
from backend.config import config, tr


def accent_color():
    """主题感知的主色：浅色主题用主绿，深色主题用亮一档的绿"""
    return '#5DCAA5' if isDarkTheme() else '#1D9E75'


def set_row_tooltip(desc, *widgets):
    """参数说明不占行内空间：整行与各子控件统一挂 tooltip，鼠标悬停显示"""
    for w in widgets:
        w.setToolTip(desc)


def _make_icon(icon, parent):
    """行首图示：FluentIconBase 渲染为 16px 小图标（随主题变色）"""
    if icon is None:
        return None
    w = IconWidget(icon, parent)
    w.setFixedSize(16, 16)
    return w


class CompactRangeRow(QFrame):
    """紧凑数值参数行：图示 + 参数名 | 数字框（键盘键入与 −/+ 步进，越界钳制）。"""

    def __init__(self, config_item, title, desc, expert=False, icon=None, parent=None):
        super().__init__(parent)
        self.config_item = config_item
        self.expert = expert
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet('CompactRangeRow { background: transparent; }')

        validator = config_item.validator
        lo, hi = int(validator.min), int(validator.max)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(10)

        icon_widget = _make_icon(icon, self)
        if icon_widget is not None:
            layout.addWidget(icon_widget, 0, Qt.AlignVCenter)

        # 左侧：参数名（值偏离默认时下方出现「已修改」小标记）
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._title_label = StrongBodyLabel(title, self)
        self._title_label.setStyleSheet('font-size: 13px;')
        text_col.addWidget(self._title_label)
        # 说明挂 tooltip；该标签仅在值偏离默认时显示「已修改 · 默认 N」
        self._desc_label = CaptionLabel('', self)
        self._desc_label.setVisible(False)
        text_col.addWidget(self._desc_label)
        layout.addLayout(text_col, 1)

        # 右侧：数字框（键盘键入与上下步进，越界自动钳制到 validator 范围）
        self.spin = SpinBox(self)
        self.spin.setRange(lo, hi)
        self.spin.setFixedWidth(88)
        self.spin.setAlignment(Qt.AlignCenter)
        # 移除行末上下调节箭头（qfluentwidgets SpinBox 为自定义按钮，需用 setSymbolVisible；
        # 键盘键入、滚轮与上下方向键仍可调值）
        self.spin.setSymbolVisible(False)
        layout.addWidget(self.spin, 0)

        set_row_tooltip(desc, self, self._title_label, self.spin)

        self._syncing = False
        v = int(config_item.value)
        self.spin.setValue(v)
        self._refresh_mark()

        # UI -> 配置（写盘即保存）；Qt 信号仅在值变化时触发，天然去重
        self.spin.valueChanged.connect(self._on_spin)
        # 配置 -> UI（恢复默认等外部写入时同步）
        config_item.valueChanged.connect(self._on_config_changed)

    def _on_spin(self, v):
        if self._syncing:
            return
        config.set(self.config_item, int(v))
        self._refresh_mark()

    def _on_config_changed(self, v):
        self._syncing = True
        self.spin.setValue(int(v))
        self._syncing = False
        self._refresh_mark()

    def _refresh_mark(self):
        """值偏离出厂默认时，标题下显示「已修改 · 默认 N」小字标记"""
        if self.config_item.value != self.config_item.defaultValue:
            self._desc_label.setText(tr['Setting']['ModifiedTag'].format(self.config_item.defaultValue))
            self._desc_label.setVisible(True)
            self._desc_label.setStyleSheet(f'color: {accent_color()}; font-size: 11px;')
            self._title_label.setStyleSheet(f'font-size: 13px; color: {accent_color()};')
        else:
            self._desc_label.setVisible(False)
            self._title_label.setStyleSheet('font-size: 13px;')

    def refresh_row(self):
        self._on_config_changed(self.config_item.value)


class CompactSwitchRow(QFrame):
    """紧凑开关参数行：图示 + 参数名 | 开关"""

    def __init__(self, config_item, title, desc, expert=False, icon=None, parent=None):
        super().__init__(parent)
        self.config_item = config_item
        self.expert = expert
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet('CompactSwitchRow { background: transparent; }')

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(10)

        icon_widget = _make_icon(icon, self)
        if icon_widget is not None:
            layout.addWidget(icon_widget, 0, Qt.AlignVCenter)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self._title_label = StrongBodyLabel(title, self)
        self._title_label.setStyleSheet('font-size: 13px;')
        title_row.addWidget(self._title_label)
        if expert:
            badge = CaptionLabel(tr['Setting']['ExpertBadge'], self)
            badge.setStyleSheet(
                'color:#854F0B; background:#FAEEDA; border-radius:7px; padding:0 7px; font-size:10px;')
            title_row.addWidget(badge)
        title_row.addStretch(1)
        layout.addLayout(title_row, 1)
        self.switch = SwitchButton(self)
        self.switch.setChecked(bool(config_item.value))
        self.switch.checkedChanged.connect(self._on_toggle)
        layout.addWidget(self.switch)
        set_row_tooltip(desc, self, self._title_label, self.switch)

    def _on_toggle(self, checked):
        config.set(self.config_item, bool(checked))

    def refresh_row(self):
        self.switch.setChecked(bool(self.config_item.value))


class CompactComboRow(QFrame):
    """紧凑下拉参数行：图示 + 参数名 | 下拉框

    texts 为显示文案列表，values 为对应的配置值列表（缺省与 texts 相同）；
    on_user_changed 在用户切换后回调（如语言切换的重启确认）。
    """

    def __init__(self, config_item, title, desc, texts, values=None,
                 on_user_changed=None, icon=None, parent=None):
        super().__init__(parent)
        self.config_item = config_item
        self.on_user_changed = on_user_changed
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet('CompactComboRow { background: transparent; }')
        self._texts = list(texts)
        self._values = list(values) if values is not None else self._texts

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(10)

        icon_widget = _make_icon(icon, self)
        if icon_widget is not None:
            layout.addWidget(icon_widget, 0, Qt.AlignVCenter)

        self._title_label = StrongBodyLabel(title, self)
        self._title_label.setStyleSheet('font-size: 13px;')
        layout.addWidget(self._title_label, 1)

        self.combo = ComboBox(self)
        self.combo.addItems(self._texts)
        self.combo.setFixedWidth(150)
        cur = config_item.value
        idx = self._values.index(cur) if cur in self._values else 0
        self.combo.setCurrentIndex(idx)
        self.combo.currentIndexChanged.connect(self._on_index)
        layout.addWidget(self.combo, 0)
        set_row_tooltip(desc, self, self._title_label, self.combo)

    def _on_index(self, idx):
        if 0 <= idx < len(self._values):
            config.set(self.config_item, self._values[idx])
        if self.on_user_changed is not None:
            self.on_user_changed()

    def refresh_row(self):
        cur = self.config_item.value
        if cur in self._values:
            self.combo.setCurrentIndex(self._values.index(cur))


class CompactParamRow(QFrame):
    """一行多个参数簇：水平均分（参照设计稿一行多参数的紧凑布局）。

    传入若干 CompactRangeRow / CompactSwitchRow / CompactComboRow，
    每个簇等宽伸展；行内的「已修改」标记等行为不受影响。
    """

    def __init__(self, *rows, parent=None):
        super().__init__(parent)
        self.rows = list(rows)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet('CompactParamRow { background: transparent; }')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(16)
        for r in rows:
            lay.addWidget(r, 1)

    def refresh_row(self):
        for r in self.rows:
            if hasattr(r, 'refresh_row'):
                r.refresh_row()
