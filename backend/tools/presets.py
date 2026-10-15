# -*- coding: utf-8 -*-
"""参数预设（功能增强计划 #5）。

内置三档（动画/真人/电影）+ 自定义；预设值落 config/presets.json（用户可编辑，
缺失键自动用内置默认补齐），覆盖项为跨算法/检测/分段的高频组合参数。
应用 = config.set 逐项写值（枚举经 serializer.deserialize 还原为成员，
避免裸字符串被 OptionsValidator.correct 静默回退到 options[0]）。
"""
import json
import os
from datetime import datetime

from backend.config import config

#: 项目根（backend/tools/presets.py → 上三级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRESETS_FILE = os.path.join(_PROJECT_ROOT, 'config', 'presets.json')

#: 预设档位（有序）与覆盖项 ConfigItem（key 即 config.json 的 "Group.Name"）
PRESET_ORDER = ['animation', 'real', 'film']
COVERED_ITEMS = [
    config.inpaintMode,
    config.subtitleDetectMode,
    config.residueCheck,
    config.autoRouteStaticThreshold,
    config.segmentSeconds,
    config.subtitleAreaDeviationPixel,
    config.minimaxMaxLoadNum,
    config.minimaxNumInferenceSteps,
    config.minimaxIterations,
    config.minimaxProcessWidth,
    config.minimaxStripPadding,
    config.minimaxBlockOverlap,
    config.minimaxVaeDecodeFp32,
]
_ITEMS = {item.key: item for item in COVERED_ITEMS}

# 内置参考值（2026-10-14/15 实测定标；值均为 config.json 的序列化形态）：
# - 动画：检测精准 + AUTO 路由 + 残留二修（敏感参数已内置）；ProcessWidth=960
#   只约束全帧透传的最坏显存（正常条带分辨率小，不受影响）；扩张 8 保描边
# - 真人：STTN 性价比主力；检测精准（风格化字幕保险）；MiniMax 组保持默认
# - 电影（黑边/宽银幕）：AUTO + 短分段（可中断续跑粒度细）；ProcessWidth=1280
#   压 4K/宽幅全帧透传的显存峰值
DEFAULT_PRESETS = {
    'animation': {
        'Main.InpaintMode': 'auto',
        'Main.SubtitleDetectMode': 'PP_OCRv5_SERVER',
        'Main.ResidueCheck': True,
        'Main.AutoRouteStaticThreshold': 1,
        'Main.SegmentSeconds': 600,
        'Main.SubtitleAreaDeviationPixel': 20,
        'MiniMax.MaxLoadNum': 121,
        'MiniMax.NumInferenceSteps': 8,
        'MiniMax.Iterations': 8,
        'MiniMax.ProcessWidth': 960,
        'MiniMax.StripPadding': 16,
        'MiniMax.BlockOverlap': 8,
        'MiniMax.VaeDecodeFp32': True,
    },
    'real': {
        'Main.InpaintMode': 'sttn-auto',
        'Main.SubtitleDetectMode': 'PP_OCRv5_SERVER',
        'Main.ResidueCheck': True,
        'Main.AutoRouteStaticThreshold': 1,
        'Main.SegmentSeconds': 600,
        'Main.SubtitleAreaDeviationPixel': 20,
        'MiniMax.MaxLoadNum': 121,
        'MiniMax.NumInferenceSteps': 8,
        'MiniMax.Iterations': 6,
        'MiniMax.ProcessWidth': 0,
        'MiniMax.StripPadding': 128,
        'MiniMax.BlockOverlap': 8,
        'MiniMax.VaeDecodeFp32': True,
    },
    'film': {
        'Main.InpaintMode': 'auto',
        'Main.SubtitleDetectMode': 'PP_OCRv5_SERVER',
        'Main.ResidueCheck': True,
        'Main.AutoRouteStaticThreshold': 1,
        'Main.SegmentSeconds': 30,
        'Main.SubtitleAreaDeviationPixel': 20,
        'MiniMax.MaxLoadNum': 121,
        'MiniMax.NumInferenceSteps': 8,
        'MiniMax.Iterations': 6,
        'MiniMax.ProcessWidth': 1280,
        'MiniMax.StripPadding': 128,
        'MiniMax.BlockOverlap': 8,
        'MiniMax.VaeDecodeFp32': True,
    },
}


def load_presets():
    """读 config/presets.json；缺失/损坏/缺键时用内置默认补齐并回写。

    用户可手改该文件调预设（重启生效）；未知键忽略，不污染内置清单。
    """
    data = {}
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            data = {}
    raw = data.get('presets') if isinstance(data, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    out = {}
    changed = not os.path.exists(PRESETS_FILE)
    for pid in PRESET_ORDER:
        vals = raw.get(pid)
        if not isinstance(vals, dict):
            out[pid] = dict(DEFAULT_PRESETS[pid])
            changed = True
            continue
        merged = {k: vals.get(k, v) for k, v in DEFAULT_PRESETS[pid].items()}
        if merged != vals:
            changed = True
        out[pid] = merged
    if changed:
        try:
            save_presets(out)
        except OSError:
            pass  # 只读环境：内存预设仍可用
    return out


def save_presets(presets):
    """预设原子写盘（.tmp + os.replace）。"""
    payload = {
        'version': 1,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'presets': presets,
    }
    os.makedirs(os.path.dirname(PRESETS_FILE), exist_ok=True)
    tmp = f'{PRESETS_FILE}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PRESETS_FILE)


def current_values():
    """当前配置的序列化快照 {key: value}（与 presets.json 同形态，便于比对）。"""
    return {item.key: item.serialize() for item in COVERED_ITEMS}


def match_current(presets):
    """当前配置与哪档预设完全一致；不一致=custom。"""
    cur = current_values()
    for pid in PRESET_ORDER:
        if all(cur.get(k) == v for k, v in presets[pid].items()):
            return pid
    return 'custom'


def apply_preset(preset_id, presets=None):
    """把预设写入配置（config.set 即时落盘 config.json）；未知键跳过。

    枚举值必须经 item.serializer.deserialize 还原为枚举成员：
    直接给字符串会被 OptionsValidator.correct 静默回退到 options[0]。
    """
    presets = presets or load_presets()
    vals = presets.get(preset_id)
    if not isinstance(vals, dict):
        raise KeyError(f'unknown preset: {preset_id}')
    for key, value in vals.items():
        item = _ITEMS.get(key)
        if item is None:
            continue
        try:
            value = item.serializer.deserialize(value)
        except Exception:
            continue  # 非法值跳过，保留当前值
        config.set(item, value)
