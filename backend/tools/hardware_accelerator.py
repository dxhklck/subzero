import traceback
import importlib.util

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
        """
        if not self.__enabled:
            return False
        if self.__paddle_cuda is None:
            try:
                import paddle
                self.__paddle_cuda = bool(paddle.is_compiled_with_cuda()) and paddle.device.cuda.device_count() > 0
            except Exception:
                self.__paddle_cuda = False
        return self.__paddle_cuda

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