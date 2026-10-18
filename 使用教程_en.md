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

Download source: GitHub Releases `https://github.com/dxhklck/subzero/releases` (weights attached to a Release)

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

### 2.0 Launch options

- `启动视频字幕擦除.bat`: runs with a console window (logs go to both the console and the Monitor)
- `启动视频字幕擦除-GUI无黑窗.exe`: **single-window mode** — no console, all logs inside the app (recommended for daily use)
- The Monitor shows identical content either way; on startup an environment banner (version / Python / GPU / CUDA) is printed once

### 2.1 Task queue

- **Add**: pick files via the button, or drag one/multiple videos into the queue.
- **Persistence**: the queue is saved locally and restored on relaunch; the status line shows "Queue auto-saved · N tasks · M resumable · queue ~X left · saved at time" — click it to open the queue file's folder.
- **Resume badge**: unfinished segmented tasks show "⏸ resumable k/n".
- **Per-task inpaint model**: right-click a task → "Inpaint model" to assign a model to **that single task** (STTN-auto / STTN-det / LAMA / OpenCV / MiniMax-Remover) instead of following the global setting; the task name then shows a suffix tag (e.g. "· MiniMax-Remover"). Unset = follow global (default). Select multiple rows and right-click to set the same model in batch. The choice is stored with the task snapshot and survives restarts; changing it participates in the resume check (same as changing the algorithm — old progress is refused).
- **Run a single task**: right-click a task → "Run This Task" processes **only that video**, leaving the rest of the queue untouched; the entry is greyed out while another task is running. Completed/failed tasks can also be re-run (auto-reset to pending, output saved with a number suffix — old results are never overwritten).
- **Params-changed badge**: after changing the algorithm/area/etc., resume is refused and the card shows "⛔ params changed" — right-click "Delete old segment progress & reset" (confirmation lists the folder and segment progress) to start fresh, no manual folder hunting.
- **Timing & estimates**: running tasks show "elapsed mm:ss / est. mm:ss" (extrapolated from the task's own speed once progress ≥3%, otherwise from historical speed; `--` when no estimate is possible); pending tasks show "Est. ~mm:ss"; completed/failed tasks show "Took mm:ss". After the first finished task, estimates keep calibrating to your machine.
- **No-overwrite output**: if the output already exists (e.g. a previous result is still locked), a numbered copy is saved instead (`_no_sub_2.mp4`, `_3`, …) — old results are never overwritten.
- **Failure triage**: on failure the Monitor tab holds the full traceback; a "崩溃报告_<name>_<timestamp>.txt" (environment + main-process log tail + traceback) is written next to the output; right-click a task → open the output folder.
- **Subtitle area**: select a task and draw rectangles on the preview (multiple allowed); with no selection the app auto-detects full screen. Areas are stored in original-pixel coordinates and follow task selection. Mask strategy is fully automatic: frame groups with detections follow the detected boxes plus adaptive dilation (8–40 px horizontally, scaled by text height) so the mask tracks the actual subtitle width; a group with no valid detection at all (OCR miss / timeline-expanded segment) falls back to the drawn rectangle — no manual toggle
- **Time range (AB sections)**: mark ranges on the slider to process only those parts. **Task-level global**: frames outside the range are neither detected nor repainted (passed through as-is); all selection boxes of a task share the same AB sections. Press `[` to mark a start, `]` to mark an end — the selected range shows live on the **range bar above the playbar** (translucent white) and at the bottom of the preview.
- **Per-box time range**: with a selection box selected (clicked), press `Alt+[` to set the current frame as its time start, `Alt+]` as its end, `Alt+\` to clear (unset = active for the whole video). The range bar above the playbar shows **both the global AB sections and the current box's colored local band** at once; matching bands also appear at the bottom of the preview. Different boxes can have different start/end positions; a box's region is not repainted outside its own time window.
- **Frame-by-frame preview**: Left/Right arrows move by the "Preview frame step" (default 1 frame, configurable 1–300 in Settings) for precise subtitle-frame positioning; `Ctrl+Left/Right` = ±1 s, `Shift+Left/Right` = ±5 s for fast coarse seeking.

#### Subtitles at different positions in different time segments

For "subtitle position changes over time" (e.g. a corner bug in the intro plus dialogue at the bottom), the recommended way is **per-box time range**: draw the area for the corner bug → click to select it → mark its appearance window with `Alt+[` / `Alt+]` — one task does it all.

Alternatively chain two tasks (older versions / special cases):

1. **Task 1**: open the original video → draw only area A → set AB to the first segment (e.g. 0–120s) → run → result 1. Frames outside the range pass through untouched; only area A inside the range is erased.
2. **Task 2**: open result 1 → draw only area B → set AB to the second segment (e.g. 120s–end) → run → final result.

Cost: two full encodes (crf 20 tier; the second-generation loss is visually invisible) plus one residue pass per run. Frames outside AB are never repainted, so the two segments compose correctly.

### 2.2 Inpainting algorithms (Settings page)

| Algorithm | Traits | Use case |
|---|---|---|
| STTN-auto | Fully automatic, fastest | Batch processing |
| MiniMax-Remover | Strip-crop inpainting, best quality/speed balance | Motion / anime |
| LAMA | High quality on static backgrounds | Static scenes / images |
| OpenCV | Classical, no large model | Quick checks |

### 2.3 Settings reference

**General**

| Setting | Default | Description |
|---|---|---|
| Interface language | Chinese | Restart to apply |
| Hardware acceleration | On | GPU inference toggle |
| Segment duration (s) | 300 | Videos longer than this are processed in segments; 0 = off. Affects resume granularity and per-segment overhead only (overlap frames cover seams), not quality |
| Segment overlap frames | 2 (0–10) | Adjacent segments exchange overlap frames as model context (not written to output), removing seam artifacts |
| Preview frame step | 1 (1–300) | Frames moved per Left/Right arrow press in the preview: 1 = frame by frame (default); increase for fast coarse seeking. Ctrl+Left/Right = ±1 s, Shift+Left/Right = ±5 s (fixed) |
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
| Residue second pass | On | After the first pass, re-detect leftovers and repair them automatically. The second pass automatically uses more sensitive detection parameters (box threshold 0.6→0.3, box expansion 1.5→2.0, at 960px resolution for an independent catch), so fragment leftovers of outlined/stylized subtitles (common in anime) are detected too; false positives are constrained by requiring overlap with first-pass regions |

**MiniMax-Remover**

| Setting | Default | Description |
|---|---|---|
| MaxLoadNum | 121 (5–121) | Frames fed to the GPU per pass (4k+1); mainly a speed parameter — larger blocks amortize fixed overhead. Little effect on peak VRAM (set by strip resolution); the app auto-checks free VRAM and shrinks if needed |
| NumInferenceSteps | 8 (1–12) | Diffusion denoise steps. 8 is the sweet spot: 6 is ~2% faster but degrades quality, 12 is ~7.5% slower |
| Iterations | 6 (0–12) | Refinement iterations per interval |
| ProcessWidth | 1280 (0–2160) | Strip processing resolution cap. When strip width exceeds this value, inference runs at the reduced width and the output is upscaled back; narrow strips are unaffected. For 1080p+ keep 1280: measured ~8x faster repair on wide strips with minimal quality loss (Lanczos upscale). 0 = always native resolution |
| StripPadding | 128 (0–300) | Padding above/below the subtitle strip for model context |
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

### 2.5 Monitor tab (a terminal)

- **The Monitor is a terminal**: all raw output from detection/inpainting streams into the Monitor tab — progress bars refresh in place (never piling up), and `[Residue]` repair logs and tqdm progress are all visible line by line
- Each launch is a fresh session (only the environment banner); starting a new task clears it and scrolls from the top
- Right-click menu: copy selection / copy all / export to `config/logs/` / clear
- The "Log" tab is a formatted summary for quick browsing; use "Monitor" for troubleshooting

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
| `-m, --inpaint-mode` | `sttn-auto` / `minimax` / `lama` / `opencv`; omitted = the mode selected in GUI settings (config.json) |
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
| A segmented task was interrupted | Re-run the same task — it resumes from the segment checkpoint automatically. **Changed parameters refuse to resume** and the card shows "⛔ params changed" — right-click "Delete old segment progress & reset" (with confirmation) to start fresh, safer than deleting folders manually |
| Output file locked / write failed | The app already works around this: an existing output is saved as `_no_sub_2.mp4` etc. instead of overwriting; if a leftover python subprocess from the last run still holds the GPU, kill the extra process |
| Anime subtitles leave fragments | The residue second pass already uses sensitive detection parameters; if residue remains, raise MiniMax "Iterations" (6→8~12) and "StripPadding" (128→160+); for vertical/scrolling staff lists, manually select the whole strip |
| Subtitles not fully removed | Increase "Area deviation pixels"; make sure the selection covers everything; keep residue second pass on; switch to MiniMax/LAMA |
| Ghosting at segment boundaries | Increase "Segment overlap frames" (2→4) and timeline expansion frames |
| Task failed / crash | Check the traceback in the Monitor tab; a "崩溃报告_<name>_<timestamp>.txt" (environment + log tail + traceback) sits next to the output — attach it when reporting issues |
| CLI produces an empty output | Check `--frame-end` > `--frame-start` and that the time range is valid (the app exits with an explicit error) |

---

## 5. Directory layout

See [README.md](README.md) / [README_en.md](README_en.md).
