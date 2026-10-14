# SubZero User Tutorial

AI-powered hard-subtitle removal: OCR locates subtitles, an AI inpainting model fills the region back in. Available as a GUI app and a CLI.

> This repository contains source only. The runtime environment and model weights must be installed/downloaded by yourself following this tutorial — the program contains no built-in auto-install or auto-download logic.

> 简体中文版：[使用教程.md](使用教程.md)

---

## 1. Environment Setup (do it yourself)

### 1.1 Requirements

- Windows 10/11 x64 (on Linux/macOS you must supply a matching ffmpeg and torch build yourself)
- NVIDIA GPU, ≥ 6GB VRAM (8GB+ recommended), driver supporting CUDA 12.8
- Python 3.10 – 3.13

### 1.2 Python dependencies

```bash
# 1) Base dependencies
pip install -r requirements.txt
pip install diffusers==0.33.1

# 2) PyTorch (CUDA 12.8)
pip install torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cu128

# 3) PaddlePaddle-GPU (CUDA 12.9)
pip install paddlepaddle-gpu==3.4.0 --index-url https://www.paddlepaddle.org.cn/packages/stable/cu129/
```

Notes:

- **Install torch before paddle** (the code also imports torch first internally).
- Do NOT install the `nvidia-*-cu12` pip packages bundled by torch/paddle (two cuDNN 9.x copies conflict with WinError 127); use the CUDA libraries shipped with torch.
- CPU-only machines can run CPU builds of torch/paddle, but it is extremely slow — not recommended.

### 1.3 FFmpeg

- Windows: `backend/ffmpeg/win_x64/ffmpeg.exe` (item 11 of the weight checklist below).
- Linux/macOS: install system ffmpeg and place it in `backend/ffmpeg/<platform>/`, or point `backend/tools/ffmpeg_cli.py` to the system path.

### 1.4 Download model weights (manual)

Place the files below under `backend/models/` (item 11 goes to `backend/ffmpeg/win_x64/`).
Two download sources, identical content:

- ModelScope (fast in China): `https://modelscope.cn/models/dxhklck/subzero-models/resolve/master/models/<repo-relative-path>`
- GitHub Releases: `https://github.com/dxhklck/subzero/releases` (weights attached to a Release)

