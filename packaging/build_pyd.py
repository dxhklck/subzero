# -*- coding: utf-8 -*-
"""构建安装版 pyd 发行目录（源码保护）。

把 backend/ + ui/ + gui.py 经 Cython+MSVC 编译为 .pyd，
产出 packaging/pyd-dist/（由 subzero.iss 打包），源码树不动。

保留 .py（不编译）的白名单：
- __init__.py            包标记（backend/__init__.py 仅 2 行 warnings 过滤）
- backend/tools/first_run.py  首启自举基础设施（gui.py 按文件路径加载，pip/权重下载）
- backend/main.py、gui.py     入口 stub（脚本路径调用兼容），逻辑转 cli.py / gui_main.py 编译

用法: python packaging/build_pyd.py [--keep-c]
编译器要求（本机已备）: MSVC 2022 + Windows SDK + 系统 Python 3.13 头文件
"""
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(HERE, 'pyd-dist')
PY = sys.executable  # 托管 3.13.12（与内嵌/安装版同版本，cp313 ABI 一致）

# 保留 .py 的白名单（相对路径，正斜杠；目录前缀 = 整目录保留）
# - __init__.py：包标记（backend/__init__.py 仅 2 行 warnings 过滤）
# - backend/tools/first_run.py：首启自举基础设施（gui.py 按文件路径加载，pip/权重下载）
# - backend/main.py、gui.py：入口 stub（脚本路径调用兼容），逻辑转 cli.py / gui_main.py 编译
# - backend/scenedetect/：内嵌第三方库 PySceneDetect（BSD 开源，无保护价值）
KEEP_PY = {
    '__init__.py',
    'backend/tools/first_run.py',
    'backend/main.py',
    'gui.py',
}
KEEP_PREFIX = ('backend/scenedetect/',)


def find_build_env():
    """探测 MSVC + Windows SDK + Python 头文件，写入 os.environ。
    绕过 setuptools 的 reg.exe 注册表探测（沙箱黑名单拦截 reg.exe）。"""
    # 1) 系统 Python 头文件（embeddable 不带 include/libs；3.13.x ABI 通用）
    py_inc = py_lib = None
    for cand in (r'C:\Program Files\Python313', r'C:\Program Files\Python312'):
        if os.path.isfile(os.path.join(cand, 'include', 'Python.h')):
            py_inc, py_lib = os.path.join(cand, 'include'), os.path.join(cand, 'libs')
            break
    if not py_inc:
        sys.exit('FATAL: 未找到系统 Python 3.13 头文件（需要 Python.h）')

    # 2) MSVC：vswhere 定位最新安装
    vswhere = rf'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
    msvc_root = None
    if os.path.isfile(vswhere):
        import subprocess
        r = subprocess.run([vswhere, '-latest', '-property', 'installationPath'],
                           capture_output=True, text=True)
        vs_path = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ''
        tools = os.path.join(vs_path, r'VC\Tools\MSVC')
        if os.path.isdir(tools):
            ver = sorted(os.listdir(tools))[-1]
            msvc_root = os.path.join(tools, ver)
    if not msvc_root:
        sys.exit('FATAL: 未找到 MSVC（vswhere）')

    # 3) Windows SDK（取最新版本号）
    sdk_inc_root = r'C:\Program Files (x86)\Windows Kits\10\Include'
    sdk_lib_root = r'C:\Program Files (x86)\Windows Kits\10\Lib'
    sdk_ver = sorted(os.listdir(sdk_inc_root))[-1]
    sdk_inc = os.path.join(sdk_inc_root, sdk_ver)
    sdk_lib = os.path.join(sdk_lib_root, sdk_ver)

    inc = ';'.join([py_inc, os.path.join(msvc_root, 'include'),
                    os.path.join(sdk_inc, 'ucrt'), os.path.join(sdk_inc, 'um'),
                    os.path.join(sdk_inc, 'shared')])
    lib = ';'.join([py_lib, os.path.join(msvc_root, 'lib', 'x64'),
                    os.path.join(sdk_lib, 'ucrt', 'x64'), os.path.join(sdk_lib, 'um', 'x64')])
    os.environ['INCLUDE'] = inc
    os.environ['LIB'] = lib
    # rc.exe（SDK bin）+ cl.exe 必须在 PATH
    sdk_bin = rf'C:\Program Files (x86)\Windows Kits\10\bin\{sdk_ver}\x64'
    cl_bin = os.path.join(msvc_root, 'bin', 'Hostx64', 'x64')
    os.environ['PATH'] = sdk_bin + os.pathsep + cl_bin + os.pathsep + os.environ['PATH']
    # 声明环境已就绪，setuptools 跳过注册表探测（reg.exe 被沙箱拦截）
    os.environ['DISTUTILS_USE_SDK'] = '1'
    os.environ['MSSdk'] = '1'


def copy_tree(src, dst, excludes=()):
    """递归拷贝目录（排除子目录名/文件后缀），返回拷贝的 .py 相对路径列表"""
    py_files = []
    for dp, dn, fn in os.walk(src):
        dn[:] = [d for d in dn if d not in excludes and d != '__pycache__']
        rel_dp = os.path.relpath(dp, src)
        target_dp = dst if rel_dp == '.' else os.path.join(dst, rel_dp)
        os.makedirs(target_dp, exist_ok=True)
        for f in fn:
            if f.endswith(('.pyc',)):
                continue
            rel = os.path.normpath(os.path.join(rel_dp, f)).replace('\\', '/')
            shutil.copy2(os.path.join(dp, f), os.path.join(target_dp, f))
            if f.endswith('.py'):
                py_files.append(rel)
    return py_files


