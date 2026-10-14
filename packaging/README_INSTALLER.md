# 净影 SubZero 安装版（Inno Setup）构建说明

本目录包含安装版的 Inno Setup 脚本 `subzero.iss`（当前版本 **v1.1.0**）。
安装版采用「薄安装」方案：**源码 + 预烘焙运行时 + 启动器**，重依赖与模型权重在首启时在线自举。

## 前置条件

- **Inno Setup 6+**（含 ISPP 预处理器支持，脚本使用了 `#define`）。
  未安装时可在 <https://jrsoftware.org/isdl.php> 获取；本机当前未安装 ISCC，
  仅交付脚本与说明，不代为安装。
- Python 3.13.12（与 embeddable 运行时同版本，用于预烘焙步骤）。
- `packaging/runtime/python-3.13.12-embed-amd64.zip`（embeddable 运行时源包）。
- `packaging/launcher/SubZero.exe`（安装版启动器，源码见 `packaging/launcher/launcher.c`）。
- 7-Zip（仅便携版分卷打包需要，安装版不走 7z）。

## 构建流程

```
# 1) 预烘焙运行时：解包 embeddable → 启用 site-packages → 装轻量 UI 依赖
#    产物：packaging/runtime/runtime/（PySide6 / qfluentwidgets / frameless 等）
python packaging/build_runtime.py

# 2) 编译安装包（在 packaging/ 目录下执行，或从仓库根目录用完整路径）
ISCC.exe packaging\subzero.iss
```

- 步骤 1 只需在运行时依赖清单变化时重跑；日常发版若预烘焙产物已在仓库中，可直接步骤 2。
- 编译产物：`packaging/output/净影SubZero-Setup-1.1.0.exe`（`OutputBaseFilename` 由
  `#define MyAppVersion` 自动派生，发新版只需改 `MyAppVersion` 一处）。

## 安装版结构（iss 脚本要点）

| 内容 | 来源 | 说明 |
| --- | --- | --- |
| 源码 | `gui.py`、`backend/*`（排除 models/ffmpeg/__pycache__）、`ui/*`、`design/*` | 不含模型权重与 ffmpeg |
| 预烘焙运行时 | `runtime/runtime/*` | embeddable Python 3.13.12 + PySide6 等轻量依赖 |
| 启动器 | `launcher/SubZero.exe` | 安装后改名 `SubZero.exe` |
| 锁定依赖清单 | `requirements-lock.txt` | 供首启 pip 安装使用 |

**首启自举链路**：`SubZero.exe` → `gui.py` 钩子 → `first_run.py`
（pip 国内镜像装 torch cu128 / paddle cu129 / 锁定依赖 → 权重经魔搭/GitHub 双源下载）。
即安装包体积小（约 200MB 级），首次启动需要联网且耗时较长（依赖约 4GB）。

## 安装版 vs 便携版定位差异

| | 安装版（本目录） | 便携版（makedist.py / 启动 bat） |
| --- | --- | --- |
| 分发形态 | `净影SubZero-Setup-x.y.z.exe` 安装向导 | 7z 分卷压缩包，解压即用 |
| 运行方式 | 写入 `Program Files`，开始菜单/桌面快捷方式 | `.\python\python.exe gui.py` 或启动 bat |
| Python 运行时 | 预烘焙 embeddable 3.13.12（轻量 UI 依赖） | 完整内嵌便携 Python（`python/`） |
| 依赖/权重获取 | 首启在线自举（镜像 + 双源下载） | 通常随包分发或由 bootstrap 补齐 |
| 升级方式 | 装新版覆盖（AppId 不变，自动卸载旧版） | 整体替换目录 |
| 适用场景 | 普通用户正式分发 | 免安装/U 盘/内网分发 |

## 发版检查清单

1. `backend/config.py` 的 `VERSION` 与 iss 的 `MyAppVersion` 保持一致（当前 1.1.0）。
2. `design/subzero.ico` 存在（`SetupIconFile` 引用）。
3. `packaging/launcher/SubZero.exe` 已重新编译（启动器逻辑变更时）。
4. 冒烟验证：安装后首次启动走完自举，可正常打开 GUI（`.workbuddy/tests/gui_smoke.py`）。
