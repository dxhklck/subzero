# -*- coding: utf-8 -*-
"""构建安装版运行时：解包 embeddable、启用 site-packages、预烘焙轻量 UI 依赖。
产物 packaging/runtime/runtime/ 由 Inno Setup 打进安装包。"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNTIME_SRC = os.path.join(ROOT, 'runtime', 'python-3.13.12-embed-amd64.zip')
RT = os.path.join(ROOT, 'runtime', 'runtime')
PY = sys.executable  # 托管 3.13.12，与 embeddable 同版本

# 预烘焙：GUI 首启窗口所需的最小依赖（重依赖 torch/paddle 等首启在线装）
LIGHT = [
    'PySide6==6.11.2',
    'PySide6-Fluent-Widgets==1.7.7',
    'PySideSix-Frameless-Window==0.8.2',
    'psutil==7.2.2',
    'requests==2.34.2',
    'darkdetect==0.8.0',
]

def main():
    # 1) 解包
    if os.path.isdir(RT):
        import shutil
        shutil.rmtree(RT)
    os.makedirs(RT)
    with zipfile.ZipFile(RUNTIME_SRC) as z:
        z.extractall(RT)
    print('extracted embeddable')

    # 2) python313._pth：启用 site + Lib/site-packages
    pth = os.path.join(RT, 'python313._pth')
    with open(pth, 'w', encoding='utf-8') as f:
        f.write('python313.zip\n.\nLib/site-packages\nimport site\n')
    print('patched python313._pth')

    # 3) 预烘焙轻量依赖（--target 直装进 runtime 的 site-packages）
    import subprocess
    sp = os.path.join(RT, 'Lib', 'site-packages')
    cmd = [PY, '-m', 'pip', 'install', *LIGHT,
           '--target', sp, '--no-compile',
           '-i', 'https://mirrors.aliyun.com/pypi/simple/']
    print('installing light deps ...')
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        sys.exit(1)
    print('light deps baked:', ', '.join(LIGHT))
    # 体积
    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, fn in os.walk(RT) for f in fn)
    print(f'runtime total: {total/1024/1024:.0f}MB')

if __name__ == '__main__':
    main()
