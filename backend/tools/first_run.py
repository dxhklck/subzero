# -*- coding: utf-8 -*-
"""@desc: 安装版首启自举——检测运行时完整性，缺什么补什么。
   依赖轻量 PySide6（安装包已预烘焙）显示进度窗；
   重依赖（torch cu128 / paddle cu129 / 其余锁定包）走国内镜像在线装；
   权重经 backend.tools.bootstrap 双源（魔搭→GitHub）下载。
   入口: ensure_environment() 由 gui.py 在任何重导入前调用。"""
import os
import sys
import subprocess

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

# 镜像源
TSINGHUA = 'https://pypi.tuna.tsinghua.edu.cn/simple'
PYTORCH_CU128 = 'https://download.pytorch.org/whl/cu128'
PADDLE_CU129 = 'https://www.paddlepaddle.org.cn/packages/stable/cu129/'

LOCK_FILE = os.path.join(PROJECT_ROOT, 'packaging', 'requirements-lock.txt')

# (展示名, pip 参数列表)
INSTALL_STEPS = [
    ('pip 升级', ['-m', 'pip', 'install', '--upgrade', 'pip', '-i', TSINGHUA]),
    ('PyTorch 2.7.0 (CUDA 12.8, ~2.5GB)',
     ['-m', 'pip', 'install', 'torch==2.7.0', 'torchvision==0.22.0',
      '--index-url', PYTORCH_CU128]),
    ('PaddlePaddle-GPU 3.4.0 (CUDA 12.9, ~1GB)',
     ['-m', 'pip', 'install', 'paddlepaddle-gpu==3.4.0', '--index-url', PADDLE_CU129]),
    ('其余依赖 (锁定版本, 100 项)',
     ['-m', 'pip', 'install', '-r', LOCK_FILE, '-i', TSINGHUA]),
]


def is_environment_ready():
    """运行时依赖是否齐备（torch + paddle + qfluentwidgets）"""
    import importlib.util
    for mod in ('torch', 'paddle', 'qfluentwidgets'):
        if importlib.util.find_spec(mod) is None:
            return False
    return True


def weights_ready():
    try:
        sys.path.insert(0, PROJECT_ROOT)
        try:
            from backend.tools import bootstrap
            _, missing = bootstrap.check_all()
            return not missing, missing
        finally:
            try:
                sys.path.remove(PROJECT_ROOT)
            except ValueError:
                pass
    except Exception:
        return False, []


def _pip_ready():
    try:
        return subprocess.run([sys.executable, '-m', 'pip', '--version'],
                              capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def _get_pip_path():
    """返回可用的 get-pip.py 路径（优先捆绑在 runtime 目录），失败返回 None"""
    bundled = os.path.join(sys.prefix, 'get-pip.py')
    if os.path.isfile(bundled) and os.path.getsize(bundled) > 100000:
        return bundled
    import urllib.request
    bundled = os.path.join(os.environ.get('TEMP', '.'), 'get-pip.py')
    for url in ('https://bootstrap.pypa.io/get-pip.py',
                'https://cdn.jsdelivr.net/gh/pypa/get-pip@main/get-pip.py'):
        try:
            urllib.request.urlretrieve(url, bundled)
            if os.path.getsize(bundled) > 100000:
                return bundled
        except Exception:
            continue
    return None


def run_install(log_cb, progress_cb):
    """执行安装步骤。log_cb(str) 输出行；progress_cb(int 0-100) 总进度。"""
    sys.path.insert(0, PROJECT_ROOT)
    try:
        from backend.tools import bootstrap
    finally:
        try:
            sys.path.remove(PROJECT_ROOT)
        except ValueError:
            pass

    steps = []
    # embeddable 无 pip：先引导
    if not _pip_ready():
        gp = _get_pip_path()
        if not gp:
            raise RuntimeError('get-pip.py 不可用（捆绑缺失且在线下载失败），请检查网络')
        steps.append((f'pip 引导 ({os.path.basename(gp)})',
                      [gp, '--no-warn-script-location', '-i', TSINGHUA]))
    steps.extend(INSTALL_STEPS)

    steps = list(INSTALL_STEPS)
    res, missing = weights_ready()
    if not res:
        steps.append((f'模型权重下载 ({len(missing)} 项, ~3.2GB)', 'WEIGHTS'))

    creationflags = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW
    for i, (name, args) in enumerate(steps):
        log_cb(f'▶ {name}')
        progress_cb(int(i / len(steps) * 100))
        if args == 'WEIGHTS':
            fixed, manual = bootstrap.fix_weights(missing, log=log_cb)
            if manual:
                log_cb('⚠ 以下项需手动获取: ' + ', '.join(manual))
            continue
        proc = subprocess.Popen([sys.executable, *args],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace',
                                cwd=PROJECT_ROOT, creationflags=creationflags)
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log_cb('  ' + line[:160])
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f'{name} 失败 (exit {rc})，请检查网络后重试')
    progress_cb(100)


def ensure_environment():
    """gui.py 首行调用。环境齐备返回 False 直接进 GUI；不齐备则跑完自举后重启进程。"""
    if is_environment_ready():
        res, _ = weights_ready()
        return False  # 权重缺失由设置页「环境自检」处理，不阻塞启动

    from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QLabel,
                                   QProgressBar, QPlainTextEdit, QPushButton)
    from PySide6.QtCore import QThread, Signal, Qt

    app = QApplication.instance() or QApplication(sys.argv)

    dlg = QDialog()
    dlg.setWindowTitle('净影 SubZero · 首次运行环境配置')
    dlg.setFixedSize(640, 460)
    lay = QVBoxLayout(dlg)
    tip = QLabel('首次运行需要配置 GPU 运行环境并下载模型数据（约 7.7GB，\n'
                 '国内网络约 15-40 分钟，支持断点续传，失败重开本程序即可继续）。')
    lay.addWidget(tip)
    bar = QProgressBar()
    lay.addWidget(bar)
    logbox = QPlainTextEdit()
    logbox.setReadOnly(True)
    lay.addWidget(logbox)
    btn = QPushButton('取消')
    btn.clicked.connect(dlg.reject)
    lay.addWidget(btn, 0, Qt.AlignRight)

    class Worker(QThread):
        line = Signal(str)
        pct = Signal(int)
        ok = Signal(bool)

        def run(self):
            try:
                run_install(self.line.emit, self.pct.emit)
                self.ok.emit(True)
            except Exception as e:
                self.line.emit(f'❌ {e}')
                self.ok.emit(False)

    w = Worker()
    w.line.connect(logbox.appendPlainText)
    w.pct.connect(bar.setValue)

    result = {'ok': False}

    def on_done(success):
        result['ok'] = success
        btn.setText('完成，启动净影' if success else '关闭重试')

    w.ok.connect(on_done)
    w.start()
    dlg.exec()
    w.stop = getattr(w, 'stop', None)
    if w.isRunning():
        w.wait(1500)

    if result['ok']:
        # 用干净解释器重启进 GUI
        os.execv(sys.executable, [sys.executable] + [a for a in sys.argv])
    sys.exit(0)


if __name__ == '__main__':
    ensure_environment()
