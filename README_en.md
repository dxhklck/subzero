[简体中文](README.md) | English

<div align="center">
  <img src="design/subzero.ico" alt="SubZero" width="128" height="128">

# SubZero (净影)

**AI-powered hard subtitle removal for videos**

![License](https://img.shields.io/badge/License-Apache%202-red.svg)
![python version](https://img.shields.io/badge/Python-3.13-blue.svg)
![support os](https://img.shields.io/badge/OS-Windows-green.svg)
![version](https://img.shields.io/badge/version-1.3.1-1D9E75.svg)
</div>

---

## Introduction

SubZero removes hard-burned subtitles from videos: OCR locates the subtitle region and an AI inpainting model fills it in, preserving the original resolution. Available as a GUI app and a CLI, with automatic segmentation for long videos, checkpoint resuming, and a locally persisted task queue.

**Fixes and improvements in 1.3.1**:

- **Missed subtitles on static footage**: duplicate-frame skipping now checks two scales — the whole frame *and* the subtitle band. On screen recordings, slide-based lessons and fixed-camera footage a single subtitle line covers only 0.4% of the frame, so the old whole-frame mean-difference test marked it as "identical to the previous frame" and reused a subtitle-free detection, leaving the entire line untouched in the output (31% of subtitle frames missed in testing, up to 201 consecutive frames). Misses are now down to 2.7%, and what remains are 1–3 frame fade in/out boundaries covered by the timeline expansion
- **Complete snapshot restore**: running tasks now persist live elapsed time and start timestamp, accumulated across resumes; on restore the completion percentage comes from the checkpoint's **frame** progress (not the segment count), and pending rows show the last duration and start time
- **Resuming no longer restarts at 1%**: the run starts from the completed-frame ratio, and progress never moves backwards while processing; tasks without a checkpoint are explicitly reported as "starting from scratch"
- **Residue second pass is on by default**: the fallback pass now ships enabled (quality first); when nothing is left it only costs one extra detection, with no re-encode

**New in 1.3.0**:

- **Live A/B comparison preview**: while a task is running the preview switches to a two-pane A/B view — A is the source frame (with the subtitle selection overlaid), B is the live inpainted result, so detection and erasing are visible frame by frame; the play button changes meaning with state (running = pause/resume the live preview, finished = synchronised A/B playback), fullscreen comparison picks side-by-side (portrait) or stacked (landscape) layout automatically, and closing fullscreen keeps playback going with the main preview back in the same A/B view
- **Strict residue second pass**: the fallback pass now samples every frame and disables duplicate-frame skipping, so 1–2 frame ghosting no longer slips between sampling steps (slower but nothing missed); it also reads the result with a frame-accurate reader instead of keyframe-snapping seeks
- **Optional SageAttention acceleration**: MiniMax self-attention can be routed through SageAttention (INT8-quantized K) — roughly 40% faster end-to-end in benchmarks with no change in peak VRAM; falls back automatically when SageAttention is missing or fails, off by default
- **Attention backend tuning**: the fallback path now pins the cuDNN SDPA backend (~14% faster, mathematically exact), and inference uses `inference_mode` + cuDNN benchmark
- **Feathered strip compositing**: inpainted pixels are blended back with a feathered mask alpha, eliminating faint rectangular edges around restored regions
- **Preview video info overlay**: resolution / frame rate / duration / average bitrate / file size shown on the preview pane
- **Per-task & queue total time**: each task row shows its total elapsed time, and a floating capsule at the bottom of the task list shows the queue total (persisted with snapshots)
- **WYSIWYS snapshot boxes**: the current on-screen selection is written into the task right before saving a snapshot, fixing "box visible on screen but empty in snapshot"

> This repository contains source only — **no automatic environment setup or weight downloading is built in**. For installation, weight download links, parameter reference and configuration examples, see the detailed tutorial: **[Tutorial (English)](使用教程_en.md)** · [使用教程 (简体中文)](使用教程.md)

## Quick Start

```bash
# 1. Install Python 3.10+ and dependencies (torch cu128 + paddlepaddle-gpu cu129, see tutorial)
pip install -r requirements.txt

# 2. Download model weights manually into backend/models/ (checklist and links in the tutorial)

# 3. Run
python gui.py                                        # GUI
python backend/main.py -i input.mp4 -o output.mp4    # CLI
```

## Directory Layout

```
subzero/
├── gui.py                          # GUI entry point
├── 使用教程.md / 使用教程_en.md      # Setup / parameter reference / examples / FAQ
├── backend/
│   ├── main.py                     # CLI entry / processing pipeline
│   ├── config.py                   # All config items (single source of version)
│   ├── subtitle_detect.py          # Subtitle detection (PaddleOCR PP-OCRv5)
│   ├── hardware_accelerator.py     # Device selection (CUDA / DirectML / MPS / CPU)
│   ├── tools/
│   │   ├── strip_inpaint.py        # Strip-crop inpainting wrapper (core optimization)
│   │   ├── segmenter.py            # Long-video segmentation plan & checkpoints (resume)
│   │   ├── segment_executor.py     # Segment execution / concat / audio mux
│   │   ├── args_handler.py         # CLI argument parsing & mutual-exclusion checks
│   │   ├── inpaint_tools.py        # Mask creation / batching / interval utils
│   │   ├── video_io.py             # Frame prefetching / ffmpeg pipe writer
│   │   ├── model_scanner.py        # Startup weight integrity scan
│   │   ├── bootstrap.py            # Environment self-check (check only, no install/download)
│   │   ├── version_service.py      # Update check
│   │   └── process_manager.py      # Subprocess lifecycle management
│   ├── inpaint/                    # Inpainting algorithms: STTN / LAMA / OpenCV / MiniMax-Remover
│   ├── models/                     # Model weights (download manually, see tutorial)
│   ├── ffmpeg/                     # ffmpeg binaries (per platform)
│   └── interface/                  # UI strings in 7 languages (ini)
├── ui/                             # PySide6 + qfluentwidgets UI (home / settings / about)
└── design/                         # Icons and design assets
```

## License

- Code is released under the [Apache License 2.0](LICENSE), adapted from [video-subtitle-remover (VSR)](https://github.com/YaoFANGUK/video-subtitle-remover). Thanks to the original author YaoFANGUK (Apache-2.0 attribution preserved in the LICENSE file).
- **MiniMax-Remover weights are licensed CC-BY-NC-4.0, non-commercial use only**; evaluate alternative algorithms for commercial use.