| # | Destination path (under backend/models/) | Size | GitHub Release asset |
|---|---|---|---|
| 1 | `V5/ch_det/inference.pdiparams` | 84 MB | `V5_ch_det_inference.pdiparams` |
| 2 | `V5/ch_det_fast/inference.pdiparams` | 4.5 MB | `V5_ch_det_fast_inference.pdiparams` |
| 3 | `big-lama/big-lama.pt` | 196 MB | `big-lama.pt` |
| 4 | `sttn-auto/infer_model.pth` | 63 MB | `sttn-auto_infer_model.pth` |
| 5 | `sttn-det/sttn.pth` | 63 MB | `sttn-det_sttn.pth` |
| 6 | `minimax-remover/transformer/diffusion_pytorch_model.safetensors` | 2.1 GB | `minimax_transformer.safetensors.part1` + `.part2` (concatenate into one file) |
| 7 | `minimax-remover/transformer/config.json` | <1 KB | `transformer_config.json` inside `minimax_configs.zip` |
| 8 | `minimax-remover/vae/diffusion_pytorch_model.safetensors` | 484 MB | `minimax_vae_model.safetensors` |
| 9 | `minimax-remover/vae/config.json` | <1 KB | `vae_config.json` inside `minimax_configs.zip` |
| 10 | `minimax-remover/scheduler/scheduler_config.json` | <1 KB | `scheduler_config.json` inside `minimax_configs.zip` |
| 11 | `backend/ffmpeg/win_x64/ffmpeg.exe` (project's backend dir) | ~100 MB | `ffmpeg.exe` inside `ffmpeg-win_x64.zip` |

> MiniMax only needs items 6–10; for LAMA/STTN only items 1–5 + 11 are required. Shard files such as `big-lama_1.pt` are legacy distribution leftovers — **not needed**.

Verify SHA256 (matches `WEIGHT_MANIFEST` in `backend/tools/bootstrap.py`):

```bash
python backend/tools/bootstrap.py --check
```

All ✅ means the environment is ready.

### 1.5 Launch

```bash
python gui.py        # GUI
python backend/main.py -i video.mp4 -o output.mp4   # CLI
```

---

## 2. GUI

Main window: task queue, video preview (letterbox, native aspect ratio with black bars), and Monitor/Log tabs.

### 2.1 Task queue

- **Add**: pick files via the button, or drag one/multiple videos into the queue.
- **Persistence**: the queue is saved locally and restored on relaunch; the status line shows "Queue auto-saved · N tasks · M resumable · saved at time" — click it to open the queue file's folder.
- **Resume badge**: unfinished segmented tasks show "⏸ resumable k/n" (if parameters changed, it says so instead). Restored tasks never auto-start — press Run manually.
- **Subtitle area**: select a task and draw rectangles on the preview (multiple allowed); with no selection the app auto-detects full screen. Areas are stored in original-pixel coordinates and follow task selection.
- **Time range (AB sections)**: mark ranges on the slider to process only those parts.

### 2.2 Inpainting algorithms (Settings page)

| Algorithm | Traits | Use case |
|---|---|---|
| STTN-auto (default) | Fully automatic, fastest | Batch processing |
| MiniMax-Remover | Strip-crop inpainting, best quality/speed balance | **Recommended** |
| LAMA | High quality on static backgrounds | Static scenes / images |
| OpenCV | Classical, no large model | Quick checks |

AUTO mode routes per interval by frame difference: LAMA (static) vs MiniMax (motion).

### 2.3 Settings reference

**General**

| Setting | Default | Description |
|---|---|---|
| Interface language | Chinese | Restart to apply |
| Hardware acceleration | On | GPU inference toggle |
| Segment duration (s) | 600 | Videos longer than this are processed in segments; 0 = off |
| Segment overlap frames | 2 (0–10) | Adjacent segments exchange overlap frames as model context (not written to output), removing seam artifacts |
| Check updates on startup | On | Online version check |
| Save directory | empty | Empty = save next to the input as `<name>_no_sub.mp4` |
| Environment self-check | — | Checks dependencies and weights; missing items must be fetched manually per this tutorial |
| App theme | Follow system | Light / Dark / Follow system |

**Detection (advanced)**

| Setting | Default | Description |
|---|---|---|
| Timeline backward/forward frames | 5 / 5 | Expand subtitle intervals along the timeline to avoid boundary leftovers |
| Area deviation pixels | 20 | Expand detected boxes outward to fully cover subtitles |
| Detection sample step | 0 = smart | OCR every N frames; 0 adapts by frame difference |
| Detection process size | 720 | Resolution cap for the detection stage (0–960); larger = more accurate, slower |
| AUTO static threshold | 1 | Frame difference below this routes to LAMA |
| Residue second pass | On | After the first pass, re-detect leftovers and repair them automatically |

**MiniMax-Remover**

| Setting | Default | Description |
|---|---|---|
| MaxLoadNum | 121 (5–121) | Frames fed to the GPU per pass (4k+1); mainly a speed parameter — larger blocks amortize fixed overhead. Little effect on peak VRAM (set by strip resolution); the app auto-checks free VRAM and shrinks if needed |
| NumInferenceSteps | 8 (1–12) | Diffusion denoise steps. 8 is the sweet spot: 6 is ~2% faster but degrades quality, 12 is ~7.5% slower |
| Iterations | 6 (0–12) | Refinement iterations per interval |
| ProcessWidth | 0 = native | Strip processing resolution cap (multiples of 16). 0 = no downsampling; for 4K, 1280–1920 trades quality for speed |
| StripPadding | 128 (0–256) | Padding above/below the subtitle strip for model context |
| BlockOverlap | 8 (0–20) | Overlap frames between blocks to hide seams within long intervals (no cost when a single batch) |
| VAE fp32 decode | On | Faster when off, but banding may appear in dark areas |

**STTN**

| Setting | Default | Description |
|---|---|---|
| NeighborStride | 5 | Neighbor-frame sampling stride |
| ReferenceLength | 10 | Reference-frame window length |
| MaxLoadNum | 50 | Per-batch frame cap (50 for 1080p, ~35 for 4K) |

### 2.4 Expected performance (RTX 4070 Ti SUPER)

- 720p MiniMax: ~0.5 s/frame (strip cropping, ~7× faster than full-frame)
- 480p 522 frames: STTN 25s / OpenCV 34s / LAMA 48s / MiniMax ~1 s/frame
- Peak VRAM is dominated by strip resolution; for 4K lower ProcessWidth to 1280–1920

---

## 3. CLI

```bash
python backend/main.py -i <input> [-o <output>] [options...]
```

### Parameters

| Parameter | Description |
|---|---|
| `-i, --input` | Input video/image path (required) |
| `-o, --output` | Output path; omitted = next to input as `<name>_no_sub.mp4` (images: `no_sub/` subfolder) |
| `-c, --subtitle-area-coords` | Subtitle area `ymin,ymax,xmin,xmax` (original pixels; repeatable for multiple areas). **Without it the full frame is scanned** — slower and more error-prone |
| `--sub-area` | Same semantics (stackable) |
| `-m, --inpaint-mode` | `sttn-auto` / `minimax` / `lama` / `opencv` / `auto`; omitted = the mode selected in GUI settings (config.json) |
| `--time-range` | Range to process in seconds (decimals allowed), e.g. `'10.5-30'` |
| `--frame-start` / `--frame-end` | 0-based frame range [start, end) for segmented/resumable processing |
| `--frame-write-start` | 0-based first frame written; earlier frames serve as model context only (segment overlap) |
| `--residue-only` | Residue-only second pass: the input is already a first-pass result |

**Mutual exclusion**: `--time-range` conflicts with `--frame-start/--frame-end/--frame-write-start`; `--residue-only` conflicts with all range parameters. `--frame-end` must be greater than `--frame-start`.

> Without `-c` the whole frame is scanned; if you know the subtitle position, always pass the area explicitly.

### Examples

```bash
# 1. Fully automatic (STTN-auto, saved next to input)
python backend/main.py -i "D:/video/lecture.mp4"

# 2. Specific algorithm, area and output
python backend/main.py -i input.mp4 -o output.mp4 -m minimax -c 850,1000,200,1700

# 3. Only 10.5s-30s
python backend/main.py -i input.mp4 -o out.mp4 --time-range 10.5-30

# 4. Manual second segment with 2-frame overlap context (0-based frames)
python backend/main.py -i input.mp4 -o seg2.mp4 --frame-start 598 --frame-end 1200 --frame-write-start 600

# 5. Batch processing (PowerShell)
Get-ChildItem D:\videos\*.mp4 | ForEach-Object {
    python backend/main.py -i $_.FullName -m minimax -c 850,1000,200,1700
}

# 6. Residue-only second pass
python backend/main.py -i first_pass.mp4 -o final.mp4 --residue-only
```

---

## 4. FAQ

| Symptom | Fix |
|---|---|
| Startup error `RuntimeError: ... big-lama.pt errno 2` | Weights missing or quarantined by antivirus. Add an AV exclusion, then re-download per section 1.4 |
| CUDA out of memory | Switch to STTN-auto; or lower MiniMax ProcessWidth; for 4K use ProcessWidth 1280–1920 |
| A segmented task was interrupted | Re-run the same task — it resumes from the segment checkpoint automatically. **Changed parameters refuse to resume**; delete the `<name>_no_sub_segments/` folder next to the output to start fresh |
| Subtitles not fully removed | Increase "Area deviation pixels"; make sure the selection covers everything; keep residue second pass on; switch to MiniMax/LAMA |
| Ghosting at segment boundaries | Increase "Segment overlap frames" (2→4) and timeline expansion frames |
| CLI produces an empty output | Check `--frame-end` > `--frame-start` and that the time range is valid (the app exits with an explicit error) |

---

## 5. Directory layout

See [README.md](README.md) / [README_en.md](README_en.md).
