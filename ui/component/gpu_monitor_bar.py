# -*- coding: utf-8 -*-
"""系统状态监控（2×2 进度图，嵌入状态卡）：显存/内存 用量条 + GPU/CPU 利用率条"""
import subprocess

import psutil
from PySide6.QtWidgets import (QWidget, QLabel, QFrame, QProgressBar, QGridLayout,
                               QSizePolicy)
from PySide6.QtCore import QThread, Signal, Qt
from qfluentwidgets import isDarkTheme, qconfig

from backend.tools.common_tools import SUBPROCESS_CREATION_FLAGS
from backend.config import tr

VRAM_C = "#9B7BF7"
RAM_C = "#2ECC71"
GPU_C = "#1D9E75"
CPU_C = "#5B8DEF"
CHIP_W, CHIP_H = 10, 10


def _theme_colors():
    if isDarkTheme():
        return {"muted": "#9A9992", "card": "rgba(255,255,255,0.05)",
                "border": "rgba(255,255,255,0.10)", "track": "rgba(255,255,255,0.08)"}
    return {"muted": "#77766E", "card": "rgba(0,0,0,0.03)",
            "border": "rgba(0,0,0,0.10)", "track": "rgba(0,0,0,0.08)"}


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
                ['nvidia-smi', '--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5,
                creationflags=SUBPROCESS_CREATION_FLAGS)
            parts = [p.strip() for p in result.stdout.strip().split(',') if p.strip()]
            # 防御式解析：取前 4 个数字为 used/total/util/temp（nvidia-smi 偶发省略 name 也不怕）
            nums = [p for p in parts if p.replace('.', '').isdigit()]
            if len(nums) < 4:
                return None
            used, total, util, temp = (int(float(n)) for n in nums[:4])
            name = next((p for p in parts if p not in nums), '')
            mem = psutil.virtual_memory()
            return {
                'gpu_name': name.replace('NVIDIA ', ''),
                'vram_used': used, 'vram_total': total,
                'gpu_util': util, 'gpu_temp': temp,
                'ram_used': mem.used // (1024 ** 2), 'ram_total': mem.total // (1024 ** 2),
                'cpu_util': int(psutil.cpu_percent(interval=None)),
            }
        except Exception:
            return None


def _chip(color):
    """静态小色块（不随用量变化）"""
    chip = QFrame()
    chip.setFixedSize(CHIP_W, CHIP_H)
    chip.setStyleSheet(f"background:{color}; border-radius:3px;")
    return chip


def _bar(color):
    """细进度条：只画数值，不带文字"""
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    bar.setStyleSheet(
        "QProgressBar { background: transparent; border: none; }"
        f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}")
    return bar


def _tag_label(text):
    lab = QLabel(text)
    lab.setStyleSheet("font-size:11px;")
    return lab


def _value_label():
    lab = QLabel("—")
    lab.setStyleSheet("font-size:11px; font-family:'Consolas';")
    lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lab.setMinimumWidth(96)
    return lab


class GpuMonitorBar(QWidget):
    """系统状态 2×2 进度图：显存 | GPU利用率 // 内存 | CPU利用率（嵌入状态卡，无自带底色）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(260)  # 允许窄窗口下收缩，不与预览争宽度
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        grid = QGridLayout(self)
        grid.setContentsMargins(14, 8, 14, 8)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(7)
        # 列：0 色块 1 名称 2 进度条 3 色块 4 名称 5 进度条 6 数值（进度条列拉伸）
        for col in (2, 5):
            grid.setColumnStretch(col, 1)

        self.vram_bar, self.vram_label = _bar(VRAM_C), _value_label()
        self.gpu_bar, self.gpu_label = _bar(GPU_C), _value_label()
        self.ram_bar, self.ram_label = _bar(RAM_C), _value_label()
        self.cpu_bar, self.cpu_label = _bar(CPU_C), _value_label()
        self._bars = {'vram': self.vram_bar, 'gpu': self.gpu_bar,
                      'ram': self.ram_bar, 'cpu': self.cpu_bar}
        self._bar_colors = {'vram': VRAM_C, 'gpu': GPU_C, 'ram': RAM_C, 'cpu': CPU_C}

        self._tag_labels = []

        def cell(tag, color, bar, val, row, col):
            grid.addWidget(_chip(color), row, col, Qt.AlignVCenter)
            tag_lab = _tag_label(tag)
            self._tag_labels.append(tag_lab)
            grid.addWidget(tag_lab, row, col + 1, Qt.AlignVCenter)
            grid.addWidget(bar, row, col + 2, Qt.AlignVCenter)
            grid.addWidget(val, row, col + 3, Qt.AlignVCenter)

        cell(tr["Setting"]["VramTag"], VRAM_C, self.vram_bar, self.vram_label, 0, 0)
        cell(tr["Setting"]["GpuUtilTag"], GPU_C, self.gpu_bar, self.gpu_label, 0, 3)
        cell(tr["Setting"]["RamTag"], RAM_C, self.ram_bar, self.ram_label, 1, 0)
        cell(tr["Setting"]["CpuUtilTag"], CPU_C, self.cpu_bar, self.cpu_label, 1, 3)

        self._apply_theme()
        qconfig.themeChanged.connect(self._apply_theme)

        self._thread = GpuSamplerThread(self)
        self._thread.stats_ready.connect(self.update_stats)
        self._thread.start()

    def _apply_theme(self):
        c = _theme_colors()
        for lab in self._tag_labels:
            lab.setStyleSheet(f"color:{c['muted']}; font-size:11px;")
        for name, bar in self._bars.items():
            bar.setStyleSheet(
                f"QProgressBar {{ background: {c['track']}; border: none; border-radius: 3px; }}"
                f"QProgressBar::chunk {{ background: {self._bar_colors[name]}; border-radius: 3px; }}")
        value_style = ("font-size:11px; font-family:'Consolas'; color:#C8C8C8;"
                       if isDarkTheme() else
                       "font-size:11px; font-family:'Consolas'; color:#333333;")
        for lab in (self.vram_label, self.ram_label, self.gpu_label, self.cpu_label):
            lab.setStyleSheet(value_style)

    def update_stats(self, s):
        self.vram_bar.setValue(int(s['vram_used'] / max(s['vram_total'], 1) * 100))
        self.vram_label.setText(
            f"{s['vram_used'] / 1024:.1f}/{s['vram_total'] / 1024:.1f}G · {s['gpu_temp']}°C")
        self.gpu_bar.setValue(int(s['gpu_util']))
        self.gpu_label.setText(f"{s['gpu_util']}%")
        self.ram_bar.setValue(int(s['ram_used'] / max(s['ram_total'], 1) * 100))
        self.ram_label.setText(f"{s['ram_used'] / 1024:.1f}/{s['ram_total'] / 1024:.1f}G")
        self.cpu_bar.setValue(int(s['cpu_util']))
        self.cpu_label.setText(f"{s['cpu_util']}%")

    def update_speed(self, fps):
        """已移除速度显示，保留接口兼容（no-op）"""
        pass

    def reset_peak(self):
        pass  # 精简版无峰值显示，保留接口兼容

    def stop(self):
        self._thread.stop()
