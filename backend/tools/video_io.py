import collections
import os
import queue
import subprocess
import threading

import cv2
import numpy as np

from .ffmpeg_cli import FFmpegCLI
from .video_encoder import resolve_h264_encoder, encoder_params
from .common_tools import SUBPROCESS_CREATION_FLAGS


class FFmpegFrameReader:
    """NVDEC 硬解读取器：ffmpeg -hwaccel cuda 解码输出 rawvideo bgr24 管道。

    接口兼容本项目对 cv2.VideoCapture 的使用面（read/grab/release/isOpened/
    get/set）。与软解的关键差异：
    - 帧已在管道中解码，grab() 与 read() 都必须取走一帧（grab 丢弃内容），
      否则后续帧错位——「跳帧」省不掉解码，只省掉调用方拿到帧的拷贝；
    - set(CAP_PROP_POS_FRAMES, n) 通过 -ss（解码到目标时间戳再输出）重开管道，
      下一帧即 0-based 第 n 帧；n 等于当前位置时为 no-op；
    - 元数据（帧数/尺寸/帧率）用 cv2 只解析容器头获得，不启动解码管道。
    提前 EOF（解码中途死掉）时打 stderr 摘要，避免静默截断输出。
    硬解不可用（非 NVIDIA）时首帧失败自动回退软解管道（-ss 仍在 -i 前，帧精确 seek 语义不变）。
    """

    def __init__(self, path):
        if not path:
            # 空路径防御：cv2.VideoCapture('') 会踩 cap_images.cpp 的
            # !_filename.empty() 断言（报错信息与真正原因毫无关系），
            # 这里直接抛可读异常，由调用方按「打不开」处理
            raise ValueError(f'FFmpegFrameReader: empty path: {path!r}')
        self.path = path
        self.ffmpeg = FFmpegCLI.instance().ffmpeg_path
        meta_cap = cv2.VideoCapture(path)
        self._meta = {
            cv2.CAP_PROP_FRAME_COUNT: meta_cap.get(cv2.CAP_PROP_FRAME_COUNT),
            cv2.CAP_PROP_FRAME_WIDTH: meta_cap.get(cv2.CAP_PROP_FRAME_WIDTH),
            cv2.CAP_PROP_FRAME_HEIGHT: meta_cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
            cv2.CAP_PROP_FPS: meta_cap.get(cv2.CAP_PROP_FPS),
        }
        meta_cap.release()
        self._w = int(self._meta[cv2.CAP_PROP_FRAME_WIDTH])
        self._h = int(self._meta[cv2.CAP_PROP_FRAME_HEIGHT])
        self._fps = self._meta[cv2.CAP_PROP_FPS] or 25.0
        if self._w <= 0 or self._h <= 0:
            raise ValueError(f'invalid video dimensions {self._w}x{self._h}: {path}')
        self._proc = None
        self._stderr = b''
        self._pos = 0
        # 硬解优先：首次打开走 -hwaccel cuda；若首帧即 EOF（非 NVIDIA 机器上
        # ffmpeg 初始化失败立即退出），read() 会重建软解管道回退，保持帧精确 seek
        self._hwaccel = True
        # 当前管道已成功解码的帧数：用于区分「硬解启动失败」与「真实视频截断」
        self._frames_decoded = 0
        self._open(0)

    def _open(self, start_frame, hwaccel=None):
        if hwaccel is not None:
            self._hwaccel = hwaccel
        self._stop()
        cmd = [self.ffmpeg, '-hide_banner', '-loglevel', 'error']
        if self._hwaccel:
            cmd += ['-hwaccel', 'cuda']
        if start_frame > 0:
            # -ss 在 -i 前：定位关键帧后解码丢弃至目标时间戳，输出精确从该帧开始
            cmd += ['-ss', f'{start_frame / self._fps:.6f}']
        cmd += ['-i', self.path, '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=SUBPROCESS_CREATION_FLAGS)
        self._pos = start_frame
        self._frames_decoded = 0

    def _stop(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                _, err = self._proc.communicate(timeout=5)
                if err:
                    self._stderr = err
            except Exception:
                pass
            self._proc = None

    def _frame_bytes(self):
        need = self._w * self._h * 3
        buf = bytearray()
        out = self._proc.stdout
        while len(buf) < need:
            chunk = out.read(need - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return np.frombuffer(bytes(buf), np.uint8).reshape(self._h, self._w, 3)

    def isOpened(self):
        return self._proc is not None

    def read(self):
        if self._proc is None:
            return False, None
        frame = self._frame_bytes()
        if frame is None:
            # 硬解管道首帧即失败（非 NVIDIA 机器上 -hwaccel cuda 初始化即退）：
            # 回退软解重开，而非把 (False, None) 交给上层跳过区间。
            # 软解同样 -ss 在 -i 前，帧精确 seek 语义不变，只是更慢
            if self._hwaccel and self._frames_decoded == 0:
                print(f'[Decoder] NVDEC 硬解首帧失败，回退 CPU 软解: {self.path}')
                self._open(self._pos, hwaccel=False)
                return self.read()
            self._warn_truncated()
            self._stop()
            return False, None
        self._frames_decoded += 1
        self._pos += 1
        return True, frame.copy()  # frombuffer 只读，调用方可能就地修改

    def grab(self):
        if self._proc is None:
            return False
        frame = self._frame_bytes()
        if frame is None:
            if self._hwaccel and self._frames_decoded == 0:
                print(f'[Decoder] NVDEC 硬解首帧失败，回退 CPU 软解: {self.path}')
                self._open(self._pos, hwaccel=False)
                return self.grab()
            self._warn_truncated()
            self._stop()
            return False
        self._frames_decoded += 1
        self._pos += 1
        return True

    def _warn_truncated(self):
        if self._pos < self._meta[cv2.CAP_PROP_FRAME_COUNT] and self._stderr:
            print(f'[Decoder] NVDEC 提前结束于帧 {self._pos}: '
                  f'{self._stderr[-200:].decode("utf-8", "replace")}')

    def get(self, propId):
        if propId == cv2.CAP_PROP_POS_FRAMES:
            return float(self._pos)
        return self._meta.get(propId, 0.0)

    def set(self, propId, value):
        if propId != cv2.CAP_PROP_POS_FRAMES:
            return False
        target = int(value)
        if target == self._pos:
            return True
        try:
            self._open(max(0, target))
            return True
        except Exception:
            return False

    def release(self):
        self._stop()


def nvdec_available(path, width=0):
    """用与正式运行相同的方式实测解码 4 帧判断 NVDEC 可用性（每文件缓存）。"""
    key = (path, os.path.getmtime(path) if os.path.exists(path) else 0)
    cached = nvdec_available.__dict__.setdefault('_cache', {})
    if key in cached:
        return cached[key]
    ffmpeg = FFmpegCLI.instance().ffmpeg_path
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-hwaccel', 'cuda',
           '-i', path, '-frames:v', '4', '-f', 'null', '-']
    try:
        r = subprocess.run(cmd, capture_output=True, creationflags=SUBPROCESS_CREATION_FLAGS,
                           timeout=60)
        ok = (r.returncode == 0)
    except Exception:
        ok = False
    cached[key] = ok
    return ok


def open_video_reader(path):
    """按 VideoDecoder 配置返回视频读取器（NVDEC 管道或 cv2 软解）。

    实测数据（RTX 4070 Ti SUPER）：ffmpeg 管道 NVDEC 需逐帧 BGR 转换+管道
    传输，720p H.264 约195fps、4K HEVC 约21fps，均慢于 cv2 软解（645/69fps）
    ——解码在本项目从来不是瓶颈（OCR ~40fps、修复 ~1s/帧）。
    因此 auto 选 CPU 软解；cuda 为实验选项（强制硬解，探测失败自动回退）。
    """
    from backend.config import config
    mode = getattr(config.videoDecoder.value, 'value', config.videoDecoder.value)
    if mode == 'cuda':
        if nvdec_available(path):
            try:
                return FFmpegFrameReader(path)
            except Exception as e:
                print(f'[Decoder] NVDEC 启动失败，回退 CPU 软解: {e}')
        else:
            print('[Decoder] 配置为 cuda 但本机 NVDEC 探测失败，回退 CPU 软解')
    return cv2.VideoCapture(path)


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
    通过 FFmpeg 管道写入帧，使用 H.264 编码。
    编码器由 video_encoder 按本机实测解析（硬件优先：NVENC→QSV→AMF，
    纯 CPU 机器保底 libx264 veryfast，与旧行为一致），探测结果进程内缓存，
    保证同一次运行的所有 writer（主流程/残留二遍/分段）编码器一致
    （分段 concat 的 -c copy 以此为前提）。
    接口兼容 cv2.VideoWriter（write/release）。

    生命周期契约：writer 可能被跨分段共享（BoxTime 分段顺序写同一通道），
    由所有者（SubtitleRemover.run）在全部段写完后统一 release；写前体检
    stdin 关闭/编码器退出，把「write to closed file」式的静默故障变成
    带 ffmpeg stderr 的可定位报错。
    """

    def __init__(self, output_path, fps, size, encoder_override=None, bitrate_kbps=0):
        w, h = size
        # fps 读不到（0/负值）时编码器会以 -r 0 拒绝或产出坏文件；回退 25 保底
        if not fps or fps <= 0:
            fps = 25.0
        encoder, enc_params = resolve_h264_encoder(encoder_override)
        self.encoder = encoder  # 实际使用的编码器（上层日志/排查用）
        # 码率：0 = 质量档（探测参数一致）；>0 = VBR 目标码率 kbps（体积可预期）
        enc_params = encoder_params(encoder, bitrate_kbps)
        cmd = [
            FFmpegCLI.instance().ffmpeg_path,
            '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{w}x{h}',
            '-pix_fmt', 'bgr24',
            '-r', str(fps),
            '-i', '-',
            '-c:v', encoder,
            *enc_params,
            # 关键帧间隔 2s：编码器默认 GOP 很长（NVENC 250 帧 ≈8.3s@30fps），
            # 播放器/预览对输出 seek 会吸附关键帧——GUI 的 A/B 对比拖滑杆
            # 时 B 框长时间停在同一帧（用户实测），短 GOP 也让成片在播放器
            # 里拖动更跟手；码率代价可忽略
            '-g', str(max(12, int(round(fps * 2)))),
            '-pix_fmt', 'yuv420p',
            '-loglevel', 'error',
            output_path
        ]
        # stderr 改管道收集（原 DEVNULL）：编码器猝死（磁盘满/NVENC 掉驱动/
        # 参数非法）时 -loglevel error 的输出是唯一线索；不排空会撑满管道缓冲
        # 把 ffmpeg 自己堵死，故用守护线程持续读空
        self._stderr_lines = collections.deque(maxlen=32)
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=SUBPROCESS_CREATION_FLAGS,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        # 编码器秒死（参数非法/磁盘满等）时立即暴露，让上层走 cv2 回退，而不是静默产出空文件
        if self._process.poll() is not None:
            raise RuntimeError(f'ffmpeg encoder exited immediately (rc={self._process.returncode}): {self._stderr_tail()}')

    def _drain_stderr(self):
        try:
            for raw in self._process.stderr:
                self._stderr_lines.append(raw.decode('utf-8', 'replace').rstrip())
        except Exception:
            pass  # 进程退出/管道被关：收集线程只是诊断用途，静默退场

    def _stderr_tail(self, n=4):
        lines = [ln for ln in self._stderr_lines if ln]
        return ' / '.join(lines[-n:]) if lines else '(ffmpeg stderr empty)'

    def write(self, frame):
        """写入一帧（numpy BGR 数组）。"""
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        # 写前体检：把两种「写不进去」变成可定位报错——
        # ① stdin 已关：writer 被 release 后又被写（跨段共享 writer 被提前释放的典型症状）
        # ② 编码器已退出：磁盘满/编码器崩溃，继续写只会静默丢帧
        if self._process.stdin.closed:
            raise RuntimeError('ffmpeg writer stdin already closed '
                               '(writer released before write; shared writers must not be released between segments)')
        rc = self._process.poll()
        if rc is not None:
            raise RuntimeError(f'ffmpeg encoder exited before write (rc={rc}): {self._stderr_tail()}')
        try:
            self._process.stdin.write(frame.tobytes())
        except (BrokenPipeError, ValueError) as e:
            # 管道断裂/句柄已关 = ffmpeg 已退出，继续写只会静默丢帧；抛出让任务显式失败
            raise RuntimeError(f'ffmpeg encoder died mid-write ({type(e).__name__}): {self._stderr_tail()}')

    def release(self):
        """关闭管道并等待编码完成（幂等：重复调用/对已退出进程安全）。"""
        try:
            self._process.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        try:
            self._process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=5)
        # 收 stderr 管道（收集线程随之退场），避免句柄泄漏
        try:
            self._process.stderr.close()
        except Exception:
            pass
