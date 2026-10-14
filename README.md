简体中文 | [English](README_en.md)

<div align="center">
  <img src="design/subzero.ico" alt="净影 SubZero" width="128" height="128">

# 净影 SubZero

**基于 AI 的视频硬字幕擦除工具**

![License](https://img.shields.io/badge/License-Apache%202-red.svg)
![python version](https://img.shields.io/badge/Python-3.13-blue.svg)
![support os](https://img.shields.io/badge/OS-Windows-green.svg)
![version](https://img.shields.io/badge/version-1.1.0-1D9E75.svg)
</div>

---

## 项目简介

净影（SubZero）是一款将视频中的硬字幕去除的桌面软件，基于 AI 模型对字幕区域进行内容填充，无损分辨率输出。

主要特性：

- **条带裁剪修复**（本项目的核心优化）：字幕区域外扩后裁剪送修、贴回原帧，窗口外像素零损失——720p 实测比全帧直推**提速 6.9 倍**，显存同比例下降
- **4 种修复算法**：MiniMax-Remover（推荐）/ STTN / LAMA / OpenCV
- **长视频分段处理 + 断点续跑**：超过设定时长（默认 600 秒，可调）的视频自动切段执行，中断或关闭后重跑同一任务自动从断点续接（段级 checkpoint，参数变更防错配）；段间可加重叠帧（默认 2）消除边界接缝
- **任务列表持久化**：队列保存在本地，重开软件自动恢复；上次未完成的任务标记「可续跑」，不会自动开跑
- **CLI 命令行**：支持字幕区域、时间区间、帧范围（分段/重叠）、残留二修等参数，可脚本化批处理（见下文）
- **GPU 监控**：界面底部实时显示显存（含本次任务峰值）、GPU 利用率、内存与处理速度
- **A/B 对比滑块**：处理中拖动分界线，左原帧右修复帧，逐像素验收擦除效果
- **预览 letterbox**：任意比例视频按原始比例显示 + 黑边，框选坐标精确映射回原图像素
- **性能预设**：480p / 720p / 1080p / 4K 一键切换推荐参数
- **明暗主题**：浅色 / 深色 / 跟随系统，主色青绿
- **任务列表拖放**：单个或多个视频文件直接拖入队列，名称 / 进度 / 状态一目了然
- **权重完整性扫描**：启动时自动核对模型权重存在性与体积，杀毒软件静默隔离 `.pt` 时立即提示并可一键修复
- **零控制台**：双击启动全程无黑窗口，日志集成在主界面

## 硬件要求

| 项目 | 要求 |
|---|---|
| 显卡 | NVIDIA GTX 10 系及以上（20/30/40/50 系全支持，50 系需 CUDA 12.9 编译的 Paddle，本仓库已内置） |
| 显存 | 建议 ≥ 8GB（MiniMax 480p 条带模式峰值约 7.4GB） |
| 驱动 | 建议升级到较新版本（r575+）；驱动过旧时程序会自动回退 CPU 检测并在日志中提示 |
| 系统 | Windows 10/11 x64 |

## 快速开始

### 便携版（推荐）

1. 解压到**纯英文路径**（中文路径会导致 PaddleOCR 模型误报文件不存在）
2. 若解压时杀毒软件拦截了 `backend/models/` 下的 `.pt/.pth` 权重文件，请先给目录添加杀毒排除项再重新解压
3. 双击 `启动视频字幕擦除-GUI无黑窗.exe`

### 源码运行

```bash
# 1. 安装 Python 3.13 与 CUDA 驱动
# 2. 安装依赖（torch cu128 + paddlepaddle-gpu cu129）
pip install -r requirements.txt
# 3. 下载模型权重（见下节）放入 backend/models/
# 4. 启动 GUI
python gui.py
```

## 模型权重

| 模型 | 大小 | 用途 |
|---|---|---|
| PP-OCRv5 (server) | ≈ 89MB | 字幕检测 |
| big-lama | 196MB | LAMA 修复 |
| sttn | 63MB | STTN 修复 |
| MiniMax-Remover | ≈ 2.6GB | MiniMax 修复（推荐） |

权重文件体积较大，不随源码仓库分发。请在 Release 附件或联系作者获取下载链接，解压后放入 `backend/models/`。启动时会自动核对权重完整性（缺失/体积异常会在日志与界面提示），也可用设置页的**环境自检**手动核对与一键修复。

## 命令行（CLI）

除 GUI 外，也可用命令行批处理（参数不写入 GUI 配置）：

```bash
# 基本用法（-o 省略时输出到输入同目录 <名>_no_sub.mp4）
python backend/main.py -i 视频.mp4 -o 输出.mp4

# 指定算法
python backend/main.py -i 视频.mp4 -o 输出.mp4 --inpaint-mode minimax

# 限定字幕区域与时间区间（原图像素坐标，区域可重复多个）
python backend/main.py -i 视频.mp4 -o 输出.mp4 --sub-area "560,720,0,1280" --time-range "10.5-30"

# 帧范围（0-based，end 排他）
python backend/main.py -i 视频.mp4 -o 输出.mp4 --frame-start 100 --frame-end 500

# 残留二修：输入已是第一遍修复产物，仅做残留检测 + 二次修复（含音频封装）
python backend/main.py -i 第一遍输出.mp4 -o 二修成片.mp4 --inpaint-mode lama --residue-only
```

说明：

- `--inpaint-mode` 可选 `sttn-auto` / `sttn-det` / `lama` / `opencv` / `minimax`；省略时沿用 GUI 设置
- `--sub-area` 任一坐标为负时需用等号形式（如 `--sub-area=-10,720,0,1280`）
- `--time-range` 与 `--frame-start/--frame-end` 互斥；`--residue-only` 不能与帧范围类参数同用
- GUI 的「视频分段时长(秒)」「段间重叠帧数」设置同样作用于 CLI 分段场景；分段底层参数为 `--frame-start` / `--frame-end` / `--frame-write-start`（重叠上下文帧只推理不写入）

## 目录结构

```
subzero/
├── gui.py                      # GUI 入口
├── backend/main.py             # CLI 入口 / 处理主流程
├── backend/tools/strip_inpaint.py   # 条带裁剪修复包装器（核心优化）
├── backend/tools/segmenter.py       # 长视频分段计划与 checkpoint（断点续跑）
├── backend/tools/model_scanner.py   # 权重完整性启动扫描
├── backend/inpaint/            # 四种修复算法实现
├── backend/models/             # 模型权重（需下载）
├── ui/                         # PySide6 + qfluentwidgets 界面
└── python/                     # 内嵌 Python 运行时（便携版）
```

## 许可证

- 本项目代码基于 [Apache License 2.0](LICENSE) 开源，改编自 [video-subtitle-remover (VSR)](https://github.com/YaoFANGUK/video-subtitle-remover)，感谢原作者 YaoFANGUK 的开源贡献（Apache-2.0 归属声明保留于 LICENSE 文件）
- **MiniMax-Remover 模型权重采用 CC-BY-NC-4.0 授权，仅限非商业用途**；商用请评估替代算法
