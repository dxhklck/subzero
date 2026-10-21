"""分段计划与断点检查点（checkpoint）管理。

移植自 seedvr2_gui/core/segmenter.py 的零侵入续跑方案：
外层执行器把长视频按帧数切成若干段，每段作为独立子进程运行
（SubtitleRemover + frame_start/frame_end），段输出文件本身即检查点产物，
checkpoint.json 记录每段状态与全局参数哈希——参数不一致的续跑会被拒绝，
防止新旧段混合产生错位结果。

差异（相对 seedvr2 原版）：
- 分段单位改为「帧数」而非「秒数」，由执行器按 fps × segment_seconds 换算；
- params_hash 的相关参数按 VSR 的配置键裁剪；
- 段间重叠（overlap）：除第一段外每段向前多取 N 帧作上下文（只送模型、不写入），
  段内区分「处理范围」[frame_start, frame_end) 与「写入范围」[write_start, frame_end)，
  拼接时无需裁剪、成片总帧数不变。overlap 由调用方作为内容参数放入 params
  （参与 params_hash：改重叠值=拒绝续跑/重建），段位置字段（skip/cap）不参与。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

#: checkpoint 结构版本（不兼容变更时递增）
CHECKPOINT_VERSION = 1

#: 段状态
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


@dataclass
class Segment:
    """一段的处理计划。

    skip/cap 描述「写入范围」[skip, skip+cap)（段首 0-based，含）；
    overlap 为段首向前重叠的上下文帧数（只送入模型、不写入段文件），
    由此派生「处理范围」[frame_start, frame_end)。
    第 0 段 overlap 恒为 0（无前序段可重叠）。
    """

    index: int
    skip: int   # 段写入起始帧（全局 0-based，含）
    cap: int    # 段写入帧数
    status: str = STATUS_PENDING
    overlap: int = 0  # 段首向前重叠的上下文帧数（不写入）

    @property
    def frame_end(self) -> int:
        """段结束帧（不含）。"""
        return self.skip + self.cap

    @property
    def frame_start(self) -> int:
        """处理起点（全局 0-based，含）：写入起点向前 overlap 帧（段0无重叠）。"""
        return max(0, self.skip - self.overlap)

    @property
    def write_start(self) -> int:
        """写入起点（全局 0-based，含）：段首，跳过前 overlap 帧上下文。"""
        return self.skip

    @property
    def process_frames(self) -> int:
        """本段实际读取/推理的帧数（含重叠上下文）。"""
        return self.frame_end - self.frame_start

    @property
    def output_name(self) -> str:
        """段输出文件名（相对任务目录）。"""
        return f"seg_{self.index:04d}.mp4"


def build_plan(total_frames: int, segment_frames: int,
               overlap: int = 0) -> List[Segment]:
    """按段帧数生成计划；total_frames <= segment_frames 时为单段（整片）。

    overlap>0 时，除第 0 段外每段处理范围向前扩展 overlap 帧作为上下文
    （写入范围不变，仍从段首开始，成片总帧数不变）。

    Example: 240 帧、段长 100、overlap=2 ->
    [(skip=0,cap=100,overlap=0), (skip=100,cap=100,overlap=2),
    (skip=200,cap=40,overlap=2)]——第 2 段处理 [98,200)、写入 [100,200)。
    overlap=0（默认）时与旧行为完全一致。
    """
    if total_frames <= 0:
        return []
    seg_frames = max(1, int(segment_frames))
    ov = max(0, int(overlap))
    plan: List[Segment] = []
    skip = 0
    index = 0
    while skip < total_frames:
        cap = min(seg_frames, total_frames - skip)
        plan.append(Segment(index=index, skip=skip, cap=cap,
                            overlap=0 if index == 0 else ov))
        index += 1
        skip += cap
    return plan


def _canonicalize(v: Any) -> Any:
    """哈希前规范化：整数值的 float 视为 int（如 81.0 与 81 等价），
    递归处理 list/tuple/dict。防止同一参数的 int/float 序列化差异
    （如选区坐标修复前后）造成假性"参数已变更"。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, (list, tuple)):
        return [_canonicalize(x) for x in v]
    if isinstance(v, dict):
        return {k: _canonicalize(x) for k, x in v.items()}
    return v


def _json_default(o: Any) -> Any:
    """json.dumps 的兜底序列化（checkpoint 落盘与参数哈希共用）。

    历史上 range 对象（GUI 的 ab_sections）流入 params，save() 直接抛
    ``TypeError: Object of type range is not JSON serializable`` 让整个分段任务
    在建 checkpoint 时崩掉。这里做最后一道保险：任何未知类型都转成可序列化
    形式，绝不让序列化问题中断任务（哈希退化为字符串也可接受）。
    """
    if isinstance(o, range):
        return [int(o.start), int(o.stop)]
    if isinstance(o, (set, frozenset)):
        try:
            return sorted(o)
        except TypeError:
            return list(o)
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, bytes):
        return o.decode("utf-8", "replace")
    item = getattr(o, "item", None)  # numpy 标量
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    return str(o)


