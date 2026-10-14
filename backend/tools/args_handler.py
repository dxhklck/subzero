import argparse
from enum import Enum

from .constant import InpaintMode


def _sub_area(value):
    """解析 --sub-area 的 "ymin,ymax,xmin,xmax"（原图像素坐标，与 GUI SUB_AREAS 语义一致）"""
    parts = [p.strip() for p in value.split(',')]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"invalid sub-area '{value}': expected 'ymin,ymax,xmin,xmax' "
            f"(4 comma-separated integers), got {len(parts)} value(s)")
    try:
        coords = tuple(int(p) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid sub-area '{value}': coordinates must be integers")
    return coords


def _time_range(value):
    """解析 --time-range 的 "start-end"（秒，支持小数）"""
    parts = value.split('-')
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"invalid time-range '{value}': expected 'start-end' in seconds, e.g. '10.5-30'")
    try:
        start, end = float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid time-range '{value}': start/end must be numbers in seconds")
    if start < 0:
        raise argparse.ArgumentTypeError(
            f"invalid time-range '{value}': start must be >= 0")
    if start >= end:
        raise argparse.ArgumentTypeError(
            f"invalid time-range '{value}': start must be less than end")
    return (start, end)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Video Subtitle Remover Command Line Tool"
    )
    parser.add_argument(
        "--input", "-i", required=True, type=str,
        help="Input video file path"
    )
    parser.add_argument(
        "--output", "-o", required=False, type=str, default=None,
        help="Output video file path (optional)"
    )
    parser.add_argument(
        "--subtitle-area-coords", "-c", action="append", nargs=4, type=int, metavar=("YMIN", "YMAX", "XMIN", "XMAX"),
        help="Subtitle area coordinates (ymin ymax xmin xmax). Can be specified multiple times for multiple areas."
    )
    parser.add_argument(
        "--inpaint-mode", type=str, default=None,
        choices=[mode.name.lower().replace('_','-') for mode in InpaintMode],
        help="Inpaint mode. If omitted, falls back to the mode selected in the GUI settings (config.json)"
    )
    parser.add_argument(
        "--sub-area", action="append", type=_sub_area, metavar="YMIN,YMAX,XMIN,XMAX",
        help="Subtitle area coordinates 'ymin,ymax,xmin,xmax' (original pixel coords). "
             "Can be specified multiple times for multiple areas. "
             "Use the equals form (--sub-area=...) when any coordinate is negative."
    )
    parser.add_argument(
        "--time-range", type=_time_range, metavar="START-END",
        help="Time range to process, in seconds (decimals allowed), e.g. '10.5-30'. "
             "Only frames within the range are processed and written to the output."
    )
    parser.add_argument(
        "--residue-only", action="store_true",
        help="Residue-only second pass: the input is already a first-pass result; "
             "skip the main repair loop and only run residue detection + second repair "
             "on the input, then write the output (with audio mux)."
    )
    parser.add_argument(
        "--frame-start", type=int, default=None,
        help="0-based start frame (inclusive) for segmented/resumable processing; default 0"
    )
    parser.add_argument(
        "--frame-end", type=int, default=None,
        help="0-based end frame (exclusive) for segmented/resumable processing; default video end"
    )
    parser.add_argument(
        "--frame-write-start", type=int, default=None,
        help="0-based first frame to write (inclusive); frames before it are processed as "
             "context only and not written (segment overlap). Default: same as --frame-start"
    )
    args = parser.parse_args()
    if args.inpaint_mode is not None:
        args.inpaint_mode = InpaintMode[args.inpaint_mode.replace('-','_').upper()]
    if args.subtitle_area_coords is None:
        args.subtitle_area_coords = []
    # --sub-area 与 -c/--subtitle-area-coords 汇合到同一列表（GUI SUB_AREAS 语义：
    # [(ymin, ymax, xmin, xmax), ...]，原图像素坐标）
    if args.sub_area:
        args.subtitle_area_coords = args.subtitle_area_coords + args.sub_area
    # --time-range 与 --frame-start/--frame-end 都定义处理范围，同时给出会歧义
    if args.time_range is not None and (args.frame_start is not None or args.frame_end is not None):
        parser.error("--time-range conflicts with --frame-start/--frame-end "
                     "(both define the processing frame range)")
    # --residue-only 对整段输入执行二修，不支持再限定帧范围
    if args.residue_only and (args.time_range is not None or args.frame_start is not None
                              or args.frame_end is not None or args.frame_write_start is not None):
        parser.error("--residue-only processes the whole input and cannot be combined with "
                     "--time-range/--frame-start/--frame-end/--frame-write-start")
    return args