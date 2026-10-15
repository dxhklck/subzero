import gc
import queue
import torch
import shutil
import traceback
import subprocess
import os
from pathlib import Path
import threading
import cv2
import sys
from collections import deque
from functools import cached_property

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import *
from backend.tools.hardware_accelerator import HardwareAccelerator
from backend.tools.common_tools import is_video_or_image, is_image_file, get_readable_path, read_image, SUBPROCESS_CREATION_FLAGS
from backend.inpaint.sttn_auto_inpaint import STTNAutoInpaint
from backend.inpaint.sttn_det_inpaint import STTNDetInpaint
from backend.inpaint.lama_inpaint import LamaInpaint
from backend.inpaint.minimax_inpaint import MiniMaxInpaint
from backend.inpaint.opencv_inpaint import OpenCVInpaint
from backend.tools.inpaint_tools import create_mask, batch_generator, expand_frame_ranges
from backend.tools.strip_inpaint import StripCropInpaint
from backend.tools.model_config import ModelConfig
from backend.tools.ffmpeg_cli import FFmpegCLI
from backend.tools.subtitle_detect import SubtitleDetect
from backend.tools.video_io import FramePrefetcher, FFmpegVideoWriter
import tempfile
import multiprocessing
import time
from tqdm import tqdm
import numpy as np

