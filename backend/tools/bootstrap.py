"""
@desc: 净影 SubZero 环境自检与自举
       检查 Python 运行时 / GPU / 驱动 / 依赖库 / 模型权重；
       权重缺失时支持从镜像源自动下载（断点续传）。
       CLI: python backend/tools/bootstrap.py --check | --fix-weights
"""
import os
import sys
import json
import urllib.request
import urllib.error

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

# 权重清单：(相对路径, 最小体积MB, 下载URL(None=需手动获取))
# 分片文件(*_1.pt 等)是历史分发残留，运行时不需要，不检查。
WEIGHT_MANIFEST = [
    # 字幕检测（PP-OCRv5 server / fast）
    ('V5/ch_det/inference.pdiparams', 80,
     'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/PP-OCRv5_server_det_infer.tar'),
    ('V5/ch_det_fast/inference.pdiparams', 4,
     'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/PP-OCRv5_mobile_det_infer.tar'),
    # 修复算法权重
    ('big-lama/big-lama.pt', 190,
     'https://hf-mirror.com/Sanster/models/resolve/main/big-lama.pt'),
    ('sttn-auto/infer_model.pth', 60, None),
    ('sttn-det/sttn.pth', 60, None),
    ('propainter/ProPainter.pth', 145,
     'https://github.com/sczhou/ProPainter/releases/download/v0.1.0/ProPainter.pth'),
    ('propainter/raft-things.pth', 18,
     'https://github.com/sczhou/ProPainter/releases/download/v0.1.0/raft-things.pth'),
    ('propainter/recurrent_flow_completion.pth', 17,
     'https://github.com/sczhou/ProPainter/releases/download/v0.1.0/recurrent_flow_completion.pth'),
    ('minimax-remover/transformer/diffusion_pytorch_model.safetensors', 2100,
     'https://hf-mirror.com/PengWeixuan/MiniMax-Remover/resolve/main/transformer/diffusion_pytorch_model.safetensors'),
    ('minimax-remover/transformer/config.json', 0,
     'https://hf-mirror.com/PengWeixuan/MiniMax-Remover/resolve/main/transformer/config.json'),
    ('minimax-remover/vae/diffusion_pytorch_model.safetensors', 480,
     'https://hf-mirror.com/PengWeixuan/MiniMax-Remover/resolve/main/vae/diffusion_pytorch_model.safetensors'),
    ('minimax-remover/vae/config.json', 0,
     'https://hf-mirror.com/PengWeixuan/MiniMax-Remover/resolve/main/vae/config.json'),
    ('minimax-remover/scheduler/scheduler_config.json', 0,
     'https://hf-mirror.com/PengWeixuan/MiniMax-Remover/resolve/main/scheduler/scheduler_config.json'),
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


def check_weights():
    """逐项核对权重文件存在且体积达标，返回 (结果, 缺失可下载列表)"""
    missing = []
    items = []
    for rel, min_mb, url in WEIGHT_MANIFEST:
        path = os.path.join(MODELS_DIR, rel.replace('/', os.sep))
        exists = os.path.isfile(path)
        size_ok = exists and os.path.getsize(path) >= min_mb * 1024 * 1024
        items.append({'path': rel, 'ok': size_ok})
        if not size_ok:
            missing.append({'path': rel, 'url': url, 'min_mb': min_mb})
    ok = not missing
    detail = f'{len(items)} 项齐全' if ok else f'缺失 {len(missing)} 项: ' + ', '.join(m['path'] for m in missing[:3]) + ('...' if len(missing) > 3 else '')
    return {'name': '模型权重', 'ok': ok, 'detail': detail}, missing


def check_all():
    results = [check_python(), check_gpu(), check_deps()]
    weights_result, missing = check_weights()
    results.append(weights_result)
    return results, missing


def download_file(url, dest, progress_cb=None):
    """下载文件（支持断点续传），dest 为最终路径。
    系统代理可能拦截国内镜像源（隧道 502），先直连、失败再走默认代理重试。"""
    tmp = dest + '.part'
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    start = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
    headers = {'User-Agent': 'SubZero-Bootstrap/1.0'}
    if start > 0:
        headers['Range'] = f'bytes={start}-'

    last_err = None
    for use_proxy in (False, True):
        try:
            if use_proxy:
                opener = urllib.request.build_opener()  # 默认策略（含系统代理）
            else:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 强制直连
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=60) as resp:
                total = resp.headers.get('Content-Length')
                total = int(total) + start if total else None
                mode = 'ab' if start > 0 else 'wb'
                done = start
                with open(tmp, mode) as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb:
                            progress_cb(done, total)
            os.replace(tmp, dest)
            return dest
        except Exception as e:
            last_err = e
            # 直连已写入部分数据时，换代理继续续传
            start = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
            if start > 0:
                headers['Range'] = f'bytes={start}-'
    raise last_err


def fix_weights(missing, progress_cb=None, log=print):
    """下载缺失权重；url 为 None 的项提示手动获取"""
    fixed, manual = [], []
    for m in missing:
        if not m['url']:
            manual.append(m['path'])
            log(f'⚠ {m["path"]} 无自动下载源，请按 README 手动获取')
            continue
        dest = os.path.join(MODELS_DIR, m['path'].replace('/', os.sep))
        log(f'⬇ 下载 {m["path"]} ...')
        try:
            download_file(m['url'], dest, progress_cb)
            log(f'✅ {m["path"]} 完成')
            fixed.append(m['path'])
        except Exception as e:
            log(f'❌ {m["path"]} 下载失败: {e}（已保留 .part 可续传）')
    return fixed, manual


def main():
    import argparse
    parser = argparse.ArgumentParser(description='净影 SubZero 环境自检')
    parser.add_argument('--check', action='store_true', help='仅检查')
    parser.add_argument('--fix-weights', action='store_true', help='下载缺失权重')
    args = parser.parse_args()

    results, missing = check_all()
    print('=' * 56)
    print('净影 SubZero 环境自检报告')
    print('=' * 56)
    for r in results:
        mark = '✅' if r['ok'] else '❌'
        print(f'{mark} {r["name"]}: {r["detail"]}')
    if args.fix_weights and missing:
        fix_weights(missing)
    elif missing:
        print(f'\n提示: 运行 --fix-weights 可自动下载 {sum(1 for m in missing if m["url"])} 项缺失权重')
    # 汇总退出码：0 全过 / 1 有缺失
    sys.exit(0 if all(r['ok'] for r in results) else 1)


if __name__ == '__main__':
    main()
