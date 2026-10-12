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
            (x1, y1) = int(i[0][0]), int(i[0][1])
            (x2, y2) = int(i[1][0]), int(i[1][1])
            (x3, y3) = int(i[2][0]), int(i[2][1])
            (x4, y4) = int(i[3][0]), int(i[3][1])
            # 取四边形外接矩形（包围盒）：倾斜/旋转文本框若取内接矩形会小于实际文字，
            # 导致 inpaint 掩膜欠覆盖、擦除后残留文字边缘
            xmin = min(x1, x2, x3, x4)
            xmax = max(x1, x2, x3, x4)
            ymin = min(y1, y2, y3, y4)
            ymax = max(y1, y2, y3, y4)
            coordinate_list.append((xmin, xmax, ymin, ymax))
    return coordinate_list
