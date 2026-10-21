def get_coordinates(dt_box):
    """
    从返回的检测框中获取坐标
    :param dt_box 检测框返回结果
    :return list 坐标点列表
    """
    coordinate_list = list()
    if isinstance(dt_box, list):
        for i in dt_box:
            i = list(i)
            # 防御退化多边形：上游（PaddleOCR）正常恒返回 4 点，但异常/裁剪边缘
            # 场景可能给出 <4 点，直接取 i[3] 会 IndexError。这里用 min/max 遍历
            # 所有点求外接矩形，<4 点跳过，>4 点也一并正确兜住。
            if len(i) < 4:
                continue
            xs = [int(p[0]) for p in i]
            ys = [int(p[1]) for p in i]
            # 取多边形外接矩形（包围盒）：倾斜/旋转文本框若取内接矩形会小于实际文字，
            # 导致 inpaint 掩膜欠覆盖、擦除后残留文字边缘
            xmin = min(xs)
            xmax = max(xs)
            ymin = min(ys)
            ymax = max(ys)
            coordinate_list.append((xmin, xmax, ymin, ymax))
    return coordinate_list
