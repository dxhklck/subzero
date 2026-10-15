# -*- coding: utf-8 -*-
"""H.264 编码器解析：探测本机真正可用的编码器，失败自动降级。

设计原则（与 hardware_accelerator 里 paddle 的 GPU 冒烟测试同一哲学）：
编码器可用性不能按显卡品牌静态判断——
  - Optimus 笔记本渲染走核显，但 NVENC 照样可用；
  - 有 N 卡但驱动过旧时，ffmpeg 的 NVENC API 初始化直接失败；
  - QSV 还要求 BIOS 使能核显；AMF 依赖 AMD 驱动。
唯一可靠的办法是让 ffmpeg 用**与正式运行完全相同的参数**真编几帧，
rc=0 才入选（参数不被某编码器支持也会在探测期暴露，不会带进正式运行）。

候选链（按优先级）：
    h264_nvenc → h264_qsv → h264_amf → libx264（保底，永远可用）
实测（1080p 噪声内容 300 帧）：NVENC p5 约 253fps，libx264 veryfast 约 72fps；
纯 CPU 机器落在保底项，与旧行为完全一致（不会变慢，只是没有硬件加速）。

探测结果按 override 值分别缓存：主流程 / 残留二遍 / 分段编码必须命中同一
编码器（分段 concat 的 -c copy 以编码器参数一致为前提）；用户中途改设置
换 override 键会重新探测，不会沿用上一个任务的编码器。
"""

import subprocess
import threading

from .common_tools import SUBPROCESS_CREATION_FLAGS
from .ffmpeg_cli import FFmpegCLI

# 候选链：编码器名 -> 与正式运行完全一致的编码参数（质量对齐 crf20 档）
_ENCODER_CANDIDATES = (
    ('h264_nvenc', ('-preset', 'p5', '-rc', 'vbr', '-cq', '21')),
    ('h264_qsv', ('-preset', 'medium', '-global_quality', '21')),
    ('h264_amf', ('-quality', 'quality', '-rc', 'cqp', '-qp_i', '21', '-qp_p', '21')),
    ('libx264', ('-preset', 'veryfast', '-crf', '20')),
)

# 探测帧尺寸：必须 >= 硬件编码器最小分辨率。
# ⚠ NVENC 对 128x128 直接 InitializeEncoder failed
# （Frame Dimension less than the minimum supported value），实测 256x256 通过
_PROBE_SIZE = (256, 256)

_cache_lock = threading.Lock()
_cache = {}  # override -> (encoder_name, params)，进程内按 override 分别缓存


def _probe(encoder, params):
    """用与正式运行相同的参数真编 6 帧小视频，rc=0 才算可用（约 100-300ms）。"""
    w, h = _PROBE_SIZE
    cmd = [FFmpegCLI.instance().ffmpeg_path,
           '-hide_banner', '-loglevel', 'error',
           '-f', 'lavfi', '-i', f'color=black:s={w}x{h}:r=30:d=0.2',
           '-c:v', encoder, *params, '-f', 'null', '-']
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30,
                           creationflags=SUBPROCESS_CREATION_FLAGS)
        return r.returncode == 0
    except Exception:
        return False


def resolve_h264_encoder(override=None):
    """解析本次运行使用的 H.264 编码器，返回 (encoder_name, params)。

    override 为 None/'auto' 时按候选链依次探测；
    为具体编码器名（如 'h264_nvenc'，或 EncoderMode 枚举成员）时优先探测它，
    本机跑不动则自动降级到候选链下一个——用户强制指定硬件编码器但机器不支持时
    降级而不是让任务失败。
    """
    # 兼容 Enum 成员与字符串两种传入（config 里取出的是枚举成员）
    key = getattr(override, 'value', override) or 'auto'
    with _cache_lock:
        if key in _cache:
            return _cache[key]
        chain = list(_ENCODER_CANDIDATES)
        if key != 'auto':
            # 强制指定：提到链首（保底项仍垫底）；无法识别的值按 auto 处理
            chain.sort(key=lambda kv: kv[0] != key)
        for name, params in chain:
            if _probe(name, params):
                _cache[key] = (name, params)
                return _cache[key]
        # 理论上不可达（libx264 永远可用）；兜底不探测直接返回
        name, params = _ENCODER_CANDIDATES[-1]
        _cache[key] = (name, params)
        return _cache[key]


def reset_encoder_cache():
    """清空探测缓存（测试用；保证改设置后重新探测）。"""
    with _cache_lock:
        _cache.clear()
