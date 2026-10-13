# -*- coding: utf-8 -*-
"""GPU 监控（单行卡片式）：显存+内存 同行静态色块 + 数字/占比/温度，置于右栏处理模型上方"""
import subprocess

import psutil
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame, QSizePolicy
from PySide6.QtCore import QThread, Signal, Qt
from qfluentwidgets import isDarkTheme, qconfig

from backend.tools.common_tools import SUBPROCESS_CREATION_FLAGS
from backend.config import tr

VRAM_C = "#9B7BF7"
RAM_C = "#2ECC71"
CHIP_W, CHIP_H = 14, 14


def _theme_colors():
    if isDarkTheme():
        return {"muted": "#9A9992", "card": "rgba(255,255,255,0.05)",
                "border": "rgba(255,255,255,0.10)"}
    return {"muted": "#77766E", "card": "rgba(0,0,0,0.03)",
            "border": "rgba(0,0,0,0.10)"}


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
    chip.setStyleSheet(f"background:{color}; border-radius:4px;")
    return chip


def _value_label():
    lab = QLabel("—")
    lab.setStyleSheet("font-size:12px; font-family:'Consolas';")
    return lab


class GpuMonitorBar(QWidget):
    """GPU 监控单行卡：显存+内存 同行（静态色块 + 数字/占比/温度）"""

    def __init__(self, parent=None, framed=True):
        super().__init__(parent)
        # 普通 QWidget 默认不画样式表背景，必须开启才能呈现整块卡片底色/边框
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(36)
        self.setMinimumWidth(260)  # 允许窄窗口下收缩（文本省略），不与预览争宽度
        # framed=False：嵌入外层卡片（与处理模型同卡），不画自带底色/边框
        self._framed = framed

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(8)

        # 显存段
        self.vram_tag = QLabel(tr["Setting"]["VramTag"])
        self.vram_chip = _chip(VRAM_C)
        self.vram_label = _value_label()
        # 内存段
        self.ram_tag = QLabel(tr["Setting"]["RamTag"])
        self.ram_chip = _chip(RAM_C)
        self.ram_label = _value_label()

        lay.addWidget(self.vram_tag)
        lay.addWidget(self.vram_chip)
        lay.addWidget(self.vram_label)
        lay.addSpacing(16)
        lay.addWidget(self.ram_tag)
        lay.addWidget(self.ram_chip)
        lay.addWidget(self.ram_label)
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        self._apply_theme()
        qconfig.themeChanged.connect(self._apply_theme)

        self._thread = GpuSamplerThread(self)
        self._thread.stats_ready.connect(self.update_stats)
        self._thread.start()

    def _apply_theme(self):
        c = _theme_colors()
        self.vram_tag.setStyleSheet(f"color:{c['muted']}; font-size:11px;")
        self.ram_tag.setStyleSheet(f"color:{c['muted']}; font-size:11px;")
        self.vram_label.setStyleSheet("font-size:12px; font-family:'Consolas';")
        self.ram_label.setStyleSheet("font-size:12px; font-family:'Consolas';")
        if not self._framed:
            self.setStyleSheet("background: transparent; border: none;")
            return
        # 用 ID 选择器只给卡片本体画底色/边框，子控件（标签/数字）不带任何框
        self.setObjectName("gpuMonitorBar")
        self.setStyleSheet(
            f"QWidget#gpuMonitorBar {{ background:{c['card']}; border:1px solid {c['border']}; border-radius:8px; }}")

    def update_stats(self, s):
        vram_ratio = s['vram_used'] / max(s['vram_total'], 1)
        ram_ratio = s['ram_used'] / max(s['ram_total'], 1)
        self.vram_label.setText(
            f"{s['vram_used'] / 1024:.1f} / {s['vram_total'] / 1024:.1f} GB"
            f" · {vram_ratio * 100:.0f}% {s['gpu_temp']}°C")
        self.ram_label.setText(
            f"{s['ram_used'] / 1024:.1f} / {s['ram_total'] / 1024:.1f} GB"
            f" · {ram_ratio * 100:.0f}%")

    def update_speed(self, fps):
        """已移除速度显示，保留接口兼容（no-op）"""
        pass

    def reset_peak(self):
        pass  # 精简版无峰值显示，保留接口兼容

    def stop(self):
        self._thread.stop()
