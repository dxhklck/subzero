简体中文 | [English](README_en.md)

<div align="center">
  <img src="design/subzero.ico" alt="净影 SubZero" width="128" height="128">

# 净影 SubZero

**基于 AI 的视频硬字幕擦除工具**

![License](https://img.shields.io/badge/License-Apache%202-red.svg)
![python version](https://img.shields.io/badge/Python-3.13-blue.svg)
![support os](https://img.shields.io/badge/OS-Windows-green.svg)
</div>

---

## 项目简介

净影（SubZero）是一款将视频中的硬字幕去除的桌面软件，基于 AI 模型对字幕区域进行内容填充，无损分辨率输出。

主要特性：

- **条带裁剪修复**（本项目的核心优化）：字幕区域外扩后裁剪送修、贴回原帧，窗口外像素零损失——720p 实测比全帧直推**提速 6.9 倍**，显存同比例下降
- **5 种修复算法**：MiniMax-Remover（推荐）/ STTN / LAMA / ProPainter / OpenCV
- **GPU 监控**：界面底部实时显示显存（含本次任务峰值）、GPU 利用率、内存与处理速度
- **A/B 对比滑块**：处理中拖动分界线，左原帧右修复帧，逐像素验收擦除效果
- **性能预设**：480p / 720p / 1080p / 4K 一键切换推荐参数
- **明暗主题**：浅色 / 深色 / 跟随系统，主色青绿
- **任务列表拖放**：单个或多个视频文件直接拖入队列，名称 / 进度 / 状态一目了然
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
| ProPainter | 189MB | ProPainter 修复 |
| MiniMax-Remover | ≈ 2.6GB | MiniMax 修复（推荐） |

权重文件体积较大，不随源码仓库分发。请在 Release 附件或联系作者获取下载链接，解压后放入 `backend/models/`。可用设置页的**环境自检**核对文件完整性。

## 目录结构

```
subzero/
├── gui.py                      # GUI 入口
├── backend/main.py             # CLI 入口 / 处理主流程
├── backend/tools/strip_inpaint.py   # 条带裁剪修复包装器（核心优化）
├── backend/inpaint/            # 五种修复算法实现
├── backend/models/             # 模型权重（需下载）
├── ui/                         # PySide6 + qfluentwidgets 界面
└── python/                     # 内嵌 Python 运行时（便携版）
```

## 许可证

- 本项目代码基于 [Apache License 2.0](LICENSE) 开源，改编自 [video-subtitle-remover (VSR)](https://github.com/YaoFANGUK/video-subtitle-remover)，感谢原作者 YaoFANGUK 的开源贡献（Apache-2.0 归属声明保留于 LICENSE 文件）
- **MiniMax-Remover 模型权重采用 CC-BY-NC-4.0 授权，仅限非商业用途**；商用请评估替代算法
