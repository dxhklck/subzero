import os
import sys
import ctypes
import subprocess

import cv2
import numpy as np
from fsplit.filesplit import Filesplit

# GUI 子系统（pythonw）下调起控制台程序会弹黑窗，Windows 下一律加此标志
SUBPROCESS_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

video_extensions = {
    '.mp4', '.m4a', '.m4v', '.f4v', '.f4a', '.m4b', '.m4r', '.f4b', '.mov',
    '.3gp', '.3gp2', '.3g2', '.3gpp', '.3gpp2', '.ogg', '.oga', '.ogv', '.ogx',
    '.wmv', '.wma', '.asf', '.webm', '.flv', '.avi', '.gifv', '.mkv', '.rm',
    '.rmvb', '.vob', '.dvd', '.mpg', '.mpeg', '.mp2', '.mpe', '.mpv', '.mpg',
    '.mpeg', '.m2v', '.svi', '.3gp', '.mxf', '.roq', '.nsv', '.flv', '.f4v',
    '.f4p', '.f4a', '.f4b'
}

image_extensions = {
    '.jpg', '.jpeg', '.jpe', '.jif', '.jfif', '.jfi', '.png', '.gif',
    '.webp', '.tiff', '.tif', '.psd', '.raw', '.arw', '.cr2', '.nrw',
    '.k25', '.bmp', '.dib', '.heif', '.heic', '.ind', '.indd', '.indt',
    '.jp2', '.j2k', '.jpf', '.jpx', '.jpm', '.mj2', '.svg', '.svgz',
    '.ai', '.eps', '.ico'
}


def is_video_file(filename):
    return os.path.splitext(filename)[-1].lower() in video_extensions


def is_image_file(filename):
    return os.path.splitext(filename)[-1].lower() in image_extensions


def is_video_or_image(filename):
    file_extension = os.path.splitext(filename)[-1].lower()
    # 检查扩展名是否在定义的视频或图片文件后缀集合中；路径不存在也判 False（提前报错）
    return (os.path.exists(filename)
            and (file_extension in video_extensions or file_extension in image_extensions))

def merge_big_file_if_not_exists(dir, file, man_filename = None):
    if file not in os.listdir(dir):
        fs = Filesplit()
        if man_filename is not None:
            fs.man_filename = man_filename
        fs.merge(input_dir=dir)

def get_readable_path(path):
    if sys.platform != 'win32':
        return path
    buf = ctypes.create_unicode_buffer(4096)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 4096)
    short = buf.value if n > 0 else ''
    # 失败（文件不存在/8.3 短名不可用/路径非法）时回退原路径。
    # 旧实现无视返回值直接 buf.value——失败时缓冲区为空，返回 '' 喂给
    # cv2.VideoCapture('') 踩 !_filename.empty() 断言，调用方 except 后
    # 静默失败（快照里源文件被移动的任务 → 选中即预览空白无提示）。
    return short if short else path

def read_image(path):
    try:
        if os.path.getsize(path) > 100*1024*1024: # 100MB
            print(f"Image {path} is too large, skip")
            return None
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), -1)
        if img is not None and img.shape[-1] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img
    except (OSError, ValueError, cv2.error):
        # 文件缺失/无权限/损坏：返回 None，由调用方按「打不开」兜底
        return None