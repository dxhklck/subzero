import argparse
from enum import Enum

from .constant import InpaintMode

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
    return args