简体中文 | [English](README_en.md)

<div align="center">
  <img src="design/subzero.ico" alt="净影 SubZero" width="128" height="128">

# 净影 SubZero

**基于 AI 的视频硬字幕擦除工具**

![License](https://img.shields.io/badge/License-Apache%202-red.svg)
![python version](https://img.shields.io/badge/Python-3.13-blue.svg)
![support os](https://img.shields.io/badge/OS-Windows-green.svg)
![version](https://img.shields.io/badge/version-1.3.1-1D9E75.svg)
</div>

---

## 简介

净影（SubZero）是一款将视频中的硬字幕去除的桌面软件：OCR 检测字幕位置，AI 修复算法对字幕区域进行内容填充，无损分辨率输出。支持 GUI 与 CLI 两种方式，长视频自动分段、断点续跑，任务队列本地持久化。

**1.3.1 修复与改进**：

- **静态画面字幕漏检修复**：重复帧跳过改为「整帧 + 字幕带」双尺度判定——录屏、课件、固定机位这类画面几乎不动的素材里，一句字幕只占整帧 0.4% 面积，旧逻辑按整帧均值差判成「与上一帧相同」并复用无字幕的检出，导致整句字幕从未进修复区、原样留在成片里（实测漏检 31%，最长连续 201 帧未擦）。修复后漏检降到 2.7%，且剩下的都是 1~3 帧的淡入淡出边界，由时间线外扩覆盖
- **快照完整还原**：处理中的任务也会落盘实时用时与开始时刻，跨续跑累计；恢复时完成度按 checkpoint 的**帧**进度还原（不是按段数），待处理行显示上次用时与开始时间
- **续跑不再从 1% 重来**：点运行时以已完成帧占比作为起始进度，处理中进度单调不回退；没有断点记录的任务在加载快照时明确提示「将从头开始」
- **残留二修默认开启**：二遍兜底改为默认开启（质量优先），无残留时只多一遍检测、不重编码

**1.3.0 新增能力**：

- **A/B 实时对比预览**：处理中预览自动切成 A/B 双框——A 框为源帧（叠加字幕选区），B 框为实时修复结果，查找字幕与擦除全过程逐帧可见；播放键随状态切换语义（处理中 = 暂停/恢复实时预览，完成后 = A/B 同步回放），全屏对比按视频比例自动选择左右（竖屏）/上下（横屏）分屏，退出全屏不断播、主预览恢复同框续看
- **残留二修严格扫描**：二遍兜底改为逐帧采样 + 禁用重复帧跳过，1~2 帧的短残影不再落在采样间隙里被漏掉（宁可慢不可漏），并改用帧精确 seek 读取成片，避免长 GOP 吸附关键帧导致扫描错位；**默认开启**（质量优先，可在高级设置关闭），无残留时只多一遍检测、不重编码
- **SageAttention 可选加速**：MiniMax 自注意力可切换 SageAttention（INT8 量化 K），实测端到端快约四成、显存峰值持平；未安装或运行异常自动回落默认实现，默认关闭
- **注意力后端优化**：回落路径钉 cuDNN 后端，画质零损失再快约 14%；推理改用 `inference_mode` + cuDNN benchmark
- **条带修复贴回羽化**：修复像素按掩膜羽化 alpha 合成回原帧，消除修复区域矩形边框痕迹
- **预览视频信息条**：预览画面左上角悬浮显示分辨率/帧率/时长/平均码率/文件大小
- **任务总耗时**：任务行底部显示单项总耗时，任务列表底部悬浮队列总耗时胶囊（随快照保存）
- **快照框选「所见即所存」**：保存快照前自动写入当前屏幕选区，修复「屏幕有框、快照为空」

> 本仓库只含源码，**不内置任何自动安装环境或下载权重的逻辑**。环境安装、模型权重下载、各参数详解与常用配置示例，请看 **[使用教程.md](使用教程.md)** · [Tutorial (English)](使用教程_en.md)

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
├── 使用教程.md / 使用教程_en.md     # 环境安装 / 参数详解 / 配置示例 / FAQ
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