class SubtitleRemover:
    def __init__(self, vd_path, gui_mode=False):
        # 线程锁
        self.lock = threading.RLock()
        # 用户指定的字幕区域位置
        self.sub_areas = []
        # 是否为gui运行，gui运行需要显示预览
        self.gui_mode = gui_mode
        self.hardware_accelerator = HardwareAccelerator.instance()
        # 是否使用硬件加速
        self.hardware_accelerator.set_enabled(config.hardwareAcceleration.value)
        self.model_config = ModelConfig()
        # 判断是否为图片
        self.is_picture = is_image_file(str(vd_path))
        # 视频路径
        self.video_path = vd_path
        self.video_cap = cv2.VideoCapture(get_readable_path(vd_path))
        # 通过视频路径获取视频名称
        self.vd_name = Path(self.video_path).stem
        # 视频帧总数
        self.frame_count = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT) + 0.5)
        # 视频帧率
        self.fps = self.video_cap.get(cv2.CAP_PROP_FPS)
        # 视频尺寸
        self.size = (int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        self.mask_size = (int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
        self.frame_height = int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.frame_width = int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        # 创建视频临时对象，windows下delete=True会有permission denied的报错
        self.video_temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        # 创建视频写对象（使用 FFmpeg libx264 编码，比 mp4v 质量更好、文件更小）
        try:
            self.video_writer = FFmpegVideoWriter(get_readable_path(self.video_temp_file.name), self.fps, self.size)
        except Exception:
            self.video_writer = cv2.VideoWriter(get_readable_path(self.video_temp_file.name), cv2.VideoWriter_fourcc(*'mp4v'), self.fps, self.size)
        self.video_out_path = os.path.abspath(os.path.join(os.path.dirname(self.video_path), f'{self.vd_name}_no_sub.mp4'))
        self.ext = os.path.splitext(vd_path)[-1]
        if self.is_picture:
            pic_dir = os.path.join(os.path.dirname(self.video_path), 'no_sub')
            if not os.path.exists(pic_dir):
                os.makedirs(pic_dir)
            self.video_out_path = os.path.join(pic_dir, f'{self.vd_name}{self.ext}')

        # 总处理进度
        self.progress_total = 0
        self.progress_remover = 0
        self.isFinished = False
        # 是否将原音频嵌入到去除字幕后的视频
        self.is_successful_merged = False
        # 进度监听器列表
        self.progress_listeners = []
        # inpaint的frame_no区域列表, 默认为inpaint所有帧
        self.ab_sections = None
        # 分段处理帧范围 [frame_start, frame_end)（0-based，frame_end=None=到片尾）。
        # 分段续跑由外层执行器在构造后注入（setattr），run() 起点据此 seek
        self.frame_start = 0
        self.frame_end = None
        # 分段写入起点 [frame_write_start, frame_end)（0-based，None=frame_start 即不跳写）。
        # 段间重叠：执行器令 frame_start 向前多取 overlap 帧作上下文，frame_write_start
        # 仍指段首——之前的帧只送模型（保邻帧窗口上下文）、不写入也不计进度
        self.frame_write_start = None
        # 分段模式：跳过残留二遍与音频合并（这两步由成片 concat 阶段统一处理）
        self.is_segment = False
        # 仅残留二修模式（--residue-only）：输入视频即第一遍修复产物，
        # 跳过主修复循环与检测驱动的主流程，仅对输入执行残留检测+二修后输出
        self.residue_only = False
        # 第一遍修复覆盖的区域（xmin,xmax,ymin,ymax），供二遍残留兜底做重叠判定
        self._first_pass_regions = []
        self._first_pass_union = None  # 第一遍修复区域的联合外接框（懒计算）

    @staticmethod
    def is_current_frame_no_start(frame_no, continuous_frame_no_list):
        """
        判断给定的帧号是否为开头，是的话返回结束帧号，不是的话返回-1
        """
        for start_no, end_no in continuous_frame_no_list:
            if start_no == frame_no:
                return True
        return False

    @staticmethod
    def find_frame_no_end(frame_no, continuous_frame_no_list):
        """
        判断给定的帧号是否为开头，是的话返回结束帧号，不是的话返回-1
        """
        for start_no, end_no in continuous_frame_no_list:
            if start_no <= frame_no <= end_no:
                return end_no
        return -1

    def update_progress(self, tbar, increment):
        tbar.update(increment)
        current_percentage = (tbar.n / tbar.total) * 100
        self.progress_remover = int(current_percentage)
        self.progress_total = self.progress_remover
        self.notify_progress_listeners()

    def append_output(self, *args):
        """输出信息到控制台
        Args:
            *args: 要输出的内容，多个参数将用空格连接
        """
        print(*args)
    
    def add_progress_listener(self, listener):
        """
        添加进度监听器
        
        Args:
            listener: 一个回调函数，接收参数 (progress_total, isFinished)
        """
        if listener not in self.progress_listeners:
            self.progress_listeners.append(listener)
    
    def remove_progress_listener(self, listener):
        """
        移除进度监听器
        
        Args:
            listener: 要移除的监听器函数
        """
        if listener in self.progress_listeners:
            self.progress_listeners.remove(listener)
            
    def notify_progress_listeners(self):
        """
        通知所有进度监听器当前进度
        """
        for listener in self.progress_listeners:
            try:
                listener(self.progress_total, self.isFinished)
            except Exception as e:
                traceback.print_exc()

    def update_preview_with_comp(self, frame_ori, frame_comp):
        """
        更新预览
        """
        pass

    def sttn_auto_mode(self, tbar):
        """
        使用sttn对选中区域进行重绘，不进行字幕检测
        """
        self.append_output(tr['Main']['ProcessingStartRemovingSubtitles'])
        mask_area_coordinates = []
        for sub_area in self.sub_areas:
            ymin, ymax, xmin, xmax = sub_area
            mask_area_coordinates.append((xmin, xmax, ymin, ymax))
        self._first_pass_regions.extend(mask_area_coordinates)
        mask = create_mask(self.mask_size, mask_area_coordinates)
        sttn_video_inpaint = STTNAutoInpaint(self.hardware_accelerator.device, self.model_config.STTN_AUTO_MODEL_PATH, self.video_path)
        sttn_video_inpaint(input_mask=mask, input_sub_remover=self, tbar=tbar)

    def _route_pick_model(self, start, end, motion_scores):
        """AUTO路由：按区间帧差均值选算法。静态区间走LAMA（快，静态背景≈无损），
        运动区间走MiniMax（质量优先）。返回 (model, batch_size)"""
        scores = [s for f, s in motion_scores.items() if start <= f <= end] if motion_scores else []
        if scores:
            avg = sum(scores) / len(scores)
        else:
            # 无帧差采样点（区间由合并/时间线扩展产生，运动量未知）：
            # 按运动处理走 MiniMax——质量优先取向下限，不用 LAMA 赌静态
            avg = float('inf')
        threshold = config.autoRouteStaticThreshold.value
        if avg < threshold:
            model = self.lama_inpaint
            batch_size = config.getSttnMaxLoadNum()
            tag = 'LAMA(静态)'
        else:
            model = self.minimax_inpaint
            batch_size = config.minimaxMaxLoadNum.value
            tag = 'MiniMax(运动)'
        self.append_output(f'[Route] 区间 {start}-{end}: 帧差均值 {avg:.2f} (阈值 {threshold}) -> {tag}')
        return model, batch_size

    @staticmethod
    def _bbox_union(boxes):
        return (min(b[0] for b in boxes), max(b[1] for b in boxes),
                min(b[2] for b in boxes), max(b[3] for b in boxes))

    @staticmethod
    def _bbox_iou(a, b):
        ix = min(a[1], b[1]) - max(a[0], b[0])
        iy = min(a[3], b[3]) - max(a[2], b[2])
        if ix <= 0 or iy <= 0:
            return 0.0
        inter = ix * iy
        area_a = (a[1] - a[0]) * (a[3] - a[2])
        area_b = (b[1] - b[0]) * (b[3] - b[2])
        return inter / float(area_a + area_b - inter)

    def _group_interval_masks(self, start, end, sub_list, iou_threshold=0.5):
        """把区间 [start,end] 按帧间字幕框变化切成连续小组。

        背景：此前每个区间只用一张「整区间并集mask」——运动镜头下字幕扫过的大片背景
        都会被圈进mask重绘（糊）。分组后每组mask只覆盖本组的实际字幕范围：
        只修需要的区域，推理面积更小，未覆盖的好背景原样保留。

        规则：
        - 帧框先过与非字幕过滤（高瘦误检，与主链路同一规则）
        - 帧并集框与当前组的运行并集 IoU>=iou_threshold 则并入本组，否则另开新组
        - 无框帧（时间线扩展产生的淡出帧）：组间空隙对半分给相邻两组，
          首组向前扩到区间头、末组向后扩到区间尾，保证区间内每帧恰好属于一组

        返回 [(g_start, g_end, coords), ...]；coords 为组内全部有效框，
        coords 为空表示整组帧都无有效检测框（直接写原帧即可）
        """
        limit = config.subtitleYXAxisDifferencePixel.value
        groups = []  # [g_start, g_end, coords(list), 运行并集]
        for f in range(start, end + 1):
            boxes = []
            for area in sub_list.get(f, []):
                xmin, xmax, ymin, ymax = area
                if (ymax - ymin) - (xmax - xmin) > limit:
                    continue
                if area not in boxes:
                    boxes.append(area)
            if not boxes:
                continue
            ub = self._bbox_union(boxes)
            if groups and self._bbox_iou(ub, groups[-1][3]) >= iou_threshold:
                g = groups[-1]
                g[1] = f
                g[3] = self._bbox_union([g[3], ub])
                g[2].extend(boxes)
            else:
                groups.append([f, f, list(boxes), ub])
        if not groups:
            return [(start, end, [])]
        # 无框帧归属：首组向前扩、末组向后扩、组间空隙对半分
        groups[0][0] = start
        groups[-1][1] = end
        for i in range(len(groups) - 1):
            mid = (groups[i][1] + groups[i + 1][0]) // 2
            groups[i][1] = mid
            groups[i + 1][0] = mid + 1
        # 同行合并：OCR 对同一条字幕的框宽逐帧波动/部分检出会切出窄组，
        # 窄组的静态掩膜盖不住其他帧字幕的两端（成片 B 框字幕两端残留的根因）。
        # 垂直重叠 >=50%（占较小者高度）的相邻组合并为一个跨度组，掩膜取
        # 全跨度水平并集——同一条字幕线上的帧共用一张完整行掩膜。
        # 代价：渐进显字等场景早期帧的掩膜偏宽（多修一点背景），换取消灭端部残留。
        def _vband(coords):
            return min(c[2] for c in coords), max(c[3] for c in coords)

        i = 0
        while i < len(groups) - 1:
            a, b = groups[i], groups[i + 1]
            ay0, ay1 = _vband(a[2])
            by0, by1 = _vband(b[2])
            ov = min(ay1, by1) - max(ay0, by0)
            ah, bh = ay1 - ay0, by1 - by0
            if ov > 0 and ah > 0 and bh > 0 and ov / min(ah, bh) >= 0.5:
                a[1] = b[1]
                a[2] = list(dict.fromkeys(a[2] + b[2]))
                a[3] = self._bbox_union([a[3], b[3]])
                del groups[i + 1]
            else:
                i += 1
        # 组内坐标去重（静态字幕每帧同框会重复）；选区整片擦除：用户手绘选区
        # （(ymin,ymax,xmin,xmax)）转换到掩膜口径 ((xmin,xmax,ymin,ymax)) 后并入
        # 每组坐标——选区内一律视为字幕，不依赖"每个字都被检出"
        # （描边/艺术字/淡入淡出的字形鬼影残留的根治手段）
        def _dedupe(seq):
            return list(dict.fromkeys(seq))

        if config.selectionAsMask.value and self.sub_areas:
            sel = [(a[2], a[3], a[0], a[1]) for a in self.sub_areas]
            return [(g[0], g[1], _dedupe(g[2] + sel)) for g in groups]
        return [(g[0], g[1], _dedupe(g[2])) for g in groups]

    @staticmethod
    def _split_duplicate_frames(frames, diff_threshold=1.0, probe_width=160):
        """识别组内与代表帧几乎相同的帧，跳过其推理、直接复用代表帧的修复结果。

        固定机位/静止画面（录屏、停顿、定格）常见成片重复帧；运动画面零跳过。
        判据为缩放灰度图的均值绝对差（0-255 标度）：阈值 1.0 约等于压缩噪声上限，
        真实运动（字幕动画/镜头晃动/胶片颗粒）通常大于 3，因此设计上零画质影响。

        返回 (kept_idx, dup_map)：kept_idx 为参与推理的帧下标（有序），
        dup_map 为 {被跳过帧下标: 代表帧下标}。
        """
        kept_idx = []
        dup_map = {}
        last_gray = None
        for i, frame in enumerate(frames):
            h, w = frame.shape[:2]
            pw = min(probe_width, w)
            gray = cv2.cvtColor(cv2.resize(frame, (pw, max(1, int(h * pw / w)))), cv2.COLOR_BGR2GRAY)
            if last_gray is None:
                kept_idx.append(i)
                last_gray = gray
                continue
            diff = float(np.mean(np.abs(gray.astype(np.int16) - last_gray.astype(np.int16))))
            if diff < diff_threshold:
                dup_map[i] = kept_idx[-1]
            else:
                kept_idx.append(i)
                last_gray = gray
        return kept_idx, dup_map

    def video_inpaint(self, tbar, model=None, batch_size=None, route=False):
        """字幕检测与去除的调度入口。

        pipelineChunkFrames 决定形态：
        - 0（关闭流水线）：全片检测完成后才开始去除，检测阶段修复资源空转（旧行为，
          作为兜底路径保留，出问题可一键回退）；
        - >0：按该帧数切块做流水线——检测线程产出一块，去除线程立刻消费一块，
          两者并行，整体耗时 ≈ max(检测, 修复) 而非二者之和。
        """
        frame_end = self.frame_end if self.frame_end is not None else self.frame_count
        chunk = int(config.pipelineChunkFrames.value or 0)
        if chunk <= 0 or (frame_end - self.frame_start) <= chunk:
            return self._video_inpaint_whole(tbar, model, batch_size, route)
        return self._video_inpaint_pipelined(tbar, model, batch_size, route, frame_end, chunk)

    def _plan_intervals(self, detector, sub_list, frame_start, range_start, range_end, tbar=None):
        """检测结果 → 「区间起点 -> 区间终点」映射（连续同掩膜 → 时间线外扩 → 短区间合并）。

        边界钳制（分块正确性关键）：
        - 起点钳到 max(range_start, frame_start+1)：区间跨块时必须在**本块首帧**重新
          起键，否则键落在块之前，本块帧匹配不到 → 字幕原样写出（漏除）；
        - 终点钳到 range_end：读帧必须严格按块计数，不得跨块读走下一块的帧
          （否则后续块逐帧错位，整片撕裂）。
        """
        if not sub_list:
            return {}
        ranges = detector.find_continuous_ranges_with_same_mask(sub_list)
        if tbar is not None:
            tbar.write(f'Subtitle detected: {ranges}')
        ranges = expand_frame_ranges(ranges, config.subtitleTimelineBackwardFrameCount.value,
                                     config.subtitleTimelineForwardFrameCount.value)
        ranges = detector.filter_and_merge_intervals(ranges, config.sttnReferenceLength.value)
        # 相邻区间同行合并（组内同行合并的跨区间补刀）：
        # 同一条字幕线的检测框逐帧波动（部分帧只检出中段/漏检其中一行）会被
        # find_continuous_ranges_with_same_mask 按掩膜变化切成多个区间；窄区间的
        # 静态掩膜盖不住宽帧的两端/另一行（B 框字幕两端残留的根因之一——
        # _group_interval_masks 的组内同行合并只在区间内部生效，跨区间无效）。
        # 垂直重叠 >=50%（占较小者高度）且间隔 <=7 帧（±5 外扩后本就相邻）的
        # 相邻区间合并为一个区间；合并后 _group_interval_masks 的组内同行合并
        # 会把全区间统一为一张完整行掩膜。
        # 代价：同位置先后两条字幕间的空档帧会被多修一点背景（数量受 7 帧间隔约束）。
        def _band(a, b):
            y0 = y1 = None
            for f in range(a, b + 1):
                for bx in sub_list.get(f, []):
                    y0 = bx[2] if y0 is None else min(y0, bx[2])
                    y1 = bx[3] if y1 is None else max(y1, bx[3])
            return (y0, y1) if y0 is not None else None

        i = 0
        while i < len(ranges) - 1:
            cur, nxt = ranges[i], ranges[i + 1]
            if nxt[0] - cur[1] <= 7:
                b1, b2 = _band(cur[0], cur[1]), _band(nxt[0], nxt[1])
                if b1 and b2:
                    ov = min(b1[1], b2[1]) - max(b1[0], b2[0])
                    h1, h2 = b1[1] - b1[0], b2[1] - b2[0]
                    if ov > 0 and min(h1, h2) > 0 and ov / min(h1, h2) >= 0.5:
                        ranges[i] = (cur[0], max(cur[1], nxt[1]))
                        del ranges[i + 1]
                        continue  # 合并后与新邻居继续比较
            i += 1
        if tbar is not None:
            tbar.write(f'Subtitle same-line intervals merged: {ranges}')
        lo = max(range_start, frame_start + 1)
        out = {}
        for start, end in ranges:
            key = max(start, lo)
            val = min(end, range_end)
            if val < key:
                continue
            out[key] = val
        return out

    def _video_inpaint_pipelined(self, tbar, model, batch_size, route, frame_end, chunk):
        """边检测边去除：检测线程（生产者）按块产出 interval 计划，
        主线程（消费者）按块读帧、修复、写盘，两者重叠。

        预取深度 2 块（用户定版语义）：先检测出 2 块再启动去除，之后消费者持续
        消费、生产者最多领先 2 块——既不逐块起停浪费算力，也不让修复等太久。
        跨块字幕区间会被自然切开（各块用本块帧的掩膜），逐帧不重不漏。
        """
        write_start = self.frame_write_start if self.frame_write_start is not None else self.frame_start
        # 预取深度（块）：先检测出这么多块再启动去除（config.pipelinePrefetchChunks，1-5）
        depth = max(1, min(5, int(config.pipelinePrefetchChunks.value or 1)))
        task_queue = queue.Queue(maxsize=depth)
        stop_event = threading.Event()
        state = {'detected': 0, 'error': None}
        # 检测器只在生产者线程内使用（paddle 推理句柄不做跨线程共享）
        detector = SubtitleDetect(self.video_path, self.sub_areas)

        def producer():
            try:
                cursor = self.frame_start + 1
                while cursor <= frame_end and not stop_event.is_set():
                    c_end = min(frame_end, cursor + chunk - 1)
                    sub_list = detector.find_subtitle_frame_no(frame_start=cursor - 1,
                                                               frame_end=c_end, sub_remover=self)
                    self._collect_first_pass_regions(sub_list)
                    state['detected'] += len(sub_list)
                    start_end_map = self._plan_intervals(detector, sub_list, self.frame_start,
                                                         cursor, c_end, tbar)
                    motion = dict(detector.frame_diff_scores) if route else None
                    detector.frame_diff_scores.clear()  # 逐块取走，避免全程累积
                    task_queue.put((cursor, c_end, start_end_map, sub_list, motion))
                    cursor = c_end + 1
            except BaseException as e:  # 异常必须回传主线程，否则消费者会阻塞在 get()
                state['error'] = e
            finally:
                task_queue.put(None)    # 哨兵：无论成败都要放行消费者

        worker = threading.Thread(target=producer, daemon=True)
        worker.start()
        self.append_output(tr['Main']['ProcessingStartRemovingSubtitles'])
        reader = FramePrefetcher(self.video_cap, max_frames=frame_end - self.frame_start)
        pending = deque()
        finished = False
        # 预取：先攒够 depth 块（不足则以生产者结束为准）再开始去除
        while len(pending) < depth and not finished:
            item = task_queue.get()
            if item is None:
                finished = True
            else:
                pending.append(item)
        try:
            while True:
                if not pending:
                    if finished:
                        break
                    item = task_queue.get()
                    if item is None:
                        break
                    pending.append(item)
                if state['error'] is not None:
                    break
                c_start, c_end, cur_map, cur_sub, motion = pending.popleft()
                self._process_range(reader, c_start, c_end, cur_map, cur_sub, tbar,
                                    model, batch_size, route, motion, write_start)
        finally:
            reader.stop()
            stop_event.set()
            worker.join(timeout=10)
        if state['error'] is not None:
            raise state['error']
        if state['detected'] == 0:
            raise Exception(tr['Main']['NoSubtitleDetected'].format(self.video_path))

    def _process_range(self, reader, range_start, range_end, start_end_map, sub_list, tbar,
                       model=None, batch_size=None, route=False, motion_scores=None, write_start=None):
        """处理帧号区间 [range_start, range_end]（1-based 全局帧号）：

        区间外的帧直接写原帧；命中区间起点的帧连续读到区间尾，按帧间 bbox 变化
        分组掩膜后修复写出。整片模式下一次覆盖全片；流水线模式下按块调用，
        每块严格读取自己的帧数（不得跨块，否则后续块逐帧错位）。
        """
        if write_start is None:
            write_start = self.frame_write_start if self.frame_write_start is not None else self.frame_start
        current_frame_index = range_start - 1
        while current_frame_index < range_end:
            ret, frame = reader.read()
            # 如果读取视频帧失败（视频读到最后一帧）
            if not ret:
                return
            current_frame_index += 1
            # 判断当前帧号是不是字幕区间开始, 如果不是，则直接写
            if current_frame_index not in start_end_map.keys():
                # 段首重叠帧（全局帧号 <= write_start）只作上下文，不写入
                if current_frame_index > write_start:
                    self.video_writer.write(frame)
                    self.update_progress(tbar, increment=1)
                    self.update_preview_with_comp(frame, frame)
            # 如果是区间开始，则找到尾巴
            else:
                start_frame_index = current_frame_index
                # 终点严格钳在本块内：跨块的区间由下一块接着处理（各自用本块掩膜）
                end_frame_index = min(start_end_map[current_frame_index], range_end)
                tbar.write(f'processing frame {start_frame_index} to {end_frame_index}')
                # 用于存储需要去字幕的视频帧
                frames_need_inpaint = list()
                frames_need_inpaint.append(frame)
                # 接着往下读，直到读取到尾巴（不得超过本块末帧）
                while current_frame_index < end_frame_index:
                    ret, frame = reader.read()
                    if not ret:
                        break
                    current_frame_index += 1
                    frames_need_inpaint.append(frame)
                # 1. 按帧间bbox变化把区间切组，每组独立mask（运动镜头下整区间并集mask
                #    会把字幕扫过的大片背景圈进去重绘导致糊，分组后只修实际字幕范围）
                groups = self._group_interval_masks(start_frame_index, end_frame_index, sub_list)
                # 2. 逐组推理
                for g_start, g_end, g_coords in groups:
                    frames_g = frames_need_inpaint[g_start - start_frame_index: g_end - start_frame_index + 1]
                    if not frames_g:
                        continue
                    if not g_coords:
                        # 整组帧都无有效检测框（全被过滤为误检）：写原帧
                        # （组内前 ctx 帧为段间重叠上下文：不写、不计进度）
                        n_write = 0
                        for i, frame in enumerate(frames_g):
                            if g_start + i > write_start:
                                self.video_writer.write(frame)
                                self.update_preview_with_comp(frame, frame)
                                n_write += 1
                        if n_write:
                            self.update_progress(tbar, increment=n_write)
                        continue
                    mask = create_mask(self.mask_size, g_coords)
                    if route:
                        model, batch_size = self._route_pick_model(g_start, g_end, motion_scores)
                    if batch_size is None:
                        batch_size = config.getSttnMaxLoadNum()
                    # 重复帧跳过：与代表帧几乎相同的帧不送推理，修复后直接复用其结果
                    kept_idx, dup_map = self._split_duplicate_frames(frames_g)
                    frames_run = [frames_g[i] for i in kept_idx]
                    # 组内前 ctx 帧为段间重叠上下文（全局帧号 <= write_start）：
                    # 仍送推理保邻帧窗口上下文，但不写入、不计进度
                    ctx = max(0, min(len(frames_g), write_start + 1 - g_start))
                    if len(dup_map) > 0:
                        self.append_output(f'[Skip] 组 {g_start}-{g_end}: {len(dup_map)}/{len(frames_g)} 帧与相邻帧几乎相同，跳过推理直接复用')
                    # MiniMax（StripCropInpaint包装）内部已按 chunk_frames(4k+1) 分块、块间重叠混合
                    # 并做显存守卫；外层再按小批分割会把接缝混合割裂（每批仅含一块、批间无重叠），
                    # 故整组一次性送入。上限4倍块大小，避免超长组把全部帧堆在内存
                    if isinstance(model, StripCropInpaint):
                        chunk = getattr(model.inner, 'chunk_frames', 61)
                        batch_size = max(batch_size, min(len(frames_run), chunk * 4))
                    out_kept = []
                    run_pos = 0
                    for batch in batch_generator(frames_run, batch_size):
                        # 3. 调用批推理
                        if len(batch) >= 1:
                            out_kept.extend(model(batch, mask))
                        # 仅统计本批中需要写入的帧（重叠上下文帧不计进度）
                        n_write = sum(1 for i in kept_idx[run_pos:run_pos + len(batch)] if i >= ctx)
                        run_pos += len(batch)
                        if n_write:
                            self.update_progress(tbar, increment=n_write)
                    # 重建全组输出：推理帧按原位回填，重复帧复用代表帧的修复结果
                    if dup_map:
                        out_full = [None] * len(frames_g)
                        for k, i in enumerate(kept_idx):
                            out_full[i] = out_kept[k]
                        for di, ri in dup_map.items():
                            out_full[di] = out_full[ri]
                        n_dup_write = sum(1 for di in dup_map if di >= ctx)
                        if n_dup_write:
                            self.update_progress(tbar, increment=n_dup_write)
                    else:
                        out_full = out_kept
                    for i, (src, rep) in enumerate(zip(frames_g, out_full)):
                        # 段首重叠帧只作上下文不写入（进度已在上面按写入帧计数）
                        if g_start + i > write_start:
                            self.video_writer.write(rep)
                            self.update_preview_with_comp(np.clip(src+mask[:,:,np.newaxis]*0.3,0,255).astype(np.uint8), rep)

    def _video_inpaint_whole(self, tbar, model=None, batch_size=None, route=False):
        sub_detector = SubtitleDetect(self.video_path, self.sub_areas)
        sub_list = sub_detector.find_subtitle_frame_no(sub_remover=self)
        self._collect_first_pass_regions(sub_list)
        if len(sub_list) == 0:
            raise Exception(tr['Main']['NoSubtitleDetected'].format(self.video_path))
        continuous_frame_no_list = sub_detector.find_continuous_ranges_with_same_mask(sub_list)
        tbar.write(f"Subtitle detected: {continuous_frame_no_list}")
        continuous_frame_no_list = expand_frame_ranges(continuous_frame_no_list, config.subtitleTimelineBackwardFrameCount.value, config.subtitleTimelineForwardFrameCount.value)
        tbar.write(f"Subtitle timeline expand ({config.subtitleTimelineBackwardFrameCount.value} <- -> {config.subtitleTimelineForwardFrameCount.value}): {continuous_frame_no_list}")
        continuous_frame_no_list = sub_detector.filter_and_merge_intervals(continuous_frame_no_list, config.sttnReferenceLength.value)
        tbar.write(f'Subtitle filter_and_merge_intervals: {continuous_frame_no_list}')
        # AUTO路由需要在删除检测器前取出帧差分数
        motion_scores = sub_detector.frame_diff_scores if route else None
        del sub_detector
        gc.collect()
        start_end_map = dict()
        frame_end = self.frame_end if self.frame_end is not None else self.frame_count
        for start, end in continuous_frame_no_list:
            # 确保区间不超出视频总帧数（分段时同时钳制到段尾），否则会导致
            # FramePrefetcher 哨兵被内循环消费后外层死锁；
            # 起点同时钳制到段首（帧号体系为 1-based 标签，段内首帧 = frame_start+1），
            # 否则向前外扩跨段边界时整段区间匹配不到、原帧直接写出（字幕漏除）
            start_end_map[max(start, self.frame_start + 1)] = min(end, frame_end)
        # 段间重叠：frame_write_start 之前的帧只作上下文（送模型、不写入、不计进度）；
        # 未注入时等于 frame_start，行为与旧版完全一致
        write_start = self.frame_write_start if self.frame_write_start is not None else self.frame_start
        current_frame_index = self.frame_start
        self.append_output(tr['Main']['ProcessingStartRemovingSubtitles'])
        # 使用帧预读取，I/O 与推理重叠；段帧数作为预读取上限（读满即停）
        reader = FramePrefetcher(self.video_cap, max_frames=frame_end - self.frame_start)
        self._process_range(reader, self.frame_start + 1, frame_end, start_end_map, sub_list,
                            tbar, model, batch_size, route, motion_scores, write_start)
        reader.stop()

    def run(self):
        # 记录开始时间
        start_time = time.time()
        # 仅残留二修模式：短路主流程，仅对输入（第一遍修复产物）执行
        # 「残留检测 + 二修」并输出（含音频 mux）
        if self.residue_only:
            self._run_residue_only(start_time)
            return
        if len(self.sub_areas) == 0:
            self.append_output(tr['Main']['FullScreenProcessingNote'])
            self.sub_areas.append((0, self.frame_height, 0, self.frame_width))
        self.append_output(tr['Main']['SubtitleArea'].format(self.sub_areas))
        self.append_output(tr['Main']['ABSection'].format(str(self.ab_sections).replace("range", "") if self.ab_sections is not None and len(self.ab_sections) > 0 else tr['Main']['ABSectionAll']))
        # 如果使用GPU加速，则打印GPU加速提示
        if self.hardware_accelerator.has_accelerator():
            accelerator_name = self.hardware_accelerator.accelerator_name
            if accelerator_name == 'DirectML' and config.inpaintMode.value not in [InpaintMode.STTN_AUTO, InpaintMode.STTN_DET]:
                self.append_output(tr['Main']['DirectMLWarning'])
        os.makedirs(os.path.dirname(self.video_out_path), exist_ok=True)
        # 分段帧范围：seek 到段起点（frame_start 在构造后由执行器注入）
        frame_end = self.frame_end if self.frame_end is not None else self.frame_count
        # 进度条总量 = 本次要写入的帧数（frame_end - frame_write_start）；
        # 段间重叠的前 overlap 帧只作上下文送模型（不写入），不计入进度
        write_start = self.frame_write_start if self.frame_write_start is not None else self.frame_start
        seg_total = max(1, frame_end - write_start)
        if self.frame_start > 0:
            self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, self.frame_start)
        # 重置进度条
        self.progress_total = 0
        tbar = tqdm(total=int(seg_total), unit='frame', position=0, file=sys.__stdout__,
                    desc='Subtitle Removing')
        if self.is_picture:
            original_frame = read_image(self.video_path)
            if original_frame is None:
                self.append_output(tr['Main']['ReadImageFailed'].format(self.video_path))
                return
            sub_detector = SubtitleDetect(self.video_path, self.sub_areas)
            sub_list = sub_detector.detect_subtitle(original_frame)
            del sub_detector
            gc.collect()
            if len(sub_list):
                mask = create_mask(original_frame.shape[0:2], sub_list)
                inpainted_frame = self.lama_inpaint.inpaint(original_frame, mask)
                self.update_preview_with_comp(np.clip(original_frame+mask[:,:,np.newaxis]*0.3,0,255).astype(np.uint8), inpainted_frame)
            else:
                inpainted_frame = original_frame
                self.update_preview_with_comp(original_frame, inpainted_frame)
            cv2.imencode(self.ext, inpainted_frame)[1].tofile(self.video_out_path)
            tbar.update(1)
            self.progress_total = 100
        else:
            # 精准模式下，获取场景分割的帧号，进一步切割
            self.log_model()
            if config.inpaintMode.value == InpaintMode.STTN_AUTO:
                self.sttn_auto_mode(tbar)
            elif config.inpaintMode.value == InpaintMode.STTN_DET:
                self.video_inpaint(tbar, self.sttn_det_inpaint)
            elif config.inpaintMode.value == InpaintMode.LAMA:
                self.video_inpaint(tbar, self.lama_inpaint)
            elif config.inpaintMode.value == InpaintMode.MINIMAX:
                self.video_inpaint(tbar, self.minimax_inpaint, config.minimaxMaxLoadNum.value)
            elif config.inpaintMode.value == InpaintMode.AUTO:
                # 智能路由：按区间帧差自动选 LAMA（静态）或 MiniMax（运动）
                self.video_inpaint(tbar, route=True)
            elif config.inpaintMode.value == InpaintMode.OPENCV:
                self.video_inpaint(tbar, OpenCVInpaint())
            else:
                raise Exception(f'inpaint mode: {config.inpaintMode.value} not implemented')

        self.video_cap.release()
        self.video_writer.release()
        if not self.is_picture:
            if self.is_segment:
                # 分段模式：段数据仍在临时文件，直接落盘为段输出；
                # 残留兜底与音频封装由成片 concat 阶段统一处理
                shutil.copy2(self.video_temp_file.name, self.video_out_path)
            else:
                # 二遍残留兜底：对第一遍输出重跑检测，检出残留并二修（可开关）
                self._residue_second_pass(tbar)
                # 将原音频合并到新生成的视频文件中
                self.merge_audio_to_video()
        self.append_output(tr['Main']['FinishedProcessing'].format(self.video_out_path))
        self.append_output(tr['Main']['ProcessingTime'].format(round(time.time() - start_time)))
        self.isFinished = True
        self.progress_total = 100
        try:
            self.video_temp_file.close()  # 先关句柄，否则 Windows 下 os.remove 报 PermissionError 泄漏临时文件
        except Exception:
            pass
        if os.path.exists(self.video_temp_file.name):
            try:
                os.remove(self.video_temp_file.name)
            except Exception:
                pass #ignore

    def _run_residue_only(self, start_time):
        """--residue-only：输入视频（-i）即第一遍修复产物。
        跳过主修复循环与检测驱动的主流程，仅对输入执行「残留检测 + 二修」
        （复用 _residue_second_pass 既有逻辑），输出到 -o（含音频 mux）。"""
        if self.is_picture:
            raise Exception('--residue-only only supports video input, got an image file.')
        self.append_output('[ResidueOnly] 输入视频为第一遍修复产物, 跳过主修复流程, 仅执行残留检测与二次修复')
        # 残留检查开关在该模式下强制启用（仅改内存值不写盘；调用方已判断过需要二修）
        old_residue = config.residueCheck.value
        config.residueCheck.value = True
        try:
            # _residue_second_pass 以 video_temp_file 为「第一遍输出」：
            # 先释放主写通道（其编码子进程持有该临时路径），再把输入复制过去
            try:
                self.video_writer.release()
            except Exception:
                pass
            shutil.copy2(get_readable_path(self.video_path), get_readable_path(self.video_temp_file.name))
            # 第一遍覆盖区域（供残留重叠判定，防止把画面里天然像文字的纹理误当残留误修）：
            # 指定了字幕区域时用区域本身（与 sttn_auto_mode 收集方式一致）；
            # 未指定时按第一遍检测流程在输入上采样检测收集（与 video_inpaint 一致），
            # 使未传 --sub-area 的契约调用（GUI 分段成片二修兜底）也能生效
            if len(self.sub_areas) > 0:
                for ymin, ymax, xmin, xmax in self.sub_areas:
                    self._first_pass_regions.append((xmin, xmax, ymin, ymax))
            else:
                self.append_output(tr['Main']['ProcessingStartFindingSubtitles'])
                detector = SubtitleDetect(self.video_path, self.sub_areas)
                sub_list = detector.find_subtitle_frame_no()
                self._collect_first_pass_regions(sub_list)
                del detector
                gc.collect()
            tbar = tqdm(total=1, unit='pass', position=0, file=sys.__stdout__,
                        desc='Residue Second Pass')
            # 残留检测 + 二修（内部按 config.residueCheck 开关与第一遍区域判定）
            self._residue_second_pass(tbar)
            # 将原音频合并到新生成的视频文件中
            self.merge_audio_to_video()
            tbar.update(1)
        finally:
            config.residueCheck.value = old_residue
        self.video_cap.release()
        self.append_output(tr['Main']['FinishedProcessing'].format(self.video_out_path))
        self.append_output(tr['Main']['ProcessingTime'].format(round(time.time() - start_time)))
        self.isFinished = True
        self.progress_total = 100
        try:
            self.video_temp_file.close()  # 先关句柄，否则 Windows 下 os.remove 报 PermissionError 泄漏临时文件
        except Exception:
            pass
        if os.path.exists(self.video_temp_file.name):
            try:
                os.remove(self.video_temp_file.name)
            except Exception:
                pass #ignore

    def log_model(self):
        model_friendly_name = list(tr['InpaintMode'].values())[list(InpaintMode).index(config.inpaintMode.value)]
        model_device = 'CPU'
        if config.inpaintMode.value != InpaintMode.OPENCV and self.hardware_accelerator.has_accelerator():
            accelerator_name = self.hardware_accelerator.accelerator_name
            if accelerator_name == 'DirectML' and config.inpaintMode.value in [InpaintMode.STTN_AUTO, InpaintMode.STTN_DET]:
                model_device = 'DirectML'
            if self.hardware_accelerator.has_cuda() or self.hardware_accelerator.has_mps():
                model_device = accelerator_name
        self.append_output(tr['Main']['SubtitleRemoverModel'].format(f"{model_friendly_name} ({model_device})"))
        detect_mode_name = list(tr['SubtitleDetectMode'].values())[list(SubtitleDetectMode).index(config.subtitleDetectMode.value)]
        detect_device = 'GPU' if self.hardware_accelerator.has_paddle_cuda() else 'CPU'
        providers = ", ".join(self.hardware_accelerator.onnx_providers)
        providers_str = f", {providers}" if providers else ""
        self.append_output(tr['Main']['SubtitleDetectionModel'].format(f"{detect_mode_name} ({detect_device}{providers_str})"))
        # 有 N 卡但 Paddle GPU 走不了（驱动过旧 / 显卡架构不被当前 paddle 覆盖 / CUDA DLL 异常）时，
        # 在日志窗口给出原因与建议，避免用户误以为字幕检测坏了
        fallback_reason = self.hardware_accelerator.paddle_cuda_fallback_reason
        if fallback_reason:
            self.append_output(tr['Main']['PaddleGpuFallbackHint'].format(fallback_reason))
            driver_ver, driver_cuda = self.hardware_accelerator.get_nvidia_driver_info()
            paddle_cuda = self.hardware_accelerator.get_paddle_compile_cuda()
            self.append_output('NVIDIA driver: {} (supports CUDA {}), paddle compiled CUDA: {}'.format(
                driver_ver or 'unknown', driver_cuda or 'unknown', paddle_cuda or 'n/a'))

    def _collect_first_pass_regions(self, sub_list):
        """收集第一遍检测/修复覆盖的区域（xmin,xmax,ymin,ymax），供二遍残留兜底做重叠判定。
        剔除高瘦误检框（与第一遍修复同规则），保证"第一遍真正修过的区域"这个语义一致。
        选区整片擦除开启时，用户选区同样属于第一遍修复区域（选区内检出按残留二修）。"""
        for boxes in sub_list.values():
            for b in boxes:
                xmin, xmax, ymin, ymax = b
                if (ymax - ymin) - (xmax - xmin) > config.subtitleYXAxisDifferencePixel.value:
                    continue
                self._first_pass_regions.append((xmin, xmax, ymin, ymax))
        if config.selectionAsMask.value and self.sub_areas:
            for a in self.sub_areas:
                # SUB_AREAS 为 (ymin,ymax,xmin,xmax)，统一到 (xmin,xmax,ymin,ymax)
                self._first_pass_regions.append((a[2], a[3], a[0], a[1]))

    def _overlaps_first_pass(self, box, margin=30):
        """残留判定：输出视频中新检出的框与第一遍修复区域（外扩 margin 容差）重叠。

        防止把画面里天然像文字的纹理（波纹/栅栏/立面/水面反光）误当残留而误修。
        兜底：第一遍**完全漏检**的字幕在 _first_pass_regions 里没有对应修复区域，
        逐区域重叠判定会把它拒掉（残留二遍检出也白检——正是「漏检残留」Persist
        的根因）。字幕通常固定在同一带内，故追加联合外接框判定：落在第一遍全部
        修复区域的联合包围盒（外扩 margin）内的检出，同样视为残留予以二修；
        带外的天然纹理仍被拒绝。
        """
        xmin, xmax, ymin, ymax = box
        for rx0, rx1, ry0, ry1 in self._first_pass_regions:
            if xmin < rx1 + margin and xmax > rx0 - margin and ymin < ry1 + margin and ymax > ry0 - margin:
                return True
        if self._first_pass_union is None:
            self._first_pass_union = (
                min(r[0] for r in self._first_pass_regions),
                max(r[1] for r in self._first_pass_regions),
                min(r[2] for r in self._first_pass_regions),
                max(r[3] for r in self._first_pass_regions),
            )
        ux0, ux1, uy0, uy1 = self._first_pass_union
        return (xmin < ux1 + margin and xmax > ux0 - margin
                and ymin < uy1 + margin and ymax > uy0 - margin)

    def _residue_second_pass(self, tbar):
        """二遍残留兜底：第一遍修复依赖检测，漏检/欠覆盖的字幕会残留在输出里。
        对第一遍输出（video_temp_file，此时无音频）重跑检测：
        1. 检出框必须与第一遍修复区域重叠才算残留（防误修天然纹理）；
        2. 残留按帧分组、时间线扩展后逐区间二修（AUTO 模式下按帧差路由，其余模式用 LAMA——
           残留 mask 小，LAMA 快且对静态/小区域足够好；残留根因是检测漏而不是算法差）；
        3. 修复帧替换后整体写新临时文件并原子替换，保证音频合并拿到的是修复后的视频；
        4. 无残留时只多花一次检测时间，不做任何重编码。"""
        if self.is_picture or not config.residueCheck.value or not self._first_pass_regions:
            return
        self.append_output(tr['Main']['ResidueCheckStart'])
        t0 = time.time()
        # 盲区补查：二遍用与第一遍不同的检测参数——960长边（检测模型内部的原生分辨率，
        # 与第一遍720长边「缩小→模型内部放大」的插值路径不同）。同参数两遍检测的漏检
        # 高度相关（同一图同一缩放路径会同样漏），换参数才有独立的捕获概率；
        # 残留是持续烘焙在每帧上的，采样密度对检出影响小，换分辨率才是关键。
        # 仅改内存值，不写盘（item.value= 不触发 qconfig 保存）
        old_size = config.subtitleDetectProcessSize.value
        try:
            config.subtitleDetectProcessSize.value = 960
            # 敏感检测参数：残留多为笔画碎边（对比度低、得分低），默认 box_thresh=0.6
            # 会漏——特别是描边/艺术字字幕的动画片。降低门槛 + 加大框扩张：
            # box_thresh 0.6→0.3（碎边也能成框）、thresh 0.3→0.2、unclip 1.5→2.0
            # （框外扩约1/3，盖住描边残留）。误检由 _overlaps_first_pass 与
            # 高瘦过滤双保险约束，代价只是对已修复区域多做一次小面积 LAMA。
            detector = SubtitleDetect(
                self.video_temp_file.name, self.sub_areas,
                det_params={'thresh': 0.2, 'box_thresh': 0.3, 'unclip_ratio': 2.0})
            sub_list = detector.find_subtitle_frame_no()
        finally:
            config.subtitleDetectProcessSize.value = old_size
        if len(sub_list) == 0:
            self.append_output(tr['Main']['ResidueCheckClean'].format(round(time.time() - t0)))
            del detector
            gc.collect()
            return
        # 只保留与第一遍修复区域重叠、且非高瘦误检的检出框
        filtered = {}
        for fno, boxes in sub_list.items():
            keep = [b for b in boxes
                    if (b[3] - b[2]) - (b[1] - b[0]) <= config.subtitleYXAxisDifferencePixel.value
                    and self._overlaps_first_pass(b)]
            if keep:
                filtered[fno] = keep
        if not filtered:
            self.append_output(tr['Main']['ResidueCheckClean'].format(round(time.time() - t0)))
            del detector
            gc.collect()
            return
        ranges = detector.find_continuous_ranges_with_same_mask(filtered)
        ranges = expand_frame_ranges(ranges, config.subtitleTimelineBackwardFrameCount.value,
                                     config.subtitleTimelineForwardFrameCount.value)
        ranges = detector.filter_and_merge_intervals(ranges, config.sttnReferenceLength.value)
        motion_scores = detector.frame_diff_scores
        del detector
        gc.collect()
        self.append_output(tr['Main']['ResidueCheckFound'].format(len(filtered), len(ranges)))
        # 逐区间二修
        repaired = {}  # 帧号(1基) -> 修复帧
        for start, end in ranges:
            end = min(end, self.frame_count)
            coords = []
            for fno in range(start, end + 1):
                for area in filtered.get(fno, []):
                    if area not in coords:
                        coords.append(area)
            if not coords:
                continue
            if config.inpaintMode.value == InpaintMode.AUTO:
                model, batch_size = self._route_pick_model(start, end, motion_scores)
            else:
                model, batch_size = self.lama_inpaint, config.getSttnMaxLoadNum()
            tbar.write(f"[Residue] repairing frames {start}-{end} with {type(model).__name__}")
            cap = cv2.VideoCapture(get_readable_path(self.video_temp_file.name))
            cap.set(cv2.CAP_PROP_POS_FRAMES, start - 1)
            frames = []
            for _ in range(end - start + 1):
                ret, frame = cap.read()
                if not ret:
                    break
                frames.append(frame)
            cap.release()
            if not frames:
                continue
            mask = create_mask(self.mask_size, coords)
            out_frames = []
            for batch in batch_generator(frames, batch_size):
                out_frames.extend(model(batch, mask))
            for i, f in enumerate(out_frames):
                repaired[start + i] = f
        if not repaired:
            self.append_output(tr['Main']['ResidueCheckClean'].format(round(time.time() - t0)))
            return
        # 整体回写：读第一遍输出，残留帧替换为修复帧，写新临时文件后原子替换
        new_temp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        new_temp.close()
        try:
            writer = FFmpegVideoWriter(get_readable_path(new_temp.name), self.fps, self.size)
        except Exception:
            writer = cv2.VideoWriter(get_readable_path(new_temp.name), cv2.VideoWriter_fourcc(*'mp4v'),
                                     self.fps, self.size)
        cap = cv2.VideoCapture(get_readable_path(self.video_temp_file.name))
        n_repaired = len(repaired)
        fno = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            fno += 1
            writer.write(repaired.pop(fno, frame))
        cap.release()
        writer.release()
        # 关闭旧临时文件句柄后原子替换（Windows 下句柄未关会替换失败）
        try:
            self.video_temp_file.close()
        except Exception:
            pass
        os.replace(new_temp.name, self.video_temp_file.name)
        self.append_output(tr['Main']['ResidueCheckRepaired'].format(n_repaired, round(time.time() - t0)))

    def merge_audio_to_video(self):
        # 创建音频临时对象，windows下delete=True会有permission denied的报错。
        # 用 .mka 中转：-acodec copy 到 .aac（ADTS）容器时 AC-3/DTS 等编码必然失败，
        # mka 可容纳任意编码，抽取/封装全程无损
        temp = tempfile.NamedTemporaryFile(suffix='.mka', delete=False)
        audio_extract_command = [FFmpegCLI.instance().ffmpeg_path,
                                 "-y", "-i", self.video_path,
                                 "-acodec", "copy",
                                 "-vn", "-loglevel", "error", temp.name]
        use_shell = True if os.name == "nt" else False
        try:
            with open(os.devnull) as devnull:
                subprocess.check_output(audio_extract_command, stdin=devnull, shell=use_shell, timeout=600,
                                        creationflags=SUBPROCESS_CREATION_FLAGS)
        except Exception as e:
            traceback.print_exc()
            self.append_output(tr['Main']['FailToExtractAudio'].format(str(e)))
            return
        else:
            if os.path.exists(self.video_temp_file.name):
                audio_merge_command = [FFmpegCLI.instance().ffmpeg_path,
                                       "-y", "-i", self.video_temp_file.name,
                                       "-i", temp.name,
                                       "-vcodec", "copy",
                                       "-acodec", "copy",
                                       "-loglevel", "error", self.video_out_path]
                try:
                    with open(os.devnull) as devnull:
                        subprocess.check_output(audio_merge_command, stdin=devnull, shell=use_shell, timeout=600,
                                                creationflags=SUBPROCESS_CREATION_FLAGS)
                    # 仅在真正完成封装时置位；临时文件缺失/封装失败时保持 False，
                    # 让 finally 的 copy 兜底产出无音频成片
                    self.is_successful_merged = True
                except Exception as e:
                    traceback.print_exc()
                    self.append_output(tr['Main']['FailToMergeAudio'].format(str(e)))
                    return
            if os.path.exists(temp.name):
                try:
                    os.remove(temp.name)
                except Exception:
                    #ignore
                    pass
        finally:
            temp.close()
            if not self.is_successful_merged:
                try:
                    shutil.copy2(self.video_temp_file.name, self.video_out_path)
                except IOError as e:
                    self.append_output(tr['Main']['CopyFileFailed'].format(self.video_temp_file.name, self.video_out_path, str(e)))
            self.video_temp_file.close()

    @cached_property
    def lama_inpaint(self):
        model_path = os.path.join(self.model_config.LAMA_MODEL_DIR, 'big-lama.pt')
        device = self.hardware_accelerator.device if self.hardware_accelerator.has_cuda() or self.hardware_accelerator.has_mps() else torch.device("cpu")
        return LamaInpaint(device, model_path)

    @cached_property
    def sttn_det_inpaint(self):
        return STTNDetInpaint(self.hardware_accelerator.device, self.model_config.STTN_DET_MODEL_PATH)

    @cached_property
    def minimax_inpaint(self):
        device = self.hardware_accelerator.device if self.hardware_accelerator.has_cuda() or self.hardware_accelerator.has_mps() else torch.device("cpu")
        model = MiniMaxInpaint(device, self.model_config.MINIMAX_MODEL_DIR,
                               max_load_num=config.minimaxMaxLoadNum.value,
                               num_inference_steps=config.minimaxNumInferenceSteps.value,
                               iterations=config.minimaxIterations.value,
                               process_width=config.minimaxProcessWidth.value,
                               block_overlap=config.minimaxBlockOverlap.value,
                               vae_decode_fp32=config.minimaxVaeDecodeFp32.value)
        # 条带裁剪修复：只把 mask 外扩 padding 的条带送入模型，窗口外像素原样保留
        return StripCropInpaint(model, config.minimaxStripPadding.value)


if __name__ == '__main__':
    multiprocessing.set_start_method("spawn")
    from backend.tools.args_handler import parse_args
    args = parse_args()
    if not is_video_or_image(args.input):
        print(f'Error: {args.input} is not a supported video or image file.')
        exit(-1)
    sr = SubtitleRemover(args.input)
    sr.sub_areas = args.subtitle_area_coords
    # 显式传 -o 时覆盖（转绝对路径：纯文件名时 os.path.dirname('') 会使 makedirs 崩溃）；
    # 省略则用默认（输入同目录 <名>_no_sub.mp4，见 __init__）
    if args.output is not None:
        sr.video_out_path = os.path.abspath(args.output)
    # 显式传参时仅覆盖内存值（不写盘，不污染 GUI 设置）；不传则沿用 config.json 中 GUI 选择的模式
    if args.inpaint_mode is not None:
        config.inpaintMode.value = args.inpaint_mode
    # 分段帧范围（可选）：由分段续跑执行器或用户手工分段使用
    if args.frame_start is not None:
        sr.frame_start = max(0, args.frame_start)
    if args.frame_end is not None:
        sr.frame_end = max(1, args.frame_end)
    # 段间重叠写入起点：之前的帧只作上下文送模型、不写入（默认与 frame_start 相同）
    if args.frame_write_start is not None:
        sr.frame_write_start = max(0, args.frame_write_start)
    # 帧范围合法性：start/end 倒置或写入起点越界会使预读取读零帧/区间永远不命中，静默输出空视频
    if sr.frame_start is not None and sr.frame_end is not None and sr.frame_end <= sr.frame_start:
        print(f"Error: --frame-end ({args.frame_end}) must be greater than "
              f"--frame-start ({args.frame_start}).")
        exit(-1)
    if sr.frame_write_start is not None:
        lo = sr.frame_start if sr.frame_start is not None else 0
        hi = sr.frame_end if sr.frame_end is not None else sr.frame_count
        if not (lo <= sr.frame_write_start < hi):
            print(f"Error: --frame-write-start ({args.frame_write_start}) must be within "
                  f"[--frame-start, --frame-end).")
            exit(-1)
    # 时间区间（秒 → 帧范围，0-based、end 排他）：仅处理并输出区间内帧；
    # 同时按 GUI 时间区间（AB 区块）语义设置 ab_sections（0-based、end 排他）
    if args.time_range is not None:
        start_sec, end_sec = args.time_range
        fps = sr.fps
        if not fps or fps <= 0:
            print(f'Error: cannot determine the frame rate of {args.input}, '
                  f'--time-range requires a readable video.')
            exit(-1)
        frame_start = int(start_sec * fps)
        frame_end = int(end_sec * fps)
        if frame_end <= frame_start:
            print(f"Error: --time-range '{start_sec}-{end_sec}' is shorter than one frame "
                  f"at {fps:.3f} fps.")
            exit(-1)
        if frame_start >= sr.frame_count:
            print(f'Error: --time-range start {start_sec}s (frame {frame_start}) is at or beyond '
                  f'the end of the video ({sr.frame_count} frames).')
            exit(-1)
        sr.frame_start = frame_start
        sr.frame_end = min(frame_end, sr.frame_count)
        sr.ab_sections = [range(sr.frame_start, sr.frame_end)]
        print(f'Time range {start_sec}s-{end_sec}s -> frames [{sr.frame_start}, {sr.frame_end}) '
              f'at {fps:.3f} fps')
    # 仅残留二修模式：输入视频即第一遍修复产物，跳过主修复流程
    if args.residue_only:
        if sr.is_picture:
            print('Error: --residue-only only supports video input, got an image file.')
            exit(-1)
        sr.residue_only = True
    sr.run()
        