def params_hash(params: Dict[str, Any]) -> str:
    """对所有影响输出像素的任务参数做稳定 SHA-256。

    段位置字段（skip/cap/输出路径）不得参与哈希；段间重叠帧数属于内容参数
    （改变邻帧模型的上下文即改变输出像素），由调用方放入 params 参与哈希。
    """
    relevant = {
        k: _canonicalize(params[k])
        for k in sorted(params)
        if k not in ("video_path", "output_path", "task_dir")
    }
    blob = json.dumps(relevant, ensure_ascii=False, sort_keys=True,
                      default=_json_default)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ResumeInfo:
    """由 checkpoint.json 派生的续跑信息（供 GUI 状态显示）。"""

    exists: bool = False
    corrupt: bool = False
    total_segments: int = 0
    done_count: int = 0
    first_pending_index: int = -1     # 第一个未完成段的 0-based 序号
    hash_match: bool = True
    done_frames: int = 0              # 已完成段覆盖的帧数（进度还原用）
    total_frames: int = 0             # 计划总帧数

    @property
    def progress_pct(self) -> float:
        """按帧计算的完成度（0~100）；无计划帧数时返回 0。

        段数进度会骗人：末段通常短于整段，按段数算 21/22 段显示 95%，
        实际帧进度可能已 99%。快照恢复的进度以帧为准。"""
        if self.total_frames <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * self.done_frames / self.total_frames))

    @property
    def can_resume(self) -> bool:
        return (self.exists and not self.corrupt and self.hash_match
                and 0 <= self.first_pending_index < self.total_segments)

    @property
    def chip_text(self) -> str:
        """人类可读的续跑状态（段号按 1-based 显示）。"""
        if not self.exists:
            return ""
        if self.corrupt:
            return "⚠ checkpoint 损坏，将作为全新任务重跑"
        if not self.hash_match:
            if self.total_segments > 0 and self.done_count >= self.total_segments:
                return "✓ 全部段已完成"
            return "✗ 哈希不一致，拒绝续跑"
        if self.can_resume and self.done_count > 0:
            x = self.first_pending_index + 1
            return (f"⏸ 上次中断于 段 {x}/{self.total_segments} · 可续跑"
                    f"（段 1..{self.first_pending_index} 已完成，只补 "
                    f"{x}..{self.total_segments}）")
        if self.done_count >= self.total_segments > 0:
            return "✓ 全部段已完成"
        return ""


def load_or_rebuild_checkpoint(path: Union[str, Path], params: Dict[str, Any],
                               total_frames: int, segment_frames: int,
                               log: Optional[Any] = None,
                               overlap: int = 0) -> tuple:
    """为任务解析 checkpoint。

    返回 ``(checkpoint, mode)``，mode 为：

    ``"created"``
        无 checkpoint —— 用 params 新建全 pending 计划。
    ``"loaded"``
        参数哈希一致 —— 安全续跑。
    ``"rebuilt"``
        哈希不同但尚无完成段 —— 不会丢任何进度，按新参数重建计划。
    ``"refused"``
        哈希不同且存在完成段 —— 拒绝续跑（新旧段混合会错位），
        调用方需提示用户处理。

    overlap 为段间重叠帧数（随 params 参与哈希，改值即触发上述分流）。
    """
    p = Path(path)
    if not p.exists():
        return Checkpoint.create(p, params, total_frames, segment_frames,
                                 overlap), "created"
    try:
        return Checkpoint.load(p, expected_hash=params_hash(params)), "loaded"
    except CheckpointMismatch:
        prior = Checkpoint.load(p)  # 未校验读取（绕过哈希检查）
        done = prior.done_count() if prior is not None else 0
        if done == 0:
            if log is not None:
                log("[续跑] 参数已变更但尚无已完成段，按新参数重建任务计划")
            return Checkpoint.create(p, params, total_frames,
                                     segment_frames, overlap), "rebuilt"
        if log is not None:
            log(f"[续跑] 参数已变更且已存在 {done} 个按旧参数完成的段，拒绝续跑")
        return prior, "refused"


def derive_resume_info(ckpt_path: Union[str, Path],
                       expected_hash: Optional[str] = None) -> ResumeInfo:
    """从 checkpoint.json 派生 ResumeInfo（哈希不匹配不抛异常，报告 hash_match=False）。"""
    p = Path(ckpt_path)
    if not p.exists():
        return ResumeInfo(exists=False)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ResumeInfo(exists=True, corrupt=True)
    if not isinstance(data, dict) or "param_hash" not in data or "segments" not in data:
        return ResumeInfo(exists=True, corrupt=True)
    segments = data.get("segments") or []
    info = ResumeInfo(exists=True, total_segments=len(segments))
    first_pending = -1
    for i, it in enumerate(segments):
        if str(it.get("status", STATUS_PENDING)) != STATUS_DONE:
            first_pending = i
            break
    info.done_count = sum(1 for it in segments
                          if str(it.get("status", "")) == STATUS_DONE)
    # 帧级进度（快照/断点恢复用）：已完成段的 cap 之和 ÷ 计划总帧数
    try:
        info.done_frames = sum(int(it.get("cap", 0) or 0) for it in segments
                               if str(it.get("status", "")) == STATUS_DONE)
        info.total_frames = int(data.get("total_frames")
                                or sum(int(it.get("cap", 0) or 0) for it in segments))
    except (TypeError, ValueError):
        info.done_frames = info.total_frames = 0
    info.first_pending_index = first_pending
    info.hash_match = (expected_hash is None
                       or str(data.get("param_hash")) == str(expected_hash))
    return info


