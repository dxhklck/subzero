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

    # 显存估算模型（纯激活，不含约4G模型权重）。实测标定（RTX 4070 Ti SUPER 16G、
    # fp32 VAE 解码开、reset峰值统计在模型加载后）：
    #   0.30MP 条带 25帧/61帧 → 8.97/8.98GB（帧数几乎不影响峰值！）
    #   0.52MP 全帧 25帧 → 13.46GB；0.92MP 全帧 25帧 → 21.56GB（溢出走共享内存）
    # 结论：峰值 ≈ MP×(30 + 0.005×帧)——由条带**分辨率**主导，块大小是速度参数而非显存参数
    # （A/B 实测：0.30MP 条带固定61帧比守卫缩到25帧快13%，峰值仅多0.3GB）
    _VRAM_GB_PER_MP = 30.0
    _VRAM_GB_PER_FRAME_MP = 0.005

    def __init__(self, device: torch.device,
                 model_dir: str,
                 max_load_num: int = 81,
                 num_inference_steps: int = 12,
                 iterations: int = 6,
                 process_width: int = 0,
                 block_overlap: int = 8,
                 vae_decode_fp32: bool = True):
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

    def _align_16(self, value: int) -> int:
        return max(16, int(round(value / 16)) * 16)

    def _fit_chunk_to_vram(self, proc_h: int, proc_w: int, chunk: int) -> int:
        """按条带实际推理分辨率与剩余显存校验块大小（只减不增，保持 4k+1）。

        实测标定（见类属性 _VRAM_GB_PER_MP）：显存峰值由条带**分辨率**主导、与块大小几乎无关
        （0.30MP 下 25 帧与 61 帧峰值仅差 0.01GB）。因此本守卫在典型字幕条带（≤0.32MP）
        下不收缩配置值——A/B 实测收缩反而慢 13%；仅在条带分辨率本身超出显存预算时介入：
        边际项超限则收缩帧数，固定项（分辨率）超限则砍帧无益，改发警告提示降处理分辨率。
        """
        if not (torch.cuda.is_available() and str(self.device).startswith('cuda')):
            return chunk
        try:
            free_b, _ = torch.cuda.mem_get_info()
        except Exception:
            return chunk
        free_gb = free_b / 1024 ** 3
        mp = (proc_h * proc_w) / 1e6
        if mp <= 0:
            return chunk
        # free 已扣除模型权重（约4G）；预算取 85%，其余留给驱动/碎片/桌面合成器
        budget = free_gb * 0.85
        pred = mp * (self._VRAM_GB_PER_MP + self._VRAM_GB_PER_FRAME_MP * chunk)
        if pred <= budget:
            print(f'[MiniMax] 条带推理 {proc_w}x{proc_h} ({mp:.2f}MP) x {chunk} 帧/块'
                  f'（预计激活 {pred:.1f}G / 预算 {budget:.1f}G，剩余显存 {free_gb:.1f}G）')
            return chunk
        if self._VRAM_GB_PER_MP * mp >= budget:
            # 固定项已超预算：收缩帧数无益（峰值由分辨率决定），提示降处理分辨率
            print(f'[MiniMax] 警告：条带 {proc_w}x{proc_h} ({mp:.2f}MP) 预计激活 '
                  f'{self._VRAM_GB_PER_MP * mp:.1f}G 超出预算 {budget:.1f}G，'
                  f'块大小已无法改善——建议调小 MiniMax 处理分辨率上限（ProcessWidth）或换更轻的算法')
            return chunk
        # 边际项超限：解出预算内的最大帧数（向下取整到 4k+1）
        max_frames = (budget / mp - self._VRAM_GB_PER_MP) / self._VRAM_GB_PER_FRAME_MP
        safe_chunk = max(5, ((max(int(max_frames), 1) - 1) // 4) * 4 + 1)
        fitted = min(chunk, safe_chunk)
        print(f'[MiniMax] 条带推理 {proc_w}x{proc_h} ({mp:.2f}MP) x {fitted} 帧/块'
              f'（显存守卫 {chunk} -> {fitted}，剩余 {free_gb:.1f}G）')
        return fitted

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

        # [0,1] float → uint8 RGB → 原分辨率 → BGR（放大用 Lanczos：细节锐度优于双三次）
        out = []
        for i in range(num):
            frame = (np.clip(result[i], 0, 1) * 255).astype(np.uint8)
            frame = cv2.resize(frame, (frames_bgr[0].shape[1], frames_bgr[0].shape[0]),
                               interpolation=cv2.INTER_LANCZOS4)
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

        # 显存守卫：按实际条带推理分辨率与剩余显存收敛块大小（见 _fit_chunk_to_vram）
        chunk = self._fit_chunk_to_vram(proc_h, proc_w, self.chunk_frames)
        # 块间重叠：视频按 chunk 帧一块独立推理，块边界处潜空间不连续（VAE 时间压缩4，
        # 接缝约4帧）可能出现闪烁。相邻块重叠 overlap 帧，重叠区用线性加权交叉混合消除接缝。
        # overlap 上限取 chunk//4，保证推理开销不超过约1.3倍；0 = 关闭
        overlap = min(self.block_overlap, chunk // 4)
        step = chunk - overlap

        n = len(input_frames)
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
