# -*- coding: utf-8 -*-
"""系统状态行（嵌入处理模型卡）：显存/内存 两行，图示 + 名称 | 细进度条 | 动态数值"""
import subprocess

import psutil
from PySide6.QtWidgets import (QWidget, QLabel, QProgressBar, QVBoxLayout,
                               QHBoxLayout, QSizePolicy)
from PySide6.QtCore import QThread, Signal, Qt
from qfluentwidgets import (isDarkTheme, qconfig, FluentIcon, StrongBodyLabel,
                           IconWidget)
from backend.tools.common_tools import SUBPROCESS_CREATION_FLAGS
from backend.config import tr

VRAM_C = "#9B7BF7"
RAM_C = "#2ECC71"


def _theme_colors():
    if isDarkTheme():
        return {"muted": "#9A9992", "track": "rgba(255,255,255,0.08)",
                "value": "#C8C8C8"}
    return {"muted": "#77766E", "track": "rgba(0,0,0,0.08)",
            "value": "#333333"}


class GpuSamplerThread(QThread):
    """1 秒轮询 nvidia-smi + psutil，发监控数据"""
    stats_ready = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True

    def run(self):
        while self._running:
            stats = self._sample()
            if stats:
                self.stats_ready.emit(stats)
            self.sleep(1)

    def stop(self):
        self._running = False
        self.wait(2000)

    @staticmethod
    def _sample():
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.used,memory.total,temperature.gpu',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5,
                creationflags=SUBPROCESS_CREATION_FLAGS)
            nums = [p.strip() for p in result.stdout.strip().split(',') if p.strip()]
            nums = [p for p in nums if p.replace('.', '').isdigit()]
            if len(nums) < 3:
                return None
            used, total, temp = (int(float(n)) for n in nums[:3])
            mem = psutil.virtual_memory()
            return {
                'vram_used': used, 'vram_total': total, 'gpu_temp': temp,
                'ram_used': mem.used // (1024 ** 2), 'ram_total': mem.total // (1024 ** 2),
            }
        except Exception:
            return None


def _bar(color):
    """细进度条：只画数值，不带文字"""
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    bar.setMinimumWidth(56)
    bar.setStyleSheet(
        "QProgressBar { background: transparent; border: none; }"
        f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}")
    return bar


class _StatRow(QWidget):
    """单行：图示 + 名称 | 进度条 | 动态数值（与处理模型行同构）"""

    def __init__(self, icon, title, color, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(10)

        self.icon_widget = IconWidget(icon, self)
        self.icon_widget.setFixedSize(16, 16)
        lay.addWidget(self.icon_widget, 0, Qt.AlignVCenter)

        self.title_label = StrongBodyLabel(title, self)
        self.title_label.setStyleSheet('font-size: 13px;')
        lay.addWidget(self.title_label, 0, Qt.AlignVCenter)

        self.bar = _bar(color)
        lay.addWidget(self.bar, 1, Qt.AlignVCenter)

        self.value_label = QLabel("—")
        self.value_label.setStyleSheet("font-size:11px; font-family:'Consolas';")
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setMinimumWidth(112)
        lay.addWidget(self.value_label, 0, Qt.AlignVCenter)


class GpuMonitorBar(QWidget):
    """显存/内存两行动态监控（置于处理模型行上方，无自带底色）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(260)  # 允许窄窗口下收缩，不与预览争宽度
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(0)
        self.vram_row = _StatRow(FluentIcon.TILES, tr["Setting"]["VramTag"], VRAM_C)
        self.ram_row = _StatRow(FluentIcon.SAVE, tr["Setting"]["RamTag"], RAM_C)
        lay.addWidget(self.vram_row)
        lay.addWidget(self.ram_row)

        self._apply_theme()
        qconfig.themeChanged.connect(self._apply_theme)

        self._thread = GpuSamplerThread(self)
        self._thread.stats_ready.connect(self.update_stats)
        self._thread.start()

    def _apply_theme(self):
        c = _theme_colors()
        # 标题样式与「处理模型」行一致：13px 默认文字色，不用灰弱色
        for row in (self.vram_row, self.ram_row):
            row.title_label.setStyleSheet('font-size: 13px;')
            row.value_label.setStyleSheet(
                f"font-size:11px; font-family:'Consolas'; color:{c['value']};")
            row.bar.setStyleSheet(
                f"QProgressBar {{ background: {c['track']}; border: none; border-radius: 3px; }}")
        self.vram_row.bar.setStyleSheet(
            f"QProgressBar {{ background: {c['track']}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {VRAM_C}; border-radius: 3px; }}")
        self.ram_row.bar.setStyleSheet(
            f"QProgressBar {{ background: {c['track']}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {RAM_C}; border-radius: 3px; }}")

    def update_stats(self, s):
        self.vram_row.bar.setValue(int(s['vram_used'] / max(s['vram_total'], 1) * 100))
        self.vram_row.value_label.setText(
            f"{s['vram_used'] / 1024:.1f}/{s['vram_total'] / 1024:.1f}G · {s['gpu_temp']}°C")
        self.ram_row.bar.setValue(int(s['ram_used'] / max(s['ram_total'], 1) * 100))
        self.ram_row.value_label.setText(
            f"{s['ram_used'] / 1024:.1f}/{s['ram_total'] / 1024:.1f}G")

    def update_speed(self, fps):
        """已移除速度显示，保留接口兼容（no-op）"""
        pass

    def reset_peak(self):
        pass  # 精简版无峰值显示，保留接口兼容

    def stop(self):
        self._thread.stop()
