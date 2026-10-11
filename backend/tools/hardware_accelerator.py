import traceback
import importlib.util
import re

import torch

from backend.config import tr

class HardwareAccelerator:

    # 类变量，用于存储单例实例
    _instance = None

    @classmethod
    def instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = HardwareAccelerator()
            cls._instance.initialize()
        return cls._instance

    def __init__(self):
        self.__cuda = False
        self.__dml = False
        self.__mps = False
        self.__onnx_providers = []
        self.__enabled = True
        self.__device = None
        # None 表示尚未探测（paddle 导入较慢，改为惰性探测）
        self.__paddle_cuda = None
        # paddle GPU 不可用时的原因摘要（仅当本机有 N 卡但 paddle 走不了 GPU 时有值）
        self.__paddle_cuda_reason = None

    def initialize(self):
        self.check_directml_available()
        self.check_cuda_available()
        self.check_mps_available()
        self.load_onnx_providers()

    def check_directml_available(self):
        self.__dml = importlib.util.find_spec("torch_directml")

    def check_cuda_available(self):
        self.__cuda = torch.cuda.is_available()

    def check_mps_available(self):
        self.__mps = torch.backends.mps.is_available() and torch.backends.mps.is_built()

    def load_onnx_providers(self):
        try:
            import onnxruntime as ort
            available_providers = ort.get_available_providers()
            for provider in available_providers:
                if provider in [
                    "CPUExecutionProvider"
                ]:
                    continue
                if provider not in [
                    "DmlExecutionProvider",         # DirectML，适用于 Windows GPU
                    "ROCMExecutionProvider",        # AMD ROCm
                    "MIGraphXExecutionProvider",    # AMD MIGraphX
                    "VitisAIExecutionProvider",     # AMD VitisAI，适用于 RyzenAI & Windows, 实测和DirectML性能似乎差不多
                    "OpenVINOExecutionProvider",    # Intel GPU
                    "MetalExecutionProvider",       # Apple macOS
                    "CoreMLExecutionProvider",      # Apple macOS
                    "CUDAExecutionProvider",        # Nvidia GPU
                ]:
                    print(tr['Main']['OnnxExectionProviderNotSupportedSkipped'].format(provider))
                    continue
                print(tr['Main']['OnnxExecutionProviderDetected'].format(provider))
                self.__onnx_providers.append(provider)
        except ModuleNotFoundError as e:
            print(tr['Main']['OnnxRuntimeNotInstall'])

    def has_accelerator(self):
        if not self.__enabled:
            return False
        return self.__cuda or self.__dml or self.__mps or len(self.__onnx_providers) > 0

    @property
    def accelerator_name(self):
        if not self.__enabled:
            return "CPU"
        if self.__dml:
            return "DirectML"
        if self.__cuda:
            return "GPU"
        if self.__mps:
            return "MPS"
        elif len(self.__onnx_providers) > 0:
            return ", ".join(self.__onnx_providers)
        else:
            return "CPU"

    @property
    def onnx_providers(self):
        if not self.__enabled:
            return ["CPUExecutionProvider"]
        return self.__onnx_providers

    def has_cuda(self):
        if not self.__enabled:
            return False
        return self.__cuda
    
    def has_mps(self):
        if not self.__enabled:
            return False
        return self.__mps

    def has_paddle_cuda(self):
        """
        PaddlePaddle 本体能否使用 CUDA（即安装的是 paddlepaddle-gpu 且能枚举到显卡）。
        字幕检测用的是 PaddleOCR（PP-OCR），它的设备参数与 torch 无关：
        若装的是 CPU 版 paddlepaddle，传入 gpu 设备会直接抛错，因此这里必须单独探测。
        惰性探测并缓存结果，避免在只用 sttn-auto（不需要检测）时白白导入 paddle。

        注意：paddle 能否真正跑 GPU 取决于两点——
        1. 「编译它的 CUDA 版本」是否覆盖本机显卡架构：Blackwell（RTX 50 系，sm_100/sm_120）
           需要用 CUDA >= 12.9 编译的 paddlepaddle-gpu；低版本 wheel 里没有对应内核，
           推理时抛 cudaErrorNoKernelImageForDevice(209)，表现为「GPU 检测不到字幕、CPU 却正常」。
        2. 「显卡驱动」是否足够新：paddle 按 CUDA 12.9 编译时需要较新驱动，
           驱动过旧会报驱动 API / context 相关错误。
        因此这里在静态检查之外再做一次真实 GPU 小运算冒烟测试（结果强制同步），
        一次性暴露以上所有问题；失败则回退 CPU 检测，视频修复算法走 torch，不受影响。
        失败原因记录在 paddle_cuda_fallback_reason，供日志窗口提示用户。
        """
        if not self.__enabled:
            return False
        if self.__paddle_cuda is None:
            try:
                import paddle
                if not (bool(paddle.is_compiled_with_cuda()) and paddle.device.cuda.device_count() > 0):
                    self.__paddle_cuda = False
                    self.__paddle_cuda_reason = 'paddlepaddle-gpu is not installed (CPU build only)'
                elif not self.__paddle_covers_arch(paddle):
                    self.__paddle_cuda = False
                    cap = torch.cuda.get_device_capability(0)
                    self.__paddle_cuda_reason = (
                        'GPU architecture sm_{}{}/sm_{}{} not covered by paddle compiled with CUDA {}'
                        ' (Blackwell needs CUDA >= 12.9 build)'.format(
                            cap[0], cap[1], cap[0], cap[1], self.get_paddle_compile_cuda()))
                else:
                    ok, err = self.__paddle_gpu_smoke_test(paddle)
                    self.__paddle_cuda = ok
                    if not ok:
                        self.__paddle_cuda_reason = err
            except Exception as e:
                self.__paddle_cuda = False
                self.__paddle_cuda_reason = str(e)
        return self.__paddle_cuda

    @property
    def paddle_cuda_fallback_reason(self):
        """本机有 N 卡但 paddle 走不了 GPU 时的原因摘要；paddle GPU 可用或无 N 卡时返回 None"""
        if self.__enabled and self.has_cuda() and not self.has_paddle_cuda():
            return self.__paddle_cuda_reason
        return None

    @staticmethod
    def __paddle_gpu_smoke_test(paddle_mod):
        """
        真实跑一次 GPU 小运算并强制同步取结果，验证驱动/内核确实可用。
        返回 (True, None) 或 (False, 错误摘要)。比静态版本比对更权威：
        驱动过旧、内核缺架构、CUDA DLL 冲突等问题都会在这里暴露。
        """
        try:
            paddle_mod.set_device('gpu:0')
            x = paddle_mod.to_tensor([[1.0, 2.0], [3.0, 4.0]])
            y = paddle_mod.matmul(x, x)
            float(y.sum())  # 强制同步，暴露异步 CUDA 错误（如 no kernel image for device）
            return True, None
        except Exception as e:
            msg = str(e).strip()
            return False, (msg[:300] if msg else repr(e))

    def get_nvidia_driver_info(self):
        """通过 nvidia-smi 获取 (驱动版本, 驱动支持的 CUDA 版本)，拿不到返回 (None, None)
        兼容新旧两种输出格式：
          旧版: | Driver Version: 576.02  CUDA Version: 12.9 |
          新版: | NVIDIA-SMI 610.88  KMD Version: 610.88  CUDA UMD Version: 13.3 |
        """
        try:
            import subprocess
            result = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=10)
            out = result.stdout
            drv = re.search(r'Driver Version:\s*([\d.]+)', out) or re.search(r'NVIDIA-SMI\s+([\d.]+)', out)
            cud = re.search(r'(?:CUDA UMD\s+|CUDA\s+)Version:\s*([\d.]+)', out)
            return (drv.group(1) if drv else None, cud.group(1) if cud else None)
        except Exception:
            return (None, None)

    def get_paddle_compile_cuda(self):
        """获取 paddle 编译所用的 CUDA 版本字符串（如 '12.9'），拿不到返回 None"""
        try:
            import paddle
            return str(paddle.version.cuda())
        except Exception:
            return None

    @staticmethod
    def __paddle_covers_arch(paddle_mod):
        """当前 paddle 编译所用的 CUDA 版本是否覆盖本机显卡架构。拿不到信息时按可用处理。"""
        try:
            import torch
            cap = torch.cuda.get_device_capability(0)
        except Exception:
            return True
        sm = cap[0] * 10 + cap[1]
        if sm < 100:  # 非 Blackwell，现有 wheel 均已覆盖
            return True
        try:
            ver = str(paddle_mod.version.cuda()).replace('cu', '')
            nums = [int(x) for x in ver.split('.') if x.isdigit()]
            return tuple(nums[:2]) >= (12, 9)
        except Exception:
            return True

    def set_enabled(self, enable):
        self.__enabled = enable

    def get_available_vram_mb(self):
        """获取可用 GPU 显存（MB），无 GPU 返回 0"""
        if not self.__enabled:
            return 0
        if self.__cuda:
            try:
                free_vram = torch.cuda.mem_get_info()[0]  # (free, total)
                return free_vram / (1024 * 1024)
            except Exception:
                return 0
        if self.__mps:
            try:
                # MPS 没有直接查询接口，使用系统内存作为参考
                import subprocess
                result = subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True)
                total_mem = int(result.stdout.strip()) / (1024 * 1024)
                return total_mem * 0.5  # 保守估计可用一半
            except Exception:
                return 0
        return 0

    @property
    def device(self):
        """
        onnxruntime-directml 1.21.1-1.22.0(往上未测试) 和 torch-directml 不能同时初始化, 会相互影响
        提示site-packages/onnxruntime/capi/onnxruntime_inference_collection.py", line 266, in run
                return self._sess.run(output_names, input_feed, run_options)
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
            UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb2 in position 344: invalid start bn 344: invalid start byte
        onnxruntime-directml 1.21.1 则正常, 但Win10跑不起来, Win11正常
        为了避免冲突以及避免重写一个QPT智能部署流程, 这里采用延迟初始化的方式+继续使用onnxruntime-directml 1.20.1
        当然SubtitleDetect放到一个独立进程去操作也是可以的
        """
        if self.__enabled:
            if self.__dml:
                try:
                    import torch_directml
                    return torch_directml.device(torch_directml.default_device())
                    self.__dml = True
                except:
                    traceback.print_exc()
                    self.__dml = False
            if self.__cuda:
                return torch.device("cuda:0")
            if self.__mps:
                return torch.device("mps")
        return torch.device("cpu")