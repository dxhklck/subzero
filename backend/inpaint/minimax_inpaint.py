import gc
import os
import sys
from typing import List, Tuple

import cv2
import numpy as np
import torch
import torch._dynamo  # noqa: F401  （torch.compile 的 suppress_errors 配置；模块顶层导入避免函数内 import 遮蔽 torch 名）
from diffusers.models import AutoencoderKLWan
from diffusers.schedulers import UniPCMultistepScheduler

# 官方推理脚本（Apache-2.0）原样分发在本子目录，作为包路径引入
_OFFICIAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'minimax_official')
if _OFFICIAL_DIR not in sys.path:
    sys.path.insert(0, _OFFICIAL_DIR)

from transformer_minimax_remover import Transformer3DModel  # noqa: E402
from pipeline_minimax_remover import Minimax_Remover_Pipeline  # noqa: E402


def _triton_available() -> bool:
    """GPU inductor 依赖 Triton 生成 kernel，而 Triton 不支持 Windows（本机实测缺失）。
    不可用时 torch.compile 会静默回退 eager——白费首次编译开销且零增益，故先探测再决定。"""
    try:
        import triton  # noqa: F401
        return True
    except ImportError:
        return False


class MiniMaxInpaint:
    """
    MiniMax-Remover（Wan2.1-1.3B DiT）视频修复，封装官方 diffusers 管线。
    - 无 CFG、无文本编码器，12 步推理
    - 模型权重：backend/models/minimax-remover/{transformer, vae, scheduler}
    - 注意：权重 CC-BY-NC-4.0，仅限非商用
    输入输出约定与 LamaInpaint 一致：
        __call__(input_frames: List[np.ndarray BGR], input_mask: np.ndarray (H,W) 0/255)
        -> List[np.ndarray BGR]
    """

    def __init__(self, device: torch.device,
                 model_dir: str,
                 max_load_num: int = 81,
                 num_inference_steps: int = 12,
                 iterations: int = 6,
                 process_width: int = 0,
                 block_overlap: int = 8,
                 vae_decode_fp32: bool = True,
                 torch_compile: bool = True):
        self.device = device
        self.num_inference_steps = num_inference_steps
        self.iterations = iterations
        # 块间重叠帧数（0=关闭）：见 __call__ 中的接缝混合逻辑
        self.block_overlap = max(0, int(block_overlap))
        # 处理分辨率上限（宽度像素，0 = 按原分辨率）。视频更宽时先等比降采样推理、输出缩回原分辨率
        self.process_width = int(process_width) if process_width else 0
        # 块大小向下取整到 4k+1（VAE 时间维压缩因子为 4）
        self.chunk_frames = max(5, ((int(max_load_num) - 1) // 4) * 4 + 1)

        vae = AutoencoderKLWan.from_pretrained(os.path.join(model_dir, 'vae'), torch_dtype=torch.float16)
        # fp32 解码 VAE（可选）：编码仍走 fp16 省显存，解码升 fp32 消除平坦背景色带
        vae_decode = None
        if vae_decode_fp32:
            vae_decode = AutoencoderKLWan.from_pretrained(os.path.join(model_dir, 'vae'), torch_dtype=torch.float32)
        transformer = Transformer3DModel.from_pretrained(os.path.join(model_dir, 'transformer'),
                                                         torch_dtype=torch.float16)
        scheduler = UniPCMultistepScheduler.from_pretrained(os.path.join(model_dir, 'scheduler'))
        self.pipe = Minimax_Remover_Pipeline(transformer=transformer, vae=vae, scheduler=scheduler,
                                             vae_decode=vae_decode)
        self.pipe.to(device)
        if vae_decode is not None:
            vae_decode.to(device)
        # torch.compile：编译 transformer 融合算子、减少每步去噪的 kernel launch 开销（长视频净赚 10-30%）。
        # ⚠ 依赖 Triton 生成 GPU kernel，而 Triton 不支持 Windows——本机实测（2026-10-13）回退 eager、
        #   零增益还白费 63% 冷启动开销，故默认关闭；Linux 环境自动生效。
        # 首次每种输入形状编译 1-3 分钟（有跨进程缓存）；suppress_errors 保证编译失败自动回退，不改变输出
        self.torch_compiled = False
        if torch_compile and _triton_available():
            try:
                torch._dynamo.config.suppress_errors = True
                self.pipe.transformer = torch.compile(self.pipe.transformer)
                self.torch_compiled = True
            except Exception as e:
                print(f'[MiniMax] torch.compile 不可用，回退 eager 模式: {e}')
        elif torch_compile:
            print('[MiniMax] 未检测到 Triton（Windows 不支持），跳过 torch.compile，使用 eager 模式')

    def _align_16(self, value: int) -> int:
        return max(16, int(round(value / 16)) * 16)

    def _proc_size(self, h: int, w: int) -> Tuple[int, int]:
        """推理分辨率：默认按原分辨率 16 对齐；设置了 process_width 且视频更宽时按宽度等比降采样（对齐 16）"""
        if self.process_width and w > self.process_width:
            scale = self.process_width / w
            return self._align_16(int(round(h * scale))), self._align_16(self.process_width)
        return self._align_16(h), self._align_16(w)

    @torch.no_grad()
    def _inpaint_chunk(self, frames_bgr: List[np.ndarray], mask_gray: np.ndarray,
                       proc_h: int, proc_w: int) -> List[np.ndarray]:
        """推理一个块（≤4k+1 帧），返回该块全部帧（原分辨率 BGR uint8）"""
        num = len(frames_bgr)

        # 帧与掩膜 resize 到 16 倍数处理分辨率（官方管线要求）
        resized = [cv2.resize(f, (proc_w, proc_h), interpolation=cv2.INTER_AREA) for f in frames_bgr]
        resized = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in resized]
        mask_resized = cv2.resize(mask_gray, (proc_w, proc_h), interpolation=cv2.INTER_NEAREST)

        images = torch.from_numpy(np.stack(resized)).float() / 127.5 - 1.0      # [f,h,w,c] [-1,1]
        m = torch.from_numpy(mask_resized.astype(np.float32) / 255.0)           # [h,w] [0,1]
        masks = m[None, :, :, None].repeat(num, 1, 1, 1)                        # [f,h,w,1]

        result = self.pipe(
            images=images,
            masks=masks,
            num_frames=num,
            height=proc_h,
            width=proc_w,
            num_inference_steps=self.num_inference_steps,
            generator=torch.Generator(device=self.device).manual_seed(42),
            iterations=self.iterations,
        ).frames[0]

        # [0,1] float → uint8 RGB → 原分辨率 → BGR
        out = []
        for i in range(num):
            frame = (np.clip(result[i], 0, 1) * 255).astype(np.uint8)
            frame = cv2.resize(frame, (frames_bgr[0].shape[1], frames_bgr[0].shape[0]),
                               interpolation=cv2.INTER_CUBIC)
            out.append(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        return out

    def __call__(self, input_frames: List[np.ndarray], input_mask: np.ndarray) -> List[np.ndarray]:
        """
        :param input_frames: 原视频帧（BGR uint8）
        :param input_mask: 字幕区域mask（H,W，0/255）
        :return: 修复后的帧列表（BGR uint8）
        """
        mask = np.asarray(input_mask)
        if mask.ndim == 3:
            mask = mask[:, :, 0]
        mask_gray = (mask > 127).astype(np.uint8) * 255
        # 闭运算（先膨胀后腐蚀，不扩大mask）：填充相邻字幕框之间≤2px的细小缝隙。
        # 缝隙若漏掩膜，DiT 不会修复那里的原图像素，残留为可见细线
        if mask_gray.any():
            mask_gray = cv2.morphologyEx(mask_gray, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

        H_ori, W_ori = input_frames[0].shape[:2]
        proc_h, proc_w = self._proc_size(H_ori, W_ori)

        n = len(input_frames)
        chunk = self.chunk_frames
        # 块间重叠：视频按 chunk 帧一块独立推理，块边界处潜空间不连续（VAE 时间压缩4，
        # 接缝约4帧）可能出现闪烁。相邻块重叠 overlap 帧，重叠区用线性加权交叉混合消除接缝。
        # overlap 上限取 chunk//4，保证推理开销不超过约1.3倍；0 = 关闭
        overlap = min(self.block_overlap, chunk // 4)
        step = chunk - overlap

        results = [None] * n
        start = 0
        while start < n:
            end = min(start + chunk, n)
            part = input_frames[start:end]
            # 尾块补帧到 4k+1（复制末帧），推理后丢弃补帧
            need = ((len(part) - 1) // 4 + 1) * 4 + 1
            if need > len(part):
                part = part + [part[-1]] * (need - len(part))
            out = self._inpaint_chunk(part, mask_gray, proc_h, proc_w)
            for j in range(end - start):
                gidx = start + j
                if results[gidx] is None:
                    results[gidx] = out[j]
                else:
                    # 重叠区：上一块尾帧与本块头帧按位置线性交叉混合（权重从1/(O+1)到O/(O+1)）
                    w = (j + 1) / (overlap + 1)
                    results[gidx] = (results[gidx].astype(np.float32) * (1 - w)
                                     + out[j].astype(np.float32) * w).astype(np.uint8)
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            start += step

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return results
