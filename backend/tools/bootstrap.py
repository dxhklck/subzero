"""
@desc: 净影 SubZero 环境自检
       检查 Python 运行时 / GPU / 驱动 / 依赖库 / 模型权重。
       本程序不内置任何环境安装或权重下载逻辑——缺失项由用户按 README/使用教程手动补齐。
       CLI: python backend/tools/bootstrap.py --check
"""
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
MODELS_DIR = os.path.join(PROJECT_ROOT, 'backend', 'models')

# 关键依赖库（import 名 -> 展示名）
PYTHON_DEPS = [
    ('torch', 'PyTorch'),
    ('paddle', 'PaddlePaddle'),
    ('cv2', 'OpenCV'),
    ('numpy', 'NumPy'),
    ('PIL', 'Pillow'),
    ('psutil', 'psutil'),
    ('qfluentwidgets', 'QFluentWidgets'),
    ('diffusers', 'diffusers'),
]

WEIGHT_MANIFEST = [
    # 字幕检测（PP-OCRv5 server / fast）
    {'path': 'V5/ch_det/inference.pdiparams', 'min_mb': 80,
     'sha256': '183146fe9d9910352f68482f623bcbbb9fa7b9e8fa1463b9ad288cef00524d2d'},
    {'path': 'V5/ch_det_fast/inference.pdiparams', 'min_mb': 4,
     'sha256': 'afa1820cb16c1fd0dad589d0f8b389139061c1ef6d68019685fd07be997dda5b'},
    # 修复算法权重
    {'path': 'big-lama/big-lama.pt', 'min_mb': 190,
     'sha256': '7ba7aa7ac37a4d41fdbbeba3a2af7ead18058552997e3a3cd1a3b2210c9e6b4c'},
    {'path': 'sttn-auto/infer_model.pth', 'min_mb': 60,
     'sha256': 'c8408c9dd1300bd7000ea3a81b8236db89d9c0f185837781e38283c1e464675b'},
    {'path': 'sttn-det/sttn.pth', 'min_mb': 60,
     'sha256': '25b0c2c30042d82efd1893bd42ec726764262d94115393a1718f8d65d2a7817b'},
    {'path': 'minimax-remover/transformer/diffusion_pytorch_model.safetensors', 'min_mb': 2100,
     'sha256': 'a379d98432970f614befb260357153edcd01a99748cf7f6dabe1a230c159b213'},
    {'path': 'minimax-remover/transformer/config.json', 'min_mb': 0,
     'sha256': '7d218c1d52a04e9e4f0e89ca72c9743daa408177e3bdccde90de1334801f8f77'},
    {'path': 'minimax-remover/vae/diffusion_pytorch_model.safetensors', 'min_mb': 480,
     'sha256': 'd6e524b3fffede1787a74e81b30976dce5400c4439ba64222168e607ed19e793'},
    {'path': 'minimax-remover/vae/config.json', 'min_mb': 0,
     'sha256': 'f0c1cc1d7decb5badc384f54691746a27a9aeff49f7ebca974e583389342d527'},
    {'path': 'minimax-remover/scheduler/scheduler_config.json', 'min_mb': 0,
     'sha256': '3fed2abbd9bbc301a74db01947198057ec5049808910dccab320925bf27bea6e'},
    # ffmpeg（Windows）
    {'path': 'backend/ffmpeg/win_x64/ffmpeg.exe', 'min_mb': 100, 'base': 'project',
     'sha256': None},
]


def check_python():
    ok = sys.version_info >= (3, 10)
    return {'name': 'Python 运行时', 'ok': ok,
            'detail': f'{sys.version.split()[0]} ({sys.executable})'}


def check_gpu():
    """torch CUDA + paddle CUDA + 驱动，一次体检"""
    info = {'name': 'GPU / CUDA', 'ok': False, 'detail': ''}
    parts = []
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            major, minor = torch.cuda.get_device_capability(0)
            parts.append(f'torch GPU ✅ {name} (sm_{major}{minor})')
            info['ok'] = True
        else:
            parts.append('torch GPU ❌ 不可用')
    except Exception as e:
        parts.append(f'torch ❌ {e}')
    try:
        import paddle
        if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            parts.append(f'paddle GPU ✅ {paddle.__version__} (CUDA {paddle.version.cuda()})')
        else:
            parts.append(f'paddle GPU ❌ {paddle.__version__} (CPU 版或无卡)')
    except Exception as e:
        parts.append(f'paddle ❌ {e}')
    info['detail'] = '；'.join(parts)
    return info


def check_deps():
    missing = []
    for mod, disp in PYTHON_DEPS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(disp)
    ok = not missing
    detail = '全部就绪' if ok else '缺失: ' + ', '.join(missing)
    return {'name': 'Python 依赖', 'ok': ok, 'detail': detail}


def _entry_abs_path(entry):
    base = PROJECT_ROOT if entry.get('base') == 'project' else MODELS_DIR
    return os.path.join(base, entry['path'].replace('/', os.sep))


def check_weights():
    """逐项核对权重文件存在且体积达标，返回 (结果, 缺失列表)"""
    missing = []
    items = []
    for entry in WEIGHT_MANIFEST:
        path = _entry_abs_path(entry)
        exists = os.path.isfile(path)
        size_ok = exists and os.path.getsize(path) >= entry['min_mb'] * 1024 * 1024
        items.append({'path': entry['path'], 'ok': size_ok})
        if not size_ok:
            missing.append(dict(entry))
    ok = not missing
    detail = f'{len(items)} 项齐全' if ok else f'缺失 {len(missing)} 项: ' + ', '.join(m['path'] for m in missing[:3]) + ('...' if len(missing) > 3 else '')
    return {'name': '模型权重', 'ok': ok, 'detail': detail}, missing


def check_all():
    results = [check_python(), check_gpu(), check_deps()]
    weights_result, missing = check_weights()
    results.append(weights_result)
    return results, missing


def main():
    import argparse
    parser = argparse.ArgumentParser(description='净影 SubZero 环境自检')
    parser.add_argument('--check', action='store_true', help='仅检查')
    args = parser.parse_args()

    results, missing = check_all()
    print('=' * 56)
    print('净影 SubZero 环境自检报告')
    print('=' * 56)
    for r in results:
        mark = '✅' if r['ok'] else '❌'
        print(f'{mark} {r["name"]}: {r["detail"]}')
    if missing:
        print('\n缺失权重请按 README/使用教程手动下载到对应路径')
    # 汇总退出码：0 全过 / 1 有缺失
    sys.exit(0 if all(r['ok'] for r in results) else 1)


if __name__ == '__main__':
    main()