def entry_convert(src_path, dst_path, stub_path, stub_code):
    """入口转换：整文件复制并把 `if __name__ == '__main__':` 块改为 def main():，
    原路径写 stub。块体缩进不变（原本就在 if 块内一级）。"""
    with open(src_path, 'r', encoding='utf-8') as f:
        src = f.read()
    marker = "if __name__ == '__main__':"
    assert src.count(marker) == 1, f'{src_path}: __main__ 块数量异常'
    head, body = src.split(marker, 1)
    with open(dst_path, 'w', encoding='utf-8') as f:
        f.write(head + 'def main():' + body)
    with open(stub_path, 'w', encoding='utf-8') as f:
        f.write(stub_code)


def build():
    find_build_env()

    # 1) 重建 stage
    if os.path.isdir(DIST):
        shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(DIST)

    # 2) 拷贝代码树（不拷权重/ffmpeg/测试——ISS 也不打包它们）
    copy_tree(os.path.join(ROOT, 'backend'), os.path.join(DIST, 'backend'),
              excludes=('models', 'ffmpeg'))
    copy_tree(os.path.join(ROOT, 'ui'), os.path.join(DIST, 'ui'))
    py_all = [os.path.relpath(os.path.join(dp, f), DIST).replace('\\', '/')
              for dp, dn, fn in os.walk(DIST) for f in fn if f.endswith('.py')]

    # 3) 入口转换
    #    stub 的 sys.path 层级：backend/main.py 在子目录（两层 dirname 才到 DIST 根），
    #    gui.py 在根目录（一层）
    entry_convert(
        os.path.join(ROOT, 'backend', 'main.py'),
        os.path.join(DIST, 'backend', 'cli.py'),
        os.path.join(DIST, 'backend', 'main.py'),
        'import os, sys\n'
        'sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n'
        'from backend.cli import main\n\nmain()\n')
    entry_convert(
        os.path.join(ROOT, 'gui.py'),
        os.path.join(DIST, 'gui_main.py'),
        os.path.join(DIST, 'gui.py'),
        'import os, sys\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from gui_main import main\n\nmain()\n')
    py_all += ['backend/cli.py', 'gui_main.py']

    # 4) 生成编译清单（模块点路径 -> 源文件相对路径），跳过白名单
    exts = []
    compiled = []
    for rel in sorted(set(py_all)):
        if rel in KEEP_PY or rel.startswith(KEEP_PREFIX) \
                or os.path.basename(rel) == '__init__.py':
            continue
        mod = rel[:-3].replace('/', '.').replace('\\', '.')
        exts.append((mod, rel))
        compiled.append(rel)
    if not exts:
        sys.exit('FATAL: 编译清单为空')

    setup_py = (
        "import os, sys\n"
        "from Cython.Build import cythonize\n"
        "from setuptools import Extension, setup\n"
        "EX = [\n" +
        ''.join(f"    Extension({m!r}, [{p!r}]),\n" for m, p in exts) +
        "]\n"
        "sys.argv = ['setup.py', 'build_ext', '--inplace']\n"
        "setup(name='subzero-pyd', ext_modules=cythonize(EX, language_level=3))\n")
    with open(os.path.join(DIST, '_build_setup.py'), 'w', encoding='utf-8') as f:
        f.write(setup_py)

    # 5) 编译（工作目录 = DIST，模块名与安装布局一致）
    #    errors='replace'：MSVC 输出为 GBK，避免 UTF-8 解码报错干扰结果判断
    import subprocess
    r = subprocess.run([PY, '_build_setup.py'], cwd=DIST,
                       capture_output=True, text=True, errors='replace')
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        sys.exit('FATAL: Cython 编译失败')
    # 统计 pyd（含 ABI tag 名），按「模块名 -> pyd」核对
    produced = {}
    for dp, dn, fn in os.walk(DIST):
        for f in fn:
            if f.endswith('.pyd'):
                base = f.split('.')[0]
                produced[base] = os.path.join(dp, f)
    missing = [m for m, _ in exts if m.split('.')[-1] not in produced]
    if missing:
        sys.exit(f'FATAL: {len(missing)} 个模块未产出 pyd: {missing}')
    print(f'compiled {len(produced)} pyd modules')

    # 6) 清理：已编译的 .py、.c、构建中间件
    for rel in compiled:
        p = os.path.join(DIST, rel.replace('/', os.sep))
        if os.path.isfile(p):
            os.remove(p)
    for dp, dn, fn in os.walk(DIST):
        for f in list(fn):
            if f.endswith('.c'):
                os.remove(os.path.join(dp, f))
    shutil.rmtree(os.path.join(DIST, 'build'), ignore_errors=True)
    os.remove(os.path.join(DIST, '_build_setup.py'))

    # 7) 校验：dist 里除白名单外不得再有 .py（源码零泄漏）
    leaked = []
    for dp, dn, fn in os.walk(DIST):
        for f in fn:
            if not f.endswith('.py'):
                continue
            rel = os.path.relpath(os.path.join(dp, f), DIST).replace('\\', '/')
            if rel not in KEEP_PY and not rel.startswith(KEEP_PREFIX) \
                    and os.path.basename(rel) != '__init__.py':
                leaked.append(rel)
    if leaked:
        sys.exit(f'FATAL: 源码泄漏 {len(leaked)} 个: {leaked}')
    kept = [rel for rel in KEEP_PY if os.path.isfile(os.path.join(DIST, rel.replace('/', os.sep)))]
    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, fn in os.walk(DIST) for f in fn)
    print(f'pyd-dist ready: {len(produced)} pyd + {len(kept)} kept .py, {total/1024/1024:.1f}MB')


if __name__ == '__main__':
    build()
