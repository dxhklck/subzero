# -*- coding: utf-8 -*-
"""
@desc: 净影 SubZero 模型权重完整性启动扫描
       对照 bootstrap.WEIGHT_MANIFEST 逐项检查 backend/models/ 下权重文件的
       存在性与体积。权重按需加载（默认 STTN 不报错），被杀毒软件静默删除/
       截断后问题暴露滞后（切到 LAMA/MiniMax 才报 open file failed），
       故在 GUI 启动时主动扫描，早发现、早提示。
       只读 bootstrap 清单与路径解析，不修改其行为。
       CLI: python backend/tools/model_scanner.py
"""
import os
import sys

# 允许 `python backend/tools/model_scanner.py` 直接以 CLI 运行：
# 把项目根目录补进 sys.path（根目录无标准库遮蔽风险，勿插 backend/tools）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.tools.bootstrap import WEIGHT_MANIFEST, _entry_abs_path  # noqa: E402

# 体积容差：实际体积 < 期望下限*(1-1%) 视为异常。
# 说明：清单 min_mb 是保守下限（如 big-lama 实际 196MB > 下限 190MB 属正常），
# 故只做向下判定；明显偏小即截断/替换特征（杀毒有时替换而非删除）。
SIZE_TOLERANCE = 0.01

# 分组展示名（按清单 path 首段归类，供摘要按算法分组）
GROUPS = {
    'V5': '字幕检测 PP-OCRv5',
    'big-lama': 'LAMA 修复',
    'sttn-auto': 'STTN 智能擦除',
    'sttn-det': 'STTN 字幕检测',
    'minimax-remover': 'MiniMax 修复',
    'ffmpeg': 'FFmpeg',
}


def _group_of(rel_path):
    """权重路径 -> 算法分组名"""
    if rel_path.startswith('backend/ffmpeg'):
        return GROUPS['ffmpeg']
    return GROUPS.get(rel_path.split('/')[0], rel_path.split('/')[0])


def scan_weights(manifest=None):
    """逐项核对权重文件存在且体积达标。

    返回 (ok_list, missing_list, size_mismatch_list)：
    - ok_list:            [(rel_path, actual_mb)]
    - missing_list:       [{'path','abs_path','expected_mb'}]   文件不存在（被杀软删除）
    - size_mismatch_list: [{'path','abs_path','expected_mb','actual_mb'}]  体积偏小（截断/替换）
    清单中 min_mb==0 的配置类条目只查存在性，不比体积。
    """
    manifest = WEIGHT_MANIFEST if manifest is None else manifest
    ok_list, missing_list, mismatch_list = [], [], []
    for entry in manifest:
        rel = entry['path']
        abs_path = _entry_abs_path(entry)
        min_mb = entry.get('min_mb', 0)
        if not os.path.isfile(abs_path):
            missing_list.append({'path': rel, 'abs_path': abs_path, 'expected_mb': min_mb})
            continue
        actual_bytes = os.path.getsize(abs_path)
        if min_mb > 0 and actual_bytes < min_mb * 1024 * 1024 * (1 - SIZE_TOLERANCE):
            mismatch_list.append({
                'path': rel, 'abs_path': abs_path, 'expected_mb': min_mb,
                'actual_mb': round(actual_bytes / 1024 / 1024, 2)})
        else:
            ok_list.append((rel, round(actual_bytes / 1024 / 1024, 2)))
    return ok_list, missing_list, mismatch_list


def format_report(ok_list, missing_list, mismatch_list):
    """按算法分组的人类可读摘要（供日志窗 / InfoBar）"""
    total_bad = len(missing_list) + len(mismatch_list)
    if not total_bad:
        return f'权重完整性扫描：{len(ok_list)} 项全部通过'
    lines = [f'权重完整性扫描：{len(ok_list)} 项正常，{total_bad} 项异常'
             '（可能被杀毒软件隔离/截断，请按 使用教程 手动下载补齐）']
    groups = {}
    for m in missing_list:
        groups.setdefault(_group_of(m['path']), []).append(('missing', m))
    for m in mismatch_list:
        groups.setdefault(_group_of(m['path']), []).append(('mismatch', m))
    for gname, items in groups.items():
        lines.append(f'【{gname}】')
        for kind, item in items:
            if kind == 'missing':
                lines.append(f"❌ 缺失: {item['path']}（期望 ≥{item['expected_mb']}MB）")
            else:
                lines.append(f"⚠ 大小异常: {item['path']} 实际 {item['actual_mb']}MB"
                             f" / 期望 ≥{item['expected_mb']}MB")
    return '\n'.join(lines)


def main():
    ok, missing, mismatch = scan_weights()
    print(format_report(ok, missing, mismatch))
    sys.exit(0 if not missing and not mismatch else 1)


if __name__ == '__main__':
    main()
