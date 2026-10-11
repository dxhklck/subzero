import numpy as np


class StripCropInpaint:
    """
    条带裁剪修复包装器：把「全帧 mask 修复」缩小为「mask 条带修复」。

    背景：MiniMax 链路的 mask 是每时间段一张静态实心矩形（区间内检测框并集外扩），
    全帧直推时大量算力花在 mask 之外的像素上。本包装器据 mask bbox 计算一个
    固定裁剪窗口：向四周外扩 padding 像素（clamp 到帧边界，高度不足补到最小值），
    整段帧序列统一裁剪该窗口 → 内层模型只修复条带 → 逐帧贴回原位。

    - 窗口在时间段内固定 → 无逐帧抖动；段间窗口随 mask 变化，与现有 mask 行为一致
    - 窗口外像素原样保留，逐像素无损
    - mask 为空（检测全被过滤）时透传内层模型，保持既有行为
    """

    # 条带最小高度：过小会超出视频修复模型的舒适尺度
    MIN_HEIGHT = 160

    def __init__(self, inner_model, padding: int = 128):
        self.inner = inner_model
        self.padding = max(0, int(padding))

    def _window(self, mask: np.ndarray):
        """由 mask 求 (y0, y1, x0, x1) 裁剪窗口；mask 全空返回 None"""
        ys, xs = np.nonzero(mask)
        if len(ys) == 0:
            return None
        H, W = mask.shape[:2]
        p = self.padding
        y0 = max(0, int(ys.min()) - p)
        y1 = min(H, int(ys.max()) + 1 + p)
        x0 = max(0, int(xs.min()) - p)
        x1 = min(W, int(xs.max()) + 1 + p)
        # 高度补到最小值（围绕条带中心扩展，clamp 后保持高度）
        if y1 - y0 < self.MIN_HEIGHT:
            center = (y0 + y1) // 2
            y0 = max(0, center - self.MIN_HEIGHT // 2)
            y1 = min(H, y0 + self.MIN_HEIGHT)
            y0 = max(0, y1 - self.MIN_HEIGHT)
        return y0, y1, x0, x1

    def __call__(self, input_frames, input_mask):
        mask = np.asarray(input_mask)
        if mask.ndim == 3:
            mask = mask[:, :, 0]
        mask = (mask > 127).astype(np.uint8) * 255

        win = self._window(mask)
        if win is None:
            return self.inner(input_frames, mask)
        y0, y1, x0, x1 = win
        H, W = mask.shape[:2]
        # 窗口已覆盖全帧时直接透传，省去无谓拷贝
        if y0 == 0 and y1 == H and x0 == 0 and x1 == W:
            return self.inner(input_frames, mask)

        crop_mask = mask[y0:y1, x0:x1]
        crop_frames = [f[y0:y1, x0:x1] for f in input_frames]
        crop_out = self.inner(crop_frames, crop_mask)

        out = []
        for src, rep in zip(input_frames, crop_out):
            frame = src.copy()
            frame[y0:y1, x0:x1] = rep
            out.append(frame)
        return out
