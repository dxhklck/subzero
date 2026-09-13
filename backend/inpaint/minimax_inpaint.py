import gc
import os
import sys
from typing import List, Tuple

import cv2
import numpy as np
import torch
from diffusers.models import AutoencoderKLWan
from diffusers.schedulers import UniPCMultistepScheduler

# 官方推理脚本（Apache-2.0）原样分发在本子目录，作为包路径引入
_OFFICIAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'minimax_official')
if _OFFICIAL_DIR not in sys.path:
    sys.path.insert(0, _OFFICIAL_DIR)

from transformer_minimax_remover import Transformer3DModel  # noqa: E402
from pipeline_minimax_remover import Minimax_Remover_Pipeline  # noqa: E402


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
                 process_width: int = 0):
        self.device = device
        self.num_inference_steps = num_inference_steps
        self.iterations = iterations
        # 处理分辨率上限（宽度像素，0 = 按原分辨率）。视频更宽时先等比降采样推理、输出缩回原分辨率
        self.process_width = int(process_width) if process_width else 0
        # 块大小向下取整到 4k+1（VAE 时间维压缩因子为 4）
        self.chunk_frames = max(5, ((int(max_load_num) - 1) // 4) * 4 + 1)

        vae = AutoencoderKLWan.from_pretrained(os.path.join(model_dir, 'vae'), torch_dtype=torch.float16)
        transformer = Transformer3DModel.from_pretrained(os.path.join(model_dir, 'transformer'),
                                                         torch_dtype=torch.float16)
        scheduler = UniPCMultistepScheduler.from_pretrained(os.path.join(model_dir, 'scheduler'))
        self.pipe = Minimax_Remover_Pipeline(transformer=transformer, vae=vae, scheduler=scheduler)
        self.pipe.to(device)

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

        H_ori, W_ori = input_frames[0].shape[:2]
        proc_h, proc_w = self._proc_size(H_ori, W_ori)

        inpainted_frames = []
        n = len(input_frames)
        chunk = self.chunk_frames
        for start in range(0, n, chunk):
            part = input_frames[start:min(start + chunk, n)]
            # 尾块补帧到 4k+1（复制末帧），推理后丢弃补帧
            need = ((len(part) - 1) // 4 + 1) * 4 + 1
            if need > len(part):
                part = part + [part[-1]] * (need - len(part))
            out = self._inpaint_chunk(part, mask_gray, proc_h, proc_w)[:min(n - start, chunk)]
            inpainted_frames.extend(out)
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return inpainted_frames
