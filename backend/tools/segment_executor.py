"""分段执行辅助：段计划参数、ffmpeg 拼接与音频封装。

纯函数模块（不依赖 GUI），供 HomeInterface 的分段任务编排调用：
- checkpoint 状态机见 backend/tools/segmenter.py；
- 每段子进程由 HomeInterface.run_subtitle_remover_process 以
  frame_start/frame_end/is_segment 注入 options 执行；
- 段间重叠（segmentOverlapFrames）：后续段 frame_start 向前多取 overlap 帧
  作上下文，frame_write_start 仍指段首——重叠帧只送模型、不写入。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.tools.common_tools import SUBPROCESS_CREATION_FLAGS
from backend.tools.ffmpeg_cli import FFmpegCLI
from backend.tools.segmenter import Segment


def probe_video_frames(video_path: str) -> tuple:
    """返回 (frame_count, fps)；探测失败返回 (0, 0.0)。"""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            return 0, 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) + 0.5)
        fps = cap.get(cv2.CAP_PROP_FPS)
        return frame_count, fps
    finally:
        cap.release()


def compute_segment_frames(frame_count: int, fps: float,
                           segment_seconds: Optional[float]) -> Optional[int]:
    """由分段秒数换算段帧数；不分段（禁用/短片/非法值）返回 None。"""
    if not segment_seconds or segment_seconds <= 0 or frame_count <= 0 or fps <= 0:
        return None
    seg_frames = int(round(fps * segment_seconds))
    if seg_frames <= 0 or frame_count <= seg_frames:
        return None  # 短片：整片处理
    return seg_frames


def build_task_params(options: Dict[str, Any], inpaint_mode: Any) -> Dict[str, Any]:
    """组装参与参数哈希的任务参数（一切影响输出像素的字段；路径除外）。

    段位置字段（frame_start/frame_end/frame_write_start/is_segment）不参与哈希；
    段间重叠帧数由调用方作为 segmentOverlapFrames 放入（改重叠值=拒绝续跑/重建）。
    """
    params: Dict[str, Any] = {"inpaint_mode": str(inpaint_mode)}
    for key, value in options.items():
        if key in ("frame_start", "frame_end", "frame_write_start", "is_segment"):
            continue  # 段位置字段，不属于内容参数
        params[key] = value
    return params


def build_segment_options(seg: Segment) -> Dict[str, Any]:
    """单段子进程注入选项：处理范围 [frame_start, frame_end) 含重叠上下文，
    写入起点 frame_write_start=段首（跳过前 overlap 帧，只送模型不写入）。"""
    return {
        "frame_start": seg.frame_start,        # 处理起点（段首向前 overlap 帧）
        "frame_end": seg.frame_end,
        "frame_write_start": seg.write_start,  # 写入起点（段首，跳过重叠帧）
        "is_segment": True,
    }


def build_concat_list(task_dir: Path, segments: List[Segment]) -> Path:
    """写 concat demuxer 清单（相对文件名 + 单引号转义），返回清单文件路径。

    用相对文件名避免 Windows 反斜杠在 ffmpeg concat demuxer 引号串内
    被当作转义字符的问题；执行时配合 cwd=task_dir。
    """
    task_dir = Path(task_dir)
    list_path = task_dir / "concat_list.txt"
    lines = []
    for s in segments:
        escaped = s.output_name.replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def concat_segments(task_dir: Path, segments: List[Segment],
                    concat_out: Path, log: Optional[Callable[[str], None]] = None) -> None:
    """无损拼接全部段文件（各段同参数 libx264，-c copy 安全）。"""
    ffmpeg = FFmpegCLI.instance().ffmpeg_path
    list_path = build_concat_list(task_dir, segments)
    concat_out = Path(concat_out)
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
           "-c", "copy", "-loglevel", "error", str(concat_out)]
    if log is not None:
        log(f"[分段] 拼接 {len(segments)} 段 -> {concat_out.name}")
    with open(os.devnull) as devnull:
        r = subprocess.run(cmd, stdin=devnull, shell=(os.name == "nt"),
                           cwd=str(task_dir), capture_output=True, text=True,
                           timeout=3600, creationflags=SUBPROCESS_CREATION_FLAGS)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg concat 失败 rc={r.returncode}: {r.stderr[-500:]}")


def mux_audio(source_video: str, video_in: Path, video_out: Path,
              log: Optional[Callable[[str], None]] = None) -> bool:
    """把源视频音频封装进成片；无音频/失败时直接把视频流复制为成片。"""
    ffmpeg = FFmpegCLI.instance().ffmpeg_path
    temp = tempfile.NamedTemporaryFile(suffix=".mka", delete=False)
    temp.close()
    use_shell = True if os.name == "nt" else False
    try:
        # .mka 中转：-acodec copy 到 .aac（ADTS）容器对 AC-3/DTS 等编码必然失败
        extract_cmd = [ffmpeg, "-y", "-i", str(source_video), "-acodec", "copy",
                       "-vn", "-loglevel", "error", temp.name]
        with open(os.devnull) as devnull:
            subprocess.check_output(extract_cmd, stdin=devnull, shell=use_shell,
                                    timeout=600, creationflags=SUBPROCESS_CREATION_FLAGS)
        merge_cmd = [ffmpeg, "-y", "-i", str(video_in), "-i", temp.name,
                     "-vcodec", "copy", "-acodec", "copy", "-loglevel", "error",
                     str(video_out)]
        with open(os.devnull) as devnull:
            subprocess.check_output(merge_cmd, stdin=devnull, shell=use_shell,
                                    timeout=3600, creationflags=SUBPROCESS_CREATION_FLAGS)
        return True
    except Exception as e:
        # 无音轨（extract 失败）或封装失败：退化为纯视频拷贝，保证有产物
        if log is not None:
            log(f"[分段] 音频封装跳过/失败（{e}），输出不含音频")
        try:
            shutil.copy2(str(video_in), str(video_out))
            return True
        except IOError as e2:
            if log is not None:
                log(f"[分段] 成片复制失败：{e2}")
            return False
    finally:
        if os.path.exists(temp.name):
            try:
                os.remove(temp.name)
            except OSError:
                pass
