# -*- coding: utf-8 -*-
"""GPU 状态栏监控：窗口底部一行，显示 显存(+峰值)/GPU利用率/内存/处理速度"""
import subprocess

import psutil
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QColor

from qfluentwidgets import ProgressRing

from backend.tools.common_tools import SUBPROCESS_CREATION_FLAGS

MUTED = "#A9A8A0"
ACCENT = "#1D9E75"


class GpuSamplerThread(QThread):
    """1 秒轮询 nvidia-smi + psutil，发监控数据"""
    stats_ready = Signal(dict)  # {vram_used, vram_total, gpu_util, ram_used, ram_total}

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
                ['nvidia-smi', '--query-gpu=memory.used,memory.total,utilization.gpu',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5,
                creationflags=SUBPROCESS_CREATION_FLAGS)
            parts = [p.strip() for p in result.stdout.strip().split(',')]
            if len(parts) < 3:
                return None
            vram_used, vram_total, gpu_util = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
            mem = psutil.virtual_memory()
            return {
                'vram_used': vram_used, 'vram_total': vram_total,
                'gpu_util': gpu_util,
                'ram_used': mem.used // (1024 ** 2), 'ram_total': mem.total // (1024 ** 2),
            }
        except Exception:
            return None


def _chip(label, mono=True):
    wrap = QLabel()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    tag = QLabel(label)
    tag.setStyleSheet(f"color:{MUTED}; font-size:11px;")
    value = QLabel("—")
    value.setStyleSheet("font-size:12px; font-family:'Consolas';")
    lay.addWidget(tag)
    lay.addWidget(value)
    wrap.value_label = value
    return wrap


class GpuMonitorBar(QWidget):
    """底部一行监控栏：显存(含本次峰值)/GPU/内存/速度"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._peak_vram = 0  # 本次会话采样到的显存峰值 MB
        self.setFixedHeight(26)
        self.setStyleSheet("background: rgba(255,255,255,0.04); border-radius: 6px;")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 2, 12, 2)
        lay.setSpacing(16)

        # 显存 chip：数值 + 迷你进度条
        self.vram_chip = QLabel()
        vlay = QHBoxLayout(self.vram_chip)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(5)
        tag = QLabel("显存")
        tag.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        self.vram_value = QLabel("—")
        self.vram_value.setStyleSheet("font-size:12px; font-family:'Consolas';")
        self.vram_bar = QLabel()
        self.vram_bar.setFixedSize(56, 5)
        self.vram_bar.setStyleSheet("background:#444441; border-radius:2px;")
        self.vram_fill = QLabel(self.vram_bar)
        self.vram_fill.setStyleSheet(f"background:{ACCENT}; border-radius:2px;")
        self.peak_label = QLabel("峰 —")
        self.peak_label.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-family:'Consolas';")
        vlay.addWidget(tag)
        vlay.addWidget(self.vram_value)
        vlay.addWidget(self.vram_bar)
        vlay.addWidget(self.peak_label)
        lay.addWidget(self.vram_chip)

        self.gpu_chip = _chip("GPU")
        self.ram_chip = _chip("内存")
        self.fps_chip = _chip("速度")
        lay.addWidget(self.gpu_chip)
        lay.addWidget(self.ram_chip)
        lay.addWidget(self.fps_chip)

        lay.addStretch(1)
        self.device_label = QLabel("")
        self.device_label.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        lay.addWidget(self.device_label)

        self._thread = GpuSamplerThread(self)
        self._thread.stats_ready.connect(self.update_stats)
        self._thread.start()

    def set_device_name(self, name):
        self.device_label.setText(name)

    def update_stats(self, s):
        used, total = s['vram_used'], s['vram_total']
        if used > self._peak_vram:
            self._peak_vram = used
        self.vram_value.setText(f"{used / 1024:.1f} / {total / 1024:.1f} GB")
        ratio = max(0, min(1, used / max(total, 1)))
        self.vram_fill.setFixedSize(max(2, int(56 * ratio)), 5)
        self.peak_label.setText(f"峰 {self._peak_vram / 1024:.1f}G")
        self.gpu_chip.value_label.setText(f"{s['gpu_util']}%")
        self.gpu_chip.value_label.setStyleSheet(
            f"font-size:12px; font-family:'Consolas'; color:{ACCENT if s['gpu_util'] > 5 else MUTED};")
        self.ram_chip.value_label.setText(f"{s['ram_used'] / 1024:.1f} / {s['ram_total'] / 1024:.1f} GB")

    def update_speed(self, fps):
        """由进度回调喂入处理速度（帧/s），EMA 平滑在外部做"""
        self.fps_chip.value_label.setText(f"{fps:.1f} 帧/s" if fps else "—")

    def reset_peak(self):
        self._peak_vram = 0
        self.peak_label.setText("峰 —")

    def stop(self):
        self._thread.stop()
