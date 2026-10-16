import sys
from functools import cached_property

import cv2
import numpy as np
from tqdm import tqdm

from .model_config import ModelConfig
from .hardware_accelerator import HardwareAccelerator
from .common_tools import get_readable_path
from .ocr import get_coordinates
from backend.config import config, tr
from backend.scenedetect import scene_detect
from backend.scenedetect.detectors import ContentDetector
from backend.tools.inpaint_tools import is_frame_number_in_ab_sections, area_active_at

# 检测重复帧跳过阈值：相邻采样帧 160px 长边灰度均值差（0-255 标度）低于该值时，
# 复用上一采样帧的 OCR 检出（相同像素 → 相同检测结果，零画质影响）。
# 与修复阶段 _split_duplicate_frames 同一标度（1.0 ≈ 压缩噪声上限）。
# 置 0 禁用（diff < 0 永不成立）
DETECT_DUP_THRESHOLD = 1.0

class SubtitleDetect:
    """
    文本框检测类，用于检测视频帧中是否存在文本框
    """

    # 采样间隔，根据视频帧率在 _init_sample_step 中自适应设置
    SAMPLE_STEP = 3

    # 检测输入裁剪的外扩 margin：需覆盖 DB unclip 外扩（约 0.2~0.4 倍字高）、
    # 跨界文字与坐标数值噪声；按帧高 1/8 自适应（下限 64px）
    CROP_MARGIN_MIN = 64

    def __init__(self, video_path, sub_areas=[], det_params=None, crop_rect=None):
        self.video_path = video_path
        self.sub_areas = sub_areas
        # DB 后处理可调参数（thresh/box_thresh/unclip_ratio），None 走模型默认。
        # 残留二遍兜底用更敏感的参数：第一遍修完的残留多为笔画碎边，对比度低、
        # 检测得分低于默认 box_thresh=0.6 门槛，两遍同参数必然同样漏
        self.det_params = det_params or {}
        # 检测输入裁剪区（原图像素坐标 (x0,y0,x1,y1)，None=不裁剪）：
        # 显式传入（残留二遍传第一遍修复区域联合带）或缺省由 sub_areas 并集自动推导；
        # 真正生效前按首帧尺寸加 margin 并钳制（_resolve_crop_rect，惰性解析一次）。
        # 质量边界见 _resolve_crop_rect 实证结论：框级有 ±18px 差异，掩膜级在
        # selectionAsMask 开启时逐像素一致，故首遍自动裁剪仅该模式启用
        self.crop_rect = crop_rect
        self._active_crop = None
        self._crop_resolved = False
        # 上一次 find_subtitle_frame_no 中重复帧跳过的次数（日志/测试用）
        self.detect_dup_skipped = 0
        # 帧差分数：采样帧号 -> 与上一采样帧的平均灰度差（0-255标度）。
        # 供 AUTO 路由判定区间运动量用，不影响检测结果
        self.frame_diff_scores = {}
        self._prev_gray_small = None
        self._init_sample_step()

    def _init_sample_step(self):
        """设置采样间隔：配置了固定步长则用之，否则根据视频帧率自适应（每秒至少采样8帧）"""
        configured_step = config.subtitleDetectSampleStep.value
        if configured_step and configured_step > 0:
            self.SAMPLE_STEP = int(configured_step)
            return
        cap = cv2.VideoCapture(get_readable_path(self.video_path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        if fps >= 60:
            self.SAMPLE_STEP = 4
        elif fps >= 30:
            self.SAMPLE_STEP = 3
        else:
            self.SAMPLE_STEP = 2

    @cached_property
    def text_detector(self):
        import paddle
        paddle.disable_signal_handler()
        from paddleocr import TextDetection
        hardware_accelerator = HardwareAccelerator.instance()
        onnx_providers = hardware_accelerator.onnx_providers
        model_config = ModelConfig()
        # 字幕检测（PP-OCR 文本框检测）逐帧推理，是整个流程的主要耗时来源之一，
        # 优先放到 GPU 上跑。注意判定条件是 paddle 自身是否编译了 CUDA，
        # 而不是 torch 的 torch.cuda.is_available()：若装的是 CPU 版 paddlepaddle，
        # 传入 "gpu:0" 会直接报错，此时回退到 cpu。
        device = "gpu:0" if hardware_accelerator.has_paddle_cuda() else "cpu"
        return TextDetection(
            model_name=model_config.DET_MODEL_NAME,
            model_dir=model_config.DET_MODEL_DIR,
            device=device,
            enable_hpi=len(onnx_providers) > 0,
            **self.det_params,
        )

    # 检测模型内部的原生长边钳制值（inference.yml DetResizeForTest resize_long=960）。
    # 模型只在长边超过 960 时才缩小、不会放大（实测 720 输入不触发内部缩放且更快），
    # 因此长边 <= 960 的源始终按实际分辨率检测——预处理缩小是纯画质损失换不来收益
    NATIVE_LONG_SIDE = 960

    def _resolve_crop_rect(self, frame_h, frame_w):
        """按首帧尺寸确定检测裁剪区（已加 margin 并钳制到帧内）；无需裁剪返回 None。

        ⚠ 实证结论（test2.mp4 真实帧，见 .workbuddy 测试）：
        检出框级**不等价**——模型输出依赖输入尺寸/上下文，裁剪后检出框有
        ±2~18px 位移、偶发同一行重组（整帧 1 框 → 裁剪 2 框），且「先裁后缩」
        「先缩后裁」甚至原生纯切片都如此（非重采样相位问题）。
        但掩膜级（真正进修复的东西）在 selectionAsMask 开启时**逐像素一致**：
        掩膜 = 选区 ∪ 检出框，选区矩形主导，框级差异被完全吸收。
        因此首遍自动裁剪仅在该模式下启用；精确掩膜模式（selectionAsMask 关）
        的用户要的就是检测精度，回退整帧检测保持逐像素。
        残留二遍显式传入的联合带裁剪不受此限（兜底清理趟，保留的框本就与
        第一遍修复带重叠，20px+ 外扩足以吸收边缘位移）。
        """
        if not config.detectCropToArea.value:
            return None
        rect = self.crop_rect
        if rect is None:
            # 自动裁剪（首遍）：仅当选区整片擦除开启时（掩膜由选区主导，
            # 框级差异被吸收——掩膜级 0 像素差已验证）；精确掩膜模式回退整帧
            if not self.sub_areas or not config.selectionAsMask.value:
                return None
            # SUB_AREAS 口径 (ymin,ymax,xmin,xmax) → 并集转 (x0,y0,x1,y1)
            rect = (min(int(a[2]) for a in self.sub_areas), min(int(a[0]) for a in self.sub_areas),
                    max(int(a[3]) for a in self.sub_areas), max(int(a[1]) for a in self.sub_areas))
        margin = max(self.CROP_MARGIN_MIN, frame_h // 8)
        x0 = max(0, int(rect[0]) - margin)
        y0 = max(0, int(rect[1]) - margin)
        x1 = min(frame_w, int(rect[2]) + margin)
        y1 = min(frame_h, int(rect[3]) + margin)
        if x1 <= x0 or y1 <= y0:
            return None
        # 两个维度都覆盖≈全帧时裁剪无意义（GUI 未框选时选区=全帧的默认 flow）
        if (x1 - x0) >= frame_w * 0.98 and (y1 - y0) >= frame_h * 0.98:
            return None
        return (x0, y0, x1, y1)

    def _prepare_for_detection(self, img):
        """按配置的长边上限等比缩小待检测帧，返回 (缩小后的帧, 坐标还原倍数)。
        倍数为1表示未缩小；检测框坐标需乘该倍数映射回原分辨率。
        规则：
        - 实际长边 <= 960（模型原生上限）：始终按实际分辨率，不缩不放
        - 设定值 >= 960：等于无操作（模型内部本就会钳到 960）
        - 仅当实际长边 > 960 且 > 设定值时，等比缩小到设定值（可跳过模型内部缩放提速）"""
        limit = config.subtitleDetectProcessSize.value
        if not limit or limit >= self.NATIVE_LONG_SIDE:
            return img, 1.0
        h, w = img.shape[:2]
        long_side = max(h, w)
        if long_side <= limit or long_side <= self.NATIVE_LONG_SIDE:
            return img, 1.0
        scale = long_side / float(limit)
        if w >= h:
            proc_w, proc_h = limit, max(1, int(round(h / scale)))
        else:
            proc_h, proc_w = limit, max(1, int(round(w / scale)))
        return cv2.resize(img, (proc_w, proc_h), interpolation=cv2.INTER_AREA), scale

    def _update_frame_diff(self, frame_no, frame):
        """计算本采样帧与上一采样帧的平均灰度差（AUTO路由用，缩到160px长边，成本<0.5ms）"""
        h, w = frame.shape[:2]
        scale = 160.0 / max(h, w)
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                           (max(1, int(w * scale)), max(1, int(h * scale))),
                           interpolation=cv2.INTER_AREA)
        if self._prev_gray_small is not None and self._prev_gray_small.shape == small.shape:
            self.frame_diff_scores[frame_no] = float(np.mean(cv2.absdiff(self._prev_gray_small, small)))
        self._prev_gray_small = small

    def detect_subtitle(self, img, frame_no=None):
        temp_list = []
        # 检测输入裁剪（惰性解析一次）：裁到字幕带+margin，检出框加偏移映射回原分辨率
        if not self._crop_resolved:
            h, w = img.shape[:2]
            self._active_crop = self._resolve_crop_rect(h, w)
            self._crop_resolved = True
        offset_x = offset_y = 0
        if self._active_crop is not None:
            x0, y0, x1, y1 = self._active_crop
            img = img[y0:y1, x0:x1]
            offset_x, offset_y = x0, y0
        proc_img, coord_scale = self._prepare_for_detection(img)
        results = self.text_detector.predict(proc_img)
        sub_areas = self.sub_areas
        has_areas = sub_areas is not None and len(sub_areas) > 0
        if has_areas:
            # 选框时间过滤：6 元组选框 (…,t0,t1) 只在 [t0,t1) 帧内生效（无时间=全程；
            # 帧号口径与 AB 区块一致）。该帧所有选框都不在时间窗内 → 不送检测、无检出
            #（⇒ 带时间选框的场景里，窗外帧整帧跳过 OCR，检测随之提速）
            active_areas = [a for a in sub_areas if area_active_at(a, frame_no)]
            if len(active_areas) == 0:
                return temp_list
        else:
            active_areas = sub_areas
        for res in results:
            dt_polys = res['dt_polys']
            if dt_polys is None or len(dt_polys) == 0:
                continue
            coordinate_list = get_coordinates(dt_polys.tolist())
            if coord_scale != 1.0:
                # 降采样检测的框映射回原分辨率坐标
                coordinate_list = [(int(round(xmin * coord_scale)), int(round(xmax * coord_scale)),
                                    int(round(ymin * coord_scale)), int(round(ymax * coord_scale)))
                                   for xmin, xmax, ymin, ymax in coordinate_list]
            if offset_x or offset_y:
                # 裁剪检测的框平移回原分辨率坐标
                coordinate_list = [(xmin + offset_x, xmax + offset_x, ymin + offset_y, ymax + offset_y)
                                   for xmin, xmax, ymin, ymax in coordinate_list]
            if not coordinate_list:
                continue
            if not has_areas:
                temp_list.extend(coordinate_list)
            elif len(active_areas) == 1:
                # 单区域快速路径（最常见场景）
                s_ymin, s_ymax, s_xmin, s_xmax = active_areas[0][:4]
                for xmin, xmax, ymin, ymax in coordinate_list:
                    if s_xmin <= xmin and xmax <= s_xmax and s_ymin <= ymin and ymax <= s_ymax:
                        temp_list.append((xmin, xmax, ymin, ymax))
            else:
                for xmin, xmax, ymin, ymax in coordinate_list:
                    for s_area in active_areas:
                        s_ymin, s_ymax, s_xmin, s_xmax = s_area[:4]
                        if s_xmin <= xmin and xmax <= s_xmax and s_ymin <= ymin and ymax <= s_ymax:
                            temp_list.append((xmin, xmax, ymin, ymax))
                            break
        return temp_list

    def find_subtitle_frame_no(self, sub_remover=None, frame_start=None, frame_end=None):
        """采样检测帧窗口内的字幕。

        frame_start/frame_end 显式指定窗口（流水线分块检测用）；
        未指定时沿用 sub_remover.frame_start/frame_end（原行为）。
        帧号体系：窗口为 1-based 的 (frame_start, frame_end]。
        """
        video_cap = cv2.VideoCapture(get_readable_path(self.video_path))
        frame_count = video_cap.get(cv2.CAP_PROP_FRAME_COUNT)
        # 分段帧范围（与 SubtitleRemover.frame_start/frame_end 对齐）：
        # 检测只在段范围内进行，帧号保持全局绝对值（掩膜/路由按全局帧号一致）
        if frame_start is None:
            frame_start = int(getattr(sub_remover, 'frame_start', 0) or 0) if sub_remover else 0
        if frame_end is None:
            frame_end = int(getattr(sub_remover, 'frame_end', 0) or 0) if sub_remover else 0
        frame_start = int(frame_start or 0)
        frame_end = int(frame_end or 0)
        if frame_end <= 0:
            frame_end = int(frame_count)
        seg_total = max(1, frame_end - frame_start)
        if frame_start > 0:
            video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_start)
        tbar = tqdm(total=seg_total, unit='frame', position=0, file=sys.__stdout__, desc='Subtitle Finding')
        current_frame_no = frame_start
        # 阶段1：采样检测，仅对每隔 sample_step 帧执行 OCR；
        # 非采样帧与AB区间外帧只用 grab() 跳过（不解码为BGR，实测比 read() 快约8倍）
        sampled_results = {}  # frame_no -> temp_list
        prev_list = None      # 上一采样帧的检出（重复帧复用）
        dup_skipped = 0       # 重复帧跳过 OCR 的次数
        if sub_remover:
            sub_remover.append_output(tr['Main']['ProcessingStartFindingSubtitles'])
        while video_cap.isOpened() and current_frame_no < frame_end:
            # current_frame_no 即下一帧的0-based序号；采样条件与原逻辑 (1-based序号-1)%step==0 等价
            in_ab_section = is_frame_number_in_ab_sections(current_frame_no, sub_remover.ab_sections) if sub_remover else True
            need_ocr = in_ab_section and (self.SAMPLE_STEP <= 1 or current_frame_no % self.SAMPLE_STEP == 0)
            if need_ocr:
                ret, frame = video_cap.read()
                # 如果读取视频帧失败（视频读到最后一帧）
                if not ret:
                    break
                current_frame_no += 1
                self._update_frame_diff(current_frame_no, frame)
                # 重复帧跳过：与上一采样帧几乎相同（160px 长边灰度均值差 < 阈值，
                # 约等于压缩噪声上限）时 OCR 结果必然相同，直接复用上一采样帧的
                # 检出，省一次检测推理——相同像素 → 相同检测，零画质影响。
                # 静止画面/录屏/定格场景成片命中；真实运动（字幕动画/镜头晃动/
                # 胶片颗粒）帧差通常 > 3，不会误跳。置 DETECT_DUP_THRESHOLD=0 禁用
                diff = self.frame_diff_scores.get(current_frame_no)
                if prev_list is not None and diff is not None and diff < DETECT_DUP_THRESHOLD:
                    temp_list = list(prev_list)
                    dup_skipped += 1
                else:
                    temp_list = self.detect_subtitle(frame, current_frame_no)
                prev_list = temp_list
                if len(temp_list) > 0:
                    sampled_results[current_frame_no] = temp_list
            else:
                if not video_cap.grab():
                    break
                current_frame_no += 1
            tbar.update(1)
            if sub_remover and seg_total > 0:
                sub_remover.progress_total = (100 * float(current_frame_no - frame_start) / float(seg_total)) // 2
        video_cap.release()
        self.detect_dup_skipped = dup_skipped
        if sub_remover and dup_skipped:
            sub_remover.append_output(
                f'[Skip] 检测重复帧跳过 {dup_skipped} 帧（与相邻采样帧几乎相同，复用检出）')
        # 阶段2：插值填充 — 两个采样帧之间都有字幕时，中间帧也标记为有字幕
        subtitle_frame_no_box_dict = {}
        detected_nos = sorted(sampled_results.keys())
        max_gap = self.SAMPLE_STEP * 2
        for f, next_f in zip(detected_nos, detected_nos[1:]):
            subtitle_frame_no_box_dict[f] = sampled_results[f]
            if next_f - f <= max_gap:
                fill_mask = sampled_results[f]
                for fill_f in range(f + 1, next_f):
                    subtitle_frame_no_box_dict[fill_f] = fill_mask
        # 添加最后一个检测帧
        if detected_nos:
            subtitle_frame_no_box_dict[detected_nos[-1]] = sampled_results[detected_nos[-1]]
        subtitle_frame_no_box_dict = self.unify_regions(subtitle_frame_no_box_dict)
        if sub_remover:
            sub_remover.append_output(tr['Main']['FinishedFindingSubtitles'])
        new_subtitle_frame_no_box_dict = dict()
        for key in subtitle_frame_no_box_dict.keys():
            if len(subtitle_frame_no_box_dict[key]) > 0:
                new_subtitle_frame_no_box_dict[key] = subtitle_frame_no_box_dict[key]
        return new_subtitle_frame_no_box_dict

    @staticmethod
    def split_range_by_scene(intervals, points):
        # 确保离散值列表是有序的
        points.sort()
        # 用于存储结果区间的列表
        result_intervals = []
        # 遍历区间
        for start, end in intervals:
            # 在当前区间内的点
            current_points = [p for p in points if start <= p <= end]

            # 遍历当前区间内的离散点
            for p in current_points:
                # 如果当前离散点不是区间的起始点，添加从区间开始到离散点前一个数字的区间
                if start < p:
                    result_intervals.append((start, p - 1))
                # 更新区间开始为当前离散点
                start = p
            # 添加从最后一个离散点或区间开始到区间结束的区间
            result_intervals.append((start, end))
        # 输出结果
        return result_intervals

    @staticmethod
    def get_scene_div_frame_no(v_path):
        """
        获取发生场景切换的帧号
        """
        scene_div_frame_no_list = []
        scene_list = scene_detect(v_path, ContentDetector())
        for scene in scene_list:
            start, end = scene
            if start.frame_num == 0:
                pass
            else:
                scene_div_frame_no_list.append(start.frame_num + 1)
        return scene_div_frame_no_list

    @staticmethod
    def are_similar(region1, region2):
        """判断两个区域是否相似。"""
        xmin1, xmax1, ymin1, ymax1 = region1
        xmin2, xmax2, ymin2, ymax2 = region2

        return abs(xmin1 - xmin2) <= config.subtitleAreaPixelToleranceXPixel.value and abs(xmax1 - xmax2) <= config.subtitleAreaPixelToleranceXPixel.value and \
            abs(ymin1 - ymin2) <= config.subtitleAreaPixelToleranceYPixel.value and abs(ymax1 - ymax2) <= config.subtitleAreaPixelToleranceYPixel.value

    def unify_regions(self, raw_regions):
        """将连续相似的区域统一，保持列表结构。"""
        if len(raw_regions) > 0:
            keys = sorted(raw_regions.keys())  # 对键进行排序以确保它们是连续的
            unified_regions = {}

            # 初始化
            last_key = keys[0]
            unify_value_map = {last_key: raw_regions[last_key]}

            for key in keys[1:]:
                current_regions = raw_regions[key]

                # 新增一个列表来存放匹配过的标准区间
                new_unify_values = []

                for idx, region in enumerate(current_regions):
                    last_standard_region = unify_value_map[last_key][idx] if idx < len(unify_value_map[last_key]) else None

                    # 如果当前的区间与前一个键的对应区间相似，我们统一它们
                    if last_standard_region and self.are_similar(region, last_standard_region):
                        new_unify_values.append(last_standard_region)
                    else:
                        new_unify_values.append(region)

                # 更新unify_value_map为最新的区间值
                unify_value_map[key] = new_unify_values
                last_key = key

            # 将最终统一后的结果传递给unified_regions
            for key in keys:
                unified_regions[key] = unify_value_map[key]
            return unified_regions
        else:
            return raw_regions

    @staticmethod
    def find_continuous_ranges(subtitle_frame_no_box_dict):
        """
        获取字幕出现的起始帧号与结束帧号
        """
        numbers = sorted(list(subtitle_frame_no_box_dict.keys()))
        ranges = []
        start = numbers[0]  # 初始区间开始值

        for i in range(1, len(numbers)):
            # 如果当前数字与前一个数字间隔超过1，
            # 则上一个区间结束，记录当前区间的开始与结束
            if numbers[i] - numbers[i - 1] != 1:
                end = numbers[i - 1]  # 则该数字是当前连续区间的终点
                ranges.append((start, end))
                start = numbers[i]  # 开始下一个连续区间
        # 添加最后一个区间
        ranges.append((start, numbers[-1]))
        return ranges

    @staticmethod
    def find_continuous_ranges_with_same_mask(subtitle_frame_no_box_dict):
        numbers = sorted(list(subtitle_frame_no_box_dict.keys()))
        ranges = []
        start = numbers[0]  # 初始区间开始值
        for i in range(1, len(numbers)):
            # 如果当前帧号与前一个帧号间隔超过1，
            # 则上一个区间结束，记录当前区间的开始与结束
            if numbers[i] - numbers[i - 1] != 1:
                end = numbers[i - 1]  # 则该数字是当前连续区间的终点
                ranges.append((start, end))
                start = numbers[i]  # 开始下一个连续区间
            # 如果当前帧号与前一个帧号间隔为1，且当前帧号对应的坐标点与上一帧号对应的坐标点不一致
            # 记录当前区间的开始与结束
            if numbers[i] - numbers[i - 1] == 1:
                if subtitle_frame_no_box_dict[numbers[i]] != subtitle_frame_no_box_dict[numbers[i - 1]]:
                    end = numbers[i - 1]  # 则该数字是当前连续区间的终点
                    ranges.append((start, end))
                    start = numbers[i]  # 开始下一个连续区间
        # 添加最后一个区间
        ranges.append((start, numbers[-1]))
        return ranges

    @staticmethod
    def filter_and_merge_intervals(intervals, target_length):
        """
        合并传入的字幕起始区间，确保区间大小最低为STTN_REFERENCE_LENGTH
        复杂度 O(n log n)
        """
        if not intervals:
            return []
        intervals = sorted(intervals, key=lambda x: x[0])
        # 一次遍历：扩展单点区间，利用排序后的相邻关系 O(n)
        expanded = []
        for i, (start, end) in enumerate(intervals):
            if start == end:  # 单点区间
                prev_end = expanded[-1][1] if expanded else float('-inf')
                next_start = intervals[i + 1][0] if i + 1 < len(intervals) else float('inf')
                half = (target_length - 1) // 2
                new_start = max(start - half, prev_end + 1)
                new_end = min(start + half, next_start - 1)
                if new_end < new_start:
                    new_start, new_end = start, start
                expanded.append((new_start, new_end))
            else:
                expanded.append((start, end))
        # 一次遍历：合并重叠或相邻的短区间 O(n)
        merged = [expanded[0]]
        for start, end in expanded[1:]:
            last_start, last_end = merged[-1]
            last_len = last_end - last_start + 1
            cur_len = end - start + 1
            if (start <= last_end or start == last_end + 1) and (cur_len < target_length or last_len < target_length):
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        return merged
