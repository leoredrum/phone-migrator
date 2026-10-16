#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成应用图标：pc/assets/icon.png(1024) / icon.ico(Windows) / icon.icns(macOS)。

依赖：pip install pillow；.icns 需要 macOS 自带的 iconutil。
运行：python3 scripts/make_icon.py（在仓库根目录）
"""
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "pc", "assets")

S = 1024
GRAD_TOP = (30, 136, 229)      # 蓝
GRAD_BOTTOM = (0, 200, 167)    # 青绿
SCREEN = (18, 110, 160)        # 屏幕色（背景中间调偏深）
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), \
           int(a[2] + (b[2] - a[2]) * t)


def build_master():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 垂直渐变 + macOS squircle 形状的圆角矩形蒙版
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(GRAD_TOP, GRAD_BOTTOM, y / (S - 1)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [36, 36, S - 36, S - 36], radius=236, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # 旧手机（左上，半透明白，屏幕朝前）
    ox0, oy0, ox1, oy1 = 108, 236, 436, 668
    d.rounded_rectangle([ox0, oy0, ox1, oy1], radius=66, fill=WHITE + (100,))
    d.rounded_rectangle([ox0 + 34, oy0 + 58, ox1 - 34, oy1 - 40],
                        radius=28, fill=SCREEN + (120,))
    d.rounded_rectangle([ox0 + 130, oy0 + 22, ox1 - 130, oy0 + 30],
                        radius=4, fill=WHITE + (170,))          # 听筒

    # 新手机（右下，实心白）
    nx0, ny0, nx1, ny1 = 476, 300, 916, 812
    d.rounded_rectangle([nx0, ny0, nx1, ny1], radius=76, fill=WHITE + (255,))
    d.rounded_rectangle([nx0 + 36, ny0 + 62, nx1 - 36, ny1 - 42],
                        radius=30, fill=SCREEN + (255,))         # 屏幕
    d.rounded_rectangle([nx0 + 168, ny0 + 26, nx1 - 168, ny0 + 34],
                        radius=4, fill=SCREEN + (200,))          # 听筒
    d.ellipse([(nx0 + nx1) // 2 - 16, ny1 - 32, (nx0 + nx1) // 2 + 16, ny1],
              fill=SCREEN + (200,))                              # home 键

    # 传送箭头（旧 → 新）：先画放大一圈的深色底再画白色箭头，
    # 避免白色箭头与白色机身重叠时融为一体
    arrow = [(356, 452), (520, 452), (520, 372), (668, 512),
             (520, 652), (520, 572), (356, 572)]

    def scale_poly(pts, k):
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        return [((x - cx) * k + cx, (y - cy) * k + cy) for x, y in pts]

    d.polygon(scale_poly(arrow, 1.20), fill=SCREEN + (255,))
    d.polygon(arrow, fill=WHITE + (255,))

    return img


def main():
    os.makedirs(ASSETS, exist_ok=True)
    master = build_master()
    master.save(os.path.join(ASSETS, "icon.png"))

    # Windows .ico
    master.save(os.path.join(ASSETS, "icon.ico"),
                sizes=[(256, 256), (128, 128), (64, 64),
                       (48, 48), (32, 32), (16, 16)])

    # macOS .icns（iconset 目录 + iconutil）
    iconset = os.path.join(ASSETS, "icon.iconset")
    if os.path.isdir(iconset):
        shutil.rmtree(iconset)
    os.makedirs(iconset)
    spec = {"icon_16x16.png": 16, "icon_16x16@2x.png": 32,
            "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
            "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
            "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
            "icon_512x512.png": 512, "icon_512x512@2x.png": 1024}
    for name, px in spec.items():
        master.resize((px, px), Image.LANCZOS).save(
            os.path.join(iconset, name))
    try:
        subprocess.run(["iconutil", "-c", "icns", iconset,
                        "-o", os.path.join(ASSETS, "icon.icns")], check=True)
    except Exception as e:
        print(f".icns 生成失败（仅 macOS 支持 iconutil）：{e}", file=sys.stderr)

    for f in sorted(os.listdir(ASSETS)):
        p = os.path.join(ASSETS, f)
        print(f"{f}: {os.path.getsize(p) / 1024:.0f} KB")
    print("完成 →", ASSETS)


if __name__ == "__main__":
    main()
