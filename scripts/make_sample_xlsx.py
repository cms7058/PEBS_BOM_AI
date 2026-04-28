"""Generate a small sample BOM spreadsheet for smoke testing.

Usage:
  python scripts/make_sample_xlsx.py > /tmp/sample.xlsx
"""

import sys
from openpyxl import Workbook

rows = [
    ["层级", "图号", "零件名称", "数量", "单位", "材料", "备注"],
    [0, "A-001", "电动滑板车总成", 1, "台", "", "整机"],
    [1, "A-001-01", "车架组件", 1, "个", "铝合金 6061", ""],
    [2, "A-001-01-1", "主梁", 1, "个", "铝合金 6061", ""],
    [2, "A-001-01-2", "立管", 1, "个", "铝合金 6061", ""],
    [1, "A-001-02", "电机组件", 1, "个", "", ""],
    [2, "A-001-02-1", "无刷直流电机 350W", 1, "个", "", "外购"],
    [2, "A-001-02-2", "电机控制器", 1, "个", "", "外购"],
    [1, "A-001-03", "电池包 36V 10Ah", 1, "个", "18650 锂电芯", "外购"],
    [1, "A-001-04", "轮组", 2, "个", "PU", ""],
    [1, "A-001-05", "刹车总成", 1, "套", "", ""],
    [1, "S-M4-10", "螺钉 M4×10", 24, "个", "不锈钢 304", "标准件"],
]

wb = Workbook()
ws = wb.active
for r in rows:
    ws.append(r)
wb.save(sys.stdout.buffer)