class CheckpointMismatch(Exception):
    """续跑时参数哈希不一致。"""


class Checkpoint:
    """任务目录内 checkpoint.json 的读写句柄（原子写入）。"""

    def __init__(self, path: Path, data: Dict[str, Any]):
        self.path = Path(path)
        self.data = data

    # -- 创建 / 加载 ---------------------------------------------------------

    @classmethod
    def create(cls, path: Path, params: Dict[str, Any], total_frames: int,
               segment_frames: int, overlap: int = 0) -> "Checkpoint":
        """新建全 pending 计划并写盘。"""
        plan = build_plan(total_frames, segment_frames, overlap)
        data = {
            "version": CHECKPOINT_VERSION,
            "param_hash": params_hash(params),
            "params": params,
            "total_frames": total_frames,
            "segment_frames": segment_frames,
            "overlap": max(0, int(overlap)),
            "segments": [
                {"index": s.index, "skip": s.skip, "cap": s.cap,
                 "overlap": s.overlap, "status": s.status}
                for s in plan
            ],
        }
        ckpt = cls(path, data)
        ckpt.save()
        return ckpt

    @classmethod
    def load(cls, path: Path, expected_hash: Optional[str] = None) -> Optional["Checkpoint"]:
        """加载 checkpoint；不存在返回 None；哈希不一致抛 CheckpointMismatch。"""
        p = Path(path)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        # 结构校验：非 dict / 缺 param_hash / segments 缺失或元素缺关键字段时按损坏处理
        # （返回 None → 上层 load_or_rebuild_checkpoint 走重建/降级，而不是直接抛异常炸任务）
        if not isinstance(data, dict) or "param_hash" not in data:
            return None
        segments = data.get("segments")
        if not isinstance(segments, list) or not segments:
            return None
        if not all(isinstance(it, dict) and {"index", "skip", "cap"} <= set(it) for it in segments):
            return None
        if expected_hash is not None and data.get("param_hash") != expected_hash:
            raise CheckpointMismatch(
                f"checkpoint param_hash mismatch: {data.get('param_hash')} != {expected_hash}")
        return cls(p, data)

    # -- 持久化 --------------------------------------------------------------

    def save(self) -> None:
        """原子写盘（tmp + replace）。

        default=_json_default 兜底：params 里若混入 range/Path/numpy 标量等
        非 JSON 类型，旧实现直接抛 TypeError 把整个任务打断（分段任务在建
        checkpoint 时必崩），这里改为尽力序列化。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8")
        tmp.replace(self.path)

    # -- 状态变更 ------------------------------------------------------------

    def segments(self) -> List[Segment]:
        return [
            Segment(index=int(it["index"]), skip=int(it["skip"]), cap=int(it["cap"]),
                    status=str(it.get("status", STATUS_PENDING)),
                    overlap=int(it.get("overlap", 0) or 0))
            for it in self.data.get("segments", [])
        ]

    def _set_status(self, index: int, status: str) -> None:
        for it in self.data["segments"]:
            if int(it["index"]) == index:
                it["status"] = status
                self.save()
                return
        raise KeyError(f"segment {index} not in checkpoint plan")

    def mark_running(self, index: int) -> None:
        self._set_status(index, STATUS_RUNNING)

    def mark_done(self, index: int) -> None:
        self._set_status(index, STATUS_DONE)

    def mark_failed(self, index: int) -> None:
        self._set_status(index, STATUS_FAILED)

    def reset_running(self) -> None:
        """启动时把遗留的 running 段复位为 pending（上次进程被杀的残留状态）。"""
        for it in self.data["segments"]:
            if it.get("status") == STATUS_RUNNING:
                it["status"] = STATUS_PENDING
        self.save()

    # -- 查询 ----------------------------------------------------------------

    def done_count(self) -> int:
        return sum(1 for s in self.segments() if s.status == STATUS_DONE)

    def total_count(self) -> int:
        return len(self.data.get("segments", []))

    def pending(self) -> List[Segment]:
        """所有未完成段。"""
        return [s for s in self.segments() if s.status != STATUS_DONE]

    def done_frames(self) -> int:
        """已完成段的总帧数（全局进度合成用）。"""
        return sum(s.cap for s in self.segments() if s.status == STATUS_DONE)

    def segment_file(self, task_dir: Union[str, Path], index: int) -> Path:
        """段输出文件的绝对路径。"""
        return Path(task_dir) / f"seg_{index:04d}.mp4"
