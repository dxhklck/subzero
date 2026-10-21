"""持久化边界契约测试。

回归目标（历史事故）：``ab_sections`` 在内存里是 ``range`` 对象，流入
``Checkpoint.save()`` 的 ``json.dumps`` 后抛
``TypeError: Object of type range is not JSON serializable``，分段任务在建
checkpoint 这一步直接崩掉、整片无法开始处理。

为什么以前没被发现：既有的分段测试用 ``{'sub_areas': [...]}``（无
``ab_sections``），既有的任务持久化测试单独测 ``_options_to_json``——
两个测试各自只覆盖一半，**从未覆盖「A/B 区间 × 分段」这个交叉组合**。
本文件就是补这个交叉组合。

跑法（无需 GPU / 模型，几秒完成）：
    cd G:/video-subtitle-remover
    QT_QPA_PLATFORM=offscreen ./python/python.exe tests/persistence_contract_test.py
也兼容 pytest：pytest tests/persistence_contract_test.py
"""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from backend.tools.common_tools import json_safe
from backend.tools.segment_executor import build_task_params
from backend.tools.segmenter import load_or_rebuild_checkpoint, params_hash

_passed = []
_failed = []


def check(name):
    """装饰器：跑一个用例，记录 PASS/FAIL（不中断其余用例）。"""
    def wrap(fn):
        try:
            fn()
            _passed.append(name)
            print(f'[PASS] {name}')
        except Exception as e:  # noqa: BLE001 - 测试收集所有失败
            _failed.append((name, e))
            print(f'[FAIL] {name}: {type(e).__name__}: {e}')
        return fn
    return wrap


# ---------------------------------------------------------------- 单元契约

@check('json_safe 递归规范化 range/tuple/set/Path/numpy/嵌套 dict')
def _():
    src = {
        'ab_sections': [range(0, 10), range(20, 30)],
        'sub_areas': [(1, 2, 3, 4)],
        'nested': {'r': range(5, 6), 't': (7, 8)},
        'st': {3, 1, 2},
        'path': Path('a/b'),
        'np': np.int64(9),
        'npf': np.float64(2.5),
        'plain': 'x',
        'flag': True,
        'none': None,
    }
    out = json_safe(src)
    json.dumps(out)  # 必须不抛
    assert out['ab_sections'] == [[0, 10], [20, 30]], out['ab_sections']
    assert out['sub_areas'] == [[1, 2, 3, 4]], out['sub_areas']
    assert out['nested'] == {'r': [5, 6], 't': [7, 8]}, out['nested']
    assert out['st'] == [1, 2, 3], out['st']
    assert out['np'] == 9 and isinstance(out['np'], int), out['np']
    assert out['npf'] == 2.5, out['npf']


@check('build_task_params 输出必须 JSON 可序列化（含 range）')
def _():
    options = {
        'ab_sections': [range(0, 100), range(200, 300)],
        'sub_areas': [(10, 20, 30, 40)],
        'inpaint_mode': 'minimax',
        'frame_start': 0,        # 段位置字段应被剔除
        'frame_end': 300,
        'frame_write_start': 0,
        'is_segment': True,
    }
    params = build_task_params(options, 'minimax')
    json.dumps(params)
    assert 'frame_start' not in params and 'is_segment' not in params, params
    assert params['ab_sections'] == [[0, 100], [200, 300]], params['ab_sections']


@check('params_hash 对 range 与等价 [[start, stop]] 同哈希（续跑判定不受表示形式影响）')
def _():
    h1 = params_hash(build_task_params({'ab_sections': [range(0, 10)]}, 'lama'))
    h2 = params_hash(build_task_params({'ab_sections': [[0, 10]]}, 'lama'))
    assert h1 == h2, f'{h1} != {h2}'
    assert params_hash(build_task_params({'ab_sections': [range(0, 10)]}, 'lama')) == h1


# ------------------------------------------- 交叉组合：A/B 区间 × 分段 checkpoint

