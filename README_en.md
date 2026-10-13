[简体中文](README.md) | English

<div align="center">
  <img src="design/subzero.ico" alt="SubZero" width="128" height="128">

# SubZero (净影)

**AI-powered hard subtitle removal for videos**

![License](https://img.shields.io/badge/License-Apache%202-red.svg)
![python version](https://img.shields.io/badge/Python-3.13-blue.svg)
![support os](https://img.shields.io/badge/OS-Windows-green.svg)
</div>

---

## Introduction

SubZero is a desktop application that removes hard-burned subtitles from videos using AI inpainting models, with lossless output resolution.

Highlights:

- **Strip-crop inpainting** (core optimization): crops the subtitle strip (with configurable padding), inpaints it, and pastes it back — pixels outside the window stay untouched. Measured **6.9x faster** than full-frame inference at 720p, with proportional VRAM savings
- **4 inpainting algorithms**: MiniMax-Remover (recommended) / STTN / LAMA / OpenCV
- **GPU monitor**: live VRAM (with per-task peak), GPU utilization, RAM and processing speed in the status bar
- **A/B wipe slider**: drag the split line to compare original vs. inpainted frames during processing
- **Performance presets**: one-click 480p / 720p / 1080p / 4K parameter sets
- **Light/Dark theme** (or follow system), teal accent
- **Drag & drop task queue**: drop one or many video files, track name / progress / status
- **Zero console windows**: everything runs in the GUI, logs included

## Hardware Requirements

| Item | Requirement |
|---|---|
| GPU | NVIDIA GTX 10 series or newer (Turing/Ampere/Ada/Blackwell all supported; RTX 50 series requires the CUDA 12.9 Paddle build, bundled here) |
| VRAM | ≥ 8GB recommended (MiniMax 480p strip mode peaks around 7.4GB) |
| Driver | Recent driver recommended (r575+); the app falls back to CPU detection and logs a warning if the driver is too old |
| OS | Windows 10/11 x64 |

## Quick Start

### Portable build (recommended)

1. Extract to a path containing **only ASCII characters** (non-ASCII paths break PaddleOCR model loading)
2. If your antivirus quarantined `.pt/.pth` files under `backend/models/`, add an exclusion for the folder and re-extract
3. Run `启动视频字幕擦除-GUI无黑窗.exe`

### From source

```bash
# 1. Install Python 3.13 and NVIDIA driver
# 2. Install dependencies (torch cu128 + paddlepaddle-gpu cu129)
pip install -r requirements.txt
# 3. Download model weights (see below) into backend/models/
# 4. Launch the GUI
python gui.py
```

## Model Weights

| Model | Size | Purpose |
|---|---|---|
| PP-OCRv5 (server) | ≈ 89MB | Subtitle detection |
| big-lama | 196MB | LAMA inpainting |
| sttn | 63MB | STTN inpainting |
| MiniMax-Remover | ≈ 2.6GB | MiniMax inpainting (recommended) |

Weights are large and not distributed with the source repository. Get them from Release attachments, then extract into `backend/models/`. Use the **Environment Check** card in Settings to verify file integrity.

## License

- Code is released under the [Apache License 2.0](LICENSE), adapted from [video-subtitle-remover (VSR)](https://github.com/YaoFANGUK/video-subtitle-remover) — thanks to the original author YaoFANGUK (attribution preserved in the LICENSE file)
- **MiniMax-Remover model weights are licensed CC-BY-NC-4.0 (non-commercial only)**; evaluate alternative algorithms for commercial use
