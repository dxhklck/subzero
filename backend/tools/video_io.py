import os
import queue
import subprocess
import threading

import cv2
import numpy as np

from .ffmpeg_cli import FFmpegCLI
from .common_tools import SUBPROCESS_CREATION_FLAGS


class FramePrefetcher:
    """
    后台线程预解码视频帧，使 I/O 与模型推理重叠。
    接口兼容 cv2.VideoCapture（read/release）。
    """

    def __init__(self, video_cap, buffer_size=10, max_frames=None):
        self.cap = video_cap
        #: 最多预读取的帧数（None=读到 EOF）；分段处理时设为段帧数，
        #: 读满即停，避免把下一段的帧（或哨兵等待）混进当前段
        self._max_frames = max_frames
        self._read_count = 0
        self._buffer = queue.Queue(maxsize=buffer_size)
        self._stopped = False
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        while not self._stopped:
            if self._max_frames is not None and self._read_count >= self._max_frames:
                self._buffer.put((False, None))
                break
            ret, frame = self.cap.read()
            self._read_count += 1
            self._buffer.put((ret, frame))
            if not ret:
                break

    def read(self):
        """读取下一帧，接口与 cv2.VideoCapture.read() 一致。

        读取线程中途死亡（EOF/读满后哨兵已被消费/线程内异常）时，
        空队列上的 get() 会永久阻塞——用超时轮询 + 线程存活判断兜底返回 (False, None)。
        """
        while True:
            try:
                return self._buffer.get(timeout=0.5)
            except queue.Empty:
                if not self._thread.is_alive() and self._buffer.empty():
                    return (False, None)

    def get(self, propId):
        return self.cap.get(propId)

    def stop(self):
        """停止预读取，不释放底层 video_cap。"""
        self._stopped = True
        try:
            while not self._buffer.empty():
                self._buffer.get_nowait()
        except queue.Empty:
            pass
        self._thread.join(timeout=5)

    def release(self):
        self.stop()
        self.cap.release()


class FFmpegVideoWriter:
    """
    通过 FFmpeg 管道写入帧，使用 libx264 编码。
    接口兼容 cv2.VideoWriter（write/release）。
    """

    def __init__(self, output_path, fps, size):
        w, h = size
        # fps 读不到（0/负值）时 libx264 会以 -r 0 拒绝或产出坏文件；回退 25 保底
        if not fps or fps <= 0:
            fps = 25.0
        # veryfast + crf 20：相较 fast + crf 18 编码快约一倍、文件小约25%，
        # 对字幕擦除输出（非母版）画质损失可忽略；极限画质可改回 fast/18
        cmd = [
            FFmpegCLI.instance().ffmpeg_path,
            '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{w}x{h}',
            '-pix_fmt', 'bgr24',
            '-r', str(fps),
            '-i', '-',
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-crf', '20',
            '-preset', 'veryfast',
            '-loglevel', 'error',
            output_path
        ]
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=SUBPROCESS_CREATION_FLAGS,
        )
        # 编码器秒死（参数非法/磁盘满等）时立即暴露，让上层走 cv2 回退，而不是静默产出空文件
        if self._process.poll() is not None:
            raise RuntimeError(f'ffmpeg encoder exited immediately (rc={self._process.returncode})')

    def write(self, frame):
        """写入一帧（numpy BGR 数组）。"""
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        try:
            self._process.stdin.write(frame.tobytes())
        except BrokenPipeError:
            # 管道断裂 = ffmpeg 已退出，继续写只会静默丢帧；抛出让任务显式失败
            raise RuntimeError('ffmpeg encoder died mid-write (BrokenPipeError)')

    def release(self):
        """关闭管道并等待编码完成。"""
        try:
            self._process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self._process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=5)
