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

SubZero is a desktop application that removes hard-burned subtitles from videos using AI inpainting models, with lossless output resolution.

Highlights:

- **Strip-crop inpainting** (core optimization): crops the subtitle strip (with configurable padding), inpaints it, and pastes it back — pixels outside the window stay untouched. Measured **6.9x faster** than full-frame inference at 720p, with proportional VRAM savings
- **4 inpainting algorithms**: MiniMax-Remover (recommended) / STTN / LAMA / OpenCV
- **Long-video segmented processing with resume**: videos longer than a configurable length (default 600 s) are processed in segments; an interrupted or closed task automatically resumes from the last finished segment when re-run (per-segment checkpoints, parameter-hash guarded); optional segment overlap frames (default 2) remove boundary seams
- **Task list persistence**: the queue is saved locally and restored on relaunch; unfinished tasks are marked "resumable" and never auto-start
- **CLI mode**: subtitle areas, time ranges, frame ranges (segments/overlap) and residue-only second pass for scripted batch processing (see below)
- **GPU monitor**: live VRAM and RAM usage above the processing card (thin progress bars + dynamic values)
- **Letterbox preview**: any aspect ratio is shown at its native ratio with black bars; selection coordinates map precisely back to original pixels
- **Light/Dark theme** (or follow system), teal accent
- **Drag & drop task queue**: drop one or many video files, track name / progress / status
- **Weight integrity scan**: model weights are verified (existence + size) at startup; if antivirus silently quarantined `.pt` files you get an immediate warning with one-click repair
- **Zero console windows**: double-click launch shows no console window; the processing subprocess's raw console output (including tqdm) streams live into the in-app **Monitor** tab, with key events mirrored in the formatted **Log** tab

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
4. To see full startup-stage logs (environment check / version check), launch `启动视频字幕擦除.bat` (console mode) instead

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

Weights are large and not distributed with the source repository. Get them from Release attachments, then extract into `backend/models/`. Weight integrity (existence + size) is verified automatically at startup; the **Environment Check** card in Settings can also verify and one-click-repair missing files.

## Command Line (CLI)

Besides the GUI, batch/scripted processing is available (CLI never writes to GUI settings):

```bash
# Basic usage (-o omitted: writes <name>_no_sub.mp4 next to the input)
python backend/main.py -i input.mp4 -o output.mp4

# Choose the algorithm
python backend/main.py -i input.mp4 -o output.mp4 --inpaint-mode minimax

# Limit subtitle area and time range (original pixel coords; repeatable areas)
python backend/main.py -i input.mp4 -o output.mp4 --sub-area "560,720,0,1280" --time-range "10.5-30"

# Frame range (0-based, end-exclusive)
python backend/main.py -i input.mp4 -o output.mp4 --frame-start 100 --frame-end 500

# Residue-only second pass: input is already a first-pass result; only residue
# detection + second repair runs (with audio mux)
python backend/main.py -i first_pass.mp4 -o final.mp4 --inpaint-mode lama --residue-only
```

Notes:

- `--inpaint-mode`: `sttn-auto` / `sttn-det` / `lama` / `opencv` / `minimax`; omitted = use the mode selected in the GUI settings
- When any `--sub-area` coordinate is negative, use the equals form (e.g. `--sub-area=-10,720,0,1280`)
- `--time-range` conflicts with `--frame-start/--frame-end`; `--residue-only` cannot be combined with frame-range options
- The GUI's "video segment length (s)" and "segment overlap frames" settings apply to CLI segmented runs too; low-level options are `--frame-start` / `--frame-end` / `--frame-write-start` (overlap context frames are inferred but not written)

## License

- Code is released under the [Apache License 2.0](LICENSE), adapted from [video-subtitle-remover (VSR)](https://github.com/YaoFANGUK/video-subtitle-remover) — thanks to the original author YaoFANGUK (attribution preserved in the LICENSE file)
- **MiniMax-Remover model weights are licensed CC-BY-NC-4.0 (non-commercial only)**; evaluate alternative algorithms for commercial use