@check('交叉组合：非空 A/B 区间 + 分段任务能建成 checkpoint 并可续跑')
def _():
    tmpdir = tempfile.mkdtemp(prefix='vsr_persist_')
    try:
        ckpt = os.path.join(tmpdir, 'video_no_sub_segments', 'checkpoint.json')
        # 模拟 GUI 内存态：ab_sections 是 range 列表，sub_areas 是 tuple 列表
        options = {'ab_sections': [range(0, 150), range(400, 600)],
                   'sub_areas': [(100, 200, 300, 400)]}
        params = build_task_params(options, 'minimax')
        params['segmentOverlapFrames'] = 2
        total_frames, seg_frames = 1000, 300

        cp, mode = load_or_rebuild_checkpoint(ckpt, params, total_frames,
                                              seg_frames, overlap=2)
        assert mode == 'created', mode
        assert os.path.isfile(ckpt), 'checkpoint.json 未落盘'
        assert len(cp.segments()) == 4, len(cp.segments())  # 1000/300 → 4 段

        data = json.loads(Path(ckpt).read_text(encoding='utf-8'))
        assert data['params']['ab_sections'] == [[0, 150], [400, 600]], data['params']
        assert data['params']['sub_areas'] == [[100, 200, 300, 400]], data['params']

        # 二次进入：哈希一致 → 走 loaded（续跑可用），不能误判参数变更
        _cp2, mode2 = load_or_rebuild_checkpoint(ckpt, params, total_frames,
                                                 seg_frames, overlap=2)
        assert mode2 == 'loaded', mode2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@check('真实视频：分段帧数换算后仍能建 checkpoint（用 test/test.mp4，不跑推理）')
def _():
    from backend.tools.segment_executor import (compute_segment_frames,
                                                probe_video_frames)
    video = ROOT / 'test' / 'test.mp4'
    if not video.is_file():
        print('       (跳过：test/test.mp4 不存在，仅本地测试素材)')
        return
    n, fps = probe_video_frames(str(video))
    assert n > 0 and fps > 0, (n, fps)
    seg_frames = compute_segment_frames(n, fps, 1)  # 1 秒/段 → 强制分段
    assert seg_frames, '短片也应能换算分段帧数'
    tmpdir = tempfile.mkdtemp(prefix='vsr_persist_real_')
    try:
        ckpt = os.path.join(tmpdir, 'x_no_sub_segments', 'checkpoint.json')
        options = {'ab_sections': [range(0, n)], 'sub_areas': [(1, 2, 3, 4)]}
        params = build_task_params(options, 'opencv')
        params['segmentOverlapFrames'] = 0
        cp, mode = load_or_rebuild_checkpoint(ckpt, params, n, seg_frames, overlap=0)
        assert mode == 'created', mode
        assert len(cp.segments()) > 1, f'应切成多段，实际 {len(cp.segments())}'
        json.loads(Path(ckpt).read_text(encoding='utf-8'))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ------------------------------------------------- 另一条持久化：tasks.json

