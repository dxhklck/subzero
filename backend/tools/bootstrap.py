"""
@desc: 净影 SubZero 环境自检与自举
       检查 Python 运行时 / GPU / 驱动 / 依赖库 / 模型权重；
       权重缺失时支持从镜像源自动下载（魔搭主源 → GitHub Release 兜底，断点续传 + SHA256 校验）。
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

# 权重清单：{'path', 'min_mb', 'sha256', 'sources'}；sources 有序下载源，失败自动切换
# 魔搭（国内主源，resolve/master 直链免登录支持 Range 续传）→ GitHub Release（海外兜底）
# 每源: {'url':...} 直存 | {'url':..., 'zip_member':...} 解压 | {'url':..., 'parts':[...]} 拼接
# 分片文件(*_1.pt 等)是历史分发残留，运行时不需要，不检查。
MS_BASE = 'https://modelscope.cn/models/dxhklck/subzero-models/resolve/master/'
# GitHub Release 兜底源：先试当前版本，404 自动落到权重分发页 v1.0.0
# （fix_weights 的逐源切换机制原生支持；权重实际挂载于 v1.0.0 Release，9 资产已验证可达）
try:
    from backend.config import VERSION as _APP_VERSION  # noqa: E402
except Exception:
    _APP_VERSION = '1.0.0'  # 独立运行（CLI 直跑）时兜底到权重分发页
_GH_RELEASES = [f'v{_APP_VERSION}', 'v1.0.0']


def _gh(name):
    """同名资产的多版本候选 URL 列表（当前版本 → 权重分发页）"""
    return [f'https://github.com/dxhklck/subzero/releases/download/{v}/{name}'
            for v in _GH_RELEASES]


def _gh_one(name, **extra):
    """同名 Release 资产的多版本候选源（当前版本 → 权重分发页 v1.0.0）"""
    return [{'url': f'https://github.com/dxhklck/subzero/releases/download/{v}/{name}', **extra}
            for v in _GH_RELEASES]


WEIGHT_MANIFEST = [
    # 字幕检测（PP-OCRv5 server / fast）
    {'path': 'V5/ch_det/inference.pdiparams', 'min_mb': 80,
     'sha256': '183146fe9d9910352f68482f623bcbbb9fa7b9e8fa1463b9ad288cef00524d2d',
     'sources': [{'url': MS_BASE + 'models/V5/ch_det/inference.pdiparams'},
                 *_gh_one('V5_ch_det_inference.pdiparams')]},
    {'path': 'V5/ch_det_fast/inference.pdiparams', 'min_mb': 4,
     'sha256': 'afa1820cb16c1fd0dad589d0f8b389139061c1ef6d68019685fd07be997dda5b',
     'sources': [{'url': MS_BASE + 'models/V5/ch_det_fast/inference.pdiparams'},
                 *_gh_one('V5_ch_det_fast_inference.pdiparams')]},
    # 修复算法权重
    {'path': 'big-lama/big-lama.pt', 'min_mb': 190,
     'sha256': '7ba7aa7ac37a4d41fdbbeba3a2af7ead18058552997e3a3cd1a3b2210c9e6b4c',
     'sources': [{'url': MS_BASE + 'models/big-lama/big-lama.pt'},
                 *_gh_one('big-lama.pt')]},
    {'path': 'sttn-auto/infer_model.pth', 'min_mb': 60,
     'sha256': 'c8408c9dd1300bd7000ea3a81b8236db89d9c0f185837781e38283c1e464675b',
     'sources': [{'url': MS_BASE + 'models/sttn-auto/infer_model.pth'},
                 *_gh_one('sttn-auto_infer_model.pth')]},
    {'path': 'sttn-det/sttn.pth', 'min_mb': 60,
     'sha256': '25b0c2c30042d82efd1893bd42ec726764262d94115393a1718f8d65d2a7817b',
     'sources': [{'url': MS_BASE + 'models/sttn-det/sttn.pth'},
                 *_gh_one('sttn-det_sttn.pth')]},
    {'path': 'minimax-remover/transformer/diffusion_pytorch_model.safetensors', 'min_mb': 2100,
     'sha256': 'a379d98432970f614befb260357153edcd01a99748cf7f6dabe1a230c159b213',
     'sources': [{'url': MS_BASE + 'models/minimax-remover/transformer/diffusion_pytorch_model.safetensors'},
                 *_gh_one('minimax_transformer.safetensors.part1',
                          parts=['minimax_transformer.safetensors.part1',
                                 'minimax_transformer.safetensors.part2'])]},
    {'path': 'minimax-remover/transformer/config.json', 'min_mb': 0,
     'sha256': '7d218c1d52a04e9e4f0e89ca72c9743daa408177e3bdccde90de1334801f8f77',
     'sources': [{'url': MS_BASE + 'models/minimax-remover/configs/transformer_config.json'},
                 *_gh_one('minimax_configs.zip', zip_member='transformer_config.json')]},
    {'path': 'minimax-remover/vae/diffusion_pytorch_model.safetensors', 'min_mb': 480,
     'sha256': 'd6e524b3fffede1787a74e81b30976dce5400c4439ba64222168e607ed19e793',
     'sources': [{'url': MS_BASE + 'models/minimax-remover/vae/diffusion_pytorch_model.safetensors'},
                 *_gh_one('minimax_vae_model.safetensors')]},
    {'path': 'minimax-remover/vae/config.json', 'min_mb': 0,
     'sha256': 'f0c1cc1d7decb5badc384f54691746a27a9aeff49f7ebca974e583389342d527',
     'sources': [{'url': MS_BASE + 'models/minimax-remover/configs/vae_config.json'},
                 *_gh_one('minimax_configs.zip', zip_member='vae_config.json')]},
    {'path': 'minimax-remover/scheduler/scheduler_config.json', 'min_mb': 0,
     'sha256': '3fed2abbd9bbc301a74db01947198057ec5049808910dccab320925bf27bea6e',
     'sources': [{'url': MS_BASE + 'models/minimax-remover/configs/scheduler_config.json'},
                 *_gh_one('minimax_configs.zip', zip_member='scheduler_config.json')]},
    # ffmpeg（Windows）：zip 下载后解压 ffmpeg.exe 到位
    {'path': 'backend/ffmpeg/win_x64/ffmpeg.exe', 'min_mb': 100, 'base': 'project',
     'sha256': None,
     'sources': [{'url': MS_BASE + 'ffmpeg/win_x64/ffmpeg-win_x64.zip', 'zip_member': 'ffmpeg.exe'},
                 *_gh_one('ffmpeg-win_x64.zip', zip_member='ffmpeg.exe')]},
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
    """逐项核对权重文件存在且体积达标，返回 (结果, 缺失可下载列表)"""
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


def _finalize_entry(entry, dest, log):
    """分卷拼接 / zip 解压等落地处理"""
    import zipfile
    if entry.get('parts'):
        # 分卷拼接：各 part 已按 <dest>.partN 存放
        final_size = 0
        with open(dest, 'wb') as out:
            for i, name in enumerate(entry['parts'], 1):
                part = dest + f'.part{i}'
                with open(part, 'rb') as f:
                    while True:
                        chunk = f.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                final_size += os.path.getsize(part)
                os.remove(part)
        log(f'  分卷 {len(entry["parts"])} 个已拼接，共 {final_size / 1024 / 1024:.0f}MB')
    if entry.get('zip_member'):
        with zipfile.ZipFile(dest + '.zipdl') as z:
            member = entry['zip_member']
            with z.open(member) as src, open(dest, 'wb') as out:
                while True:
                    chunk = src.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        os.remove(dest + '.zipdl')


def _sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def fix_weights(missing, progress_cb=None, log=print):
    """逐源尝试下载缺失权重（魔搭→GitHub），SHA256 校验不过自动换源"""
    fixed, manual = [], []
    for m in missing:
        sources = m.get('sources') or ([{'url': m['url']}] if m.get('url') else [])
        if not sources:
            manual.append(m['path'])
            log(f'⚠ {m["path"]} 无自动下载源，请按 README 手动获取')
            continue
        dest = _entry_abs_path(m)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        done = False
        for i, src in enumerate(sources, 1):
            try:
                log(f'⬇ [{i}/{len(sources)}] 下载 {m["path"]} <- {src["url"][:60]}...')
                if src.get('parts'):
                    # 分卷：逐卷下载为 <dest>.partN（支持断点续传），全部完成后拼接
                    for j, name in enumerate(src['parts'], 1):
                        part = dest + f'.part{j}'
                        if os.path.isfile(part) and os.path.getsize(part) > 0:
                            log(f'  part{j} 已存在，跳过')
                            continue
                        download_file(src['url'].replace(src['parts'][0], name), part, progress_cb)
                elif src.get('zip_member'):
                    download_file(src['url'], dest + '.zipdl', progress_cb)
                else:
                    download_file(src['url'], dest, progress_cb)
                _finalize_entry(src, dest, log)
                # 完整性校验：不过则删掉换下一个源
                if m.get('sha256'):
                    actual = _sha256_of(dest)
                    if actual != m['sha256']:
                        log(f'⚠ {m["path"]} SHA256 不符（{actual[:12]}...），丢弃换源')
                        os.remove(dest)
                        continue
                log(f'✅ {m["path"]} 完成')
                fixed.append(m['path'])
                done = True
                break
            except Exception as e:
                log(f'❌ 源{i}失败: {e}（已保留 .part 可续传）')
        if not done:
            log(f'❌ {m["path"]} 所有源均失败')
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
        print(f'\n提示: 运行 --fix-weights 可自动下载 {sum(1 for m in missing if m.get("sources"))} 项缺失权重')
    # 汇总退出码：0 全过 / 1 有缺失
    sys.exit(0 if all(r['ok'] for r in results) else 1)


if __name__ == '__main__':
    main()
