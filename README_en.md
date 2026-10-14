[简体中文](README.md) | English

<div align="center">
  <img src="design/subzero.ico" alt="SubZero" width="128" height="128">

# SubZero (净影)

**AI-powered hard subtitle removal for videos**

![License](https://img.shields.io/badge/License-Apache%202-red.svg)
![python version](https://img.shields.io/badge/Python-3.13-blue.svg)
![support os](https://img.shields.io/badge/OS-Windows-green.svg)
![version](https://img.shields.io/badge/version-1.1.0-1D9E75.svg)
</div>

---

## Introduction

SubZero removes hard-burned subtitles from videos: OCR locates the subtitle region and an AI inpainting model fills it in, preserving the original resolution. Available as a GUI app and a CLI, with automatic segmentation for long videos, checkpoint resuming, and a locally persisted task queue.

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