@check('tasks.json 落盘：含 range/tuple/set 的任务选项不抛异常且可回环')
def _():
    from ui.component.task_list_component import (Task, TaskOptions, TaskStatus,
                                                  load_tasks_file, save_tasks_file)
    tmpdir = tempfile.mkdtemp(prefix='vsr_tasks_')
    try:
        path = os.path.join(tmpdir, 'tasks.json')
        task = Task(
            path=str(ROOT / 'test' / 'test.mp4'),
            name='t.mp4',
            progress=0,
            status=TaskStatus.PENDING,
            options={
                TaskOptions.AB_SECTIONS.value: [range(0, 100), range(200, 260)],
                TaskOptions.SUB_AREAS.value: [(10, 20, 30, 40)],
                'future_key': {'r': range(1, 2), 's': {5, 6}},
            },
        )
        save_tasks_file([task], path=path)   # 旧实现：非 AB 键里的 range 会让这里抛
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
        assert raw['tasks'][0]['options']['ab_sections'] == [[0, 100], [200, 260]], raw
        assert raw['tasks'][0]['options']['sub_areas'] == [[10, 20, 30, 40]], raw

        back, warnings, _total = load_tasks_file(path)
        assert len(back) == 1, (back, warnings)
        opts = back[0].options
        # 内存态必须还原成 range / tuple（显示组件按 .start/.stop 工作）
        assert all(isinstance(r, range) for r in opts[TaskOptions.AB_SECTIONS.value]), opts
        assert opts[TaskOptions.AB_SECTIONS.value][0] == range(0, 100), opts
        assert isinstance(opts[TaskOptions.SUB_AREAS.value][0], tuple), opts
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@check('快照完整性：处理中任务的时间/进度/开始时刻能落盘并回环')
def _():
    import time as _t
    from ui.component.task_list_component import (Task, TaskOptions, TaskStatus,
                                                  load_tasks_file, save_tasks_file)
    tmpdir = tempfile.mkdtemp(prefix='vsr_snap_')
    try:
        path = os.path.join(tmpdir, 'snap.json')
        task = Task(path=str(ROOT / 'test' / 'test.mp4'), name='t.mp4',
                    progress=37, status=TaskStatus.PROCESSING,
                    options={TaskOptions.AB_SECTIONS.value: [range(0, 100)]})
        task.started_at = _t.time() - 90      # 已跑 90 秒
        task.elapsed = 30.0                    # 之前续跑累计 30 秒
        save_tasks_file([task], path=path)
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
        rec = raw['tasks'][0]
        # 旧实现只存完成时定格的 elapsed：处理中恒 0 → 快照里处理时间整段丢失
        assert rec['elapsed'] >= 118, rec['elapsed']
        assert rec['started_at'] and rec['started_at'] > 0, rec
        assert rec['progress'] == 37, rec

        back, _warnings, total = load_tasks_file(path)
        assert len(back) == 1
        t2 = back[0]
        assert t2.progress == 37, t2.progress
        assert t2.elapsed >= 118, t2.elapsed
        assert t2.last_started_at and t2.started_at is None, (t2.last_started_at, t2.started_at)
        # 总耗时（队列级）必须包含处理中任务的已用时长
        assert total >= 118, total
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@check('快照完整性：分段任务按 checkpoint 帧进度还原完成度（不归零）')
def _():
    from backend.tools.segmenter import derive_resume_info
    from ui.component.task_list_component import Task, TaskOptions, TaskStatus, collect_segment_info
    tmpdir = tempfile.mkdtemp(prefix='vsr_snap_seg_')
    try:
        video = ROOT / 'test' / 'test.mp4'
        out_dir = os.path.join(tmpdir, 'out')
        os.makedirs(out_dir, exist_ok=True)
        stem = 'test'
        task_dir = os.path.join(out_dir, f'{stem}_no_sub_segments')
        os.makedirs(task_dir, exist_ok=True)
        # 手工造 checkpoint：4 段 × 250 帧，前 2 段完成（末段短，验证按帧而非按段）
        segs = [{'index': 0, 'skip': 0, 'cap': 250, 'overlap': 0, 'status': 'done'},
                {'index': 1, 'skip': 250, 'cap': 250, 'overlap': 2, 'status': 'done'},
                {'index': 2, 'skip': 500, 'cap': 250, 'overlap': 2, 'status': 'pending'},
                {'index': 3, 'skip': 750, 'cap': 100, 'overlap': 2, 'status': 'pending'}]
        ckpt = os.path.join(task_dir, 'checkpoint.json')
        Path(ckpt).write_text(json.dumps({
            'version': 1, 'param_hash': 'x', 'params': {},
            'total_frames': 850, 'segment_frames': 250, 'overlap': 2,
            'segments': segs}), encoding='utf-8')

        info = derive_resume_info(ckpt)
        assert info.done_count == 2 and info.total_segments == 4, (info.done_count, info.total_segments)
        assert info.done_frames == 500 and info.total_frames == 850, (info.done_frames, info.total_frames)
        assert 58.0 < info.progress_pct < 59.0, info.progress_pct   # 500/850

        task = Task(path=str(video), name='test.mp4', progress=0,
                    status=TaskStatus.PENDING, options={},
                    _output_path=os.path.join(out_dir, 'test_no_sub.mp4'))
        snap = collect_segment_info(task)
        assert snap and snap['done_frames'] == 500 and snap['total_frames'] == 850, snap
        assert 58.0 < snap['progress_pct'] < 59.0, snap
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@check('检测判重：静态画面里选区带内新出现字幕不得判为重复帧')
def _():
    from backend.tools.subtitle_detect import SubtitleDetect
    video = ROOT / 'test' / 'test.mp4'
    if not video.is_file():
        print('       (跳过：test/test.mp4 不存在)')
        return
    det = SubtitleDetect(str(video), [(300, 400, 100, 800)])  # (ymin,ymax,xmin,xmax)
    h, w = 1080, 1920
    f1 = np.full((h, w, 3), 40, np.uint8)
    det._update_frame_diff(1, f1)
    f2 = f1.copy()
    f2[320:350, 150:450] = 240        # 带内出现一条"字幕"（占整帧 0.43%）
    det._update_frame_diff(2, f2)
    global_diff = det.frame_diff_scores[2]
    band_diff = det.frame_band_diff_scores[2]
    assert global_diff < 1.0, f'整帧差 {global_diff:.3f} 应低于阈值（复现前提）'
    assert band_diff > 1.0, f'带内差 {band_diff:.3f} 必须高于阈值（否则漏检）'
    # 无选框时退回整帧判据（不产生带内分数）
    det2 = SubtitleDetect(str(video), [])
    det2._update_frame_diff(1, f1)
    det2._update_frame_diff(2, f2)
    assert 2 not in det2.frame_band_diff_scores


def main():
    total = len(_passed) + len(_failed)
    print('\n' + '=' * 60)
    print(f'通过: {len(_passed)}  失败: {len(_failed)}  共 {total}')
    if _failed:
        for name, err in _failed:
            print(f'  FAIL {name}: {type(err).__name__}: {err}')
    print('=' * 60)
    return 0 if not _failed else 1


if __name__ == '__main__':
    sys.exit(main())
