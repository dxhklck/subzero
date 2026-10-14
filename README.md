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

## 简介

净影（SubZero）是一款将视频中的硬字幕去除的桌面软件：OCR 检测字幕位置，AI 修复算法对字幕区域进行内容填充，无损分辨率输出。支持 GUI 与 CLI 两种方式，长视频自动分段、断点续跑，任务队列本地持久化。

> 本仓库只含源码，**不内置任何自动安装环境或下载权重的逻辑**。环境安装、模型权重下载、各参数详解与常用配置示例，请看 **[使用教程.md](使用教程.md)**。

## 快速开始

```bash
# 1. 安装 Python 3.10+ 与依赖（torch cu128 + paddlepaddle-gpu cu129，见教程）
pip install -r requirements.txt

# 2. 手动下载模型权重放入 backend/models/（清单与直链见教程）

# 3. 启动
python gui.py                                  # GUI
python backend/main.py -i 视频.mp4 -o 输出.mp4   # CLI
```

## 目录结构

```
subzero/
├── gui.py                          # GUI 入口
├── 使用教程.md                      # 环境安装 / 参数详解 / 配置示例 / FAQ
├── backend/
│   ├── main.py                     # CLI 入口 / 处理主流程
│   ├── config.py                   # 全部配置项定义（版本号单一来源）
│   ├── subtitle_detect.py          # 字幕检测（PaddleOCR PP-OCRv5）
│   ├── hardware_accelerator.py     # 设备判定（CUDA / DirectML / MPS / CPU）
│   ├── tools/
│   │   ├── strip_inpaint.py        # 条带裁剪修复包装器（核心优化）
│   │   ├── segmenter.py            # 长视频分段计划与 checkpoint（断点续跑）
│   │   ├── segment_executor.py     # 段执行 / 拼接 / 音频封装
│   │   ├── args_handler.py         # CLI 参数解析与互斥校验
│   │   ├── inpaint_tools.py        # 掩膜生成 / 批次切分 / 区间工具
│   │   ├── video_io.py             # 帧预读取 / ffmpeg 管道写入
│   │   ├── model_scanner.py        # 权重完整性启动扫描
│   │   ├── bootstrap.py            # 环境自检（仅检查，不安装不下载）
│   │   ├── version_service.py      # 版本检查更新
│   │   └── process_manager.py      # 子进程生命周期管理
│   ├── inpaint/                    # 修复算法：STTN / LAMA / OpenCV / MiniMax-Remover
│   ├── models/                     # 模型权重（需按教程自行下载）
│   ├── ffmpeg/                     # ffmpeg 可执行文件（按平台）
│   └── interface/                  # 7 语言界面文案（ini）
├── ui/                             # PySide6 + qfluentwidgets 界面（首页/设置/关于）
└── design/                         # 图标等设计资源
```

## 许可证

- 本项目代码基于 [Apache License 2.0](LICENSE) 开源，改编自 [video-subtitle-remover (VSR)](https://github.com/YaoFANGUK/video-subtitle-remover)，感谢原作者 YaoFANGUK 的开源贡献（Apache-2.0 归属声明保留于 LICENSE 文件）
- **MiniMax-Remover 模型权重采用 CC-BY-NC-4.0 授权，仅限非商业用途**；商用请评估替代算法
