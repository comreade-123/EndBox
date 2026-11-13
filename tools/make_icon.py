# -*- coding: utf-8 -*-
"""把用户提供的 icon.png 处理成透明背景的应用图标。

    python tools/make_icon.py [源文件]

处理：
  1) 从图片四周洪水填充接近白色的背景 → 透明（角色内部的白色不会被误删）；
  2) 边缘做一次轻度 alpha 羽化，避免白边；
  3) 裁到内容边界、补成正方形、生成 512 PNG 与多尺寸 ICO。

输出：assets/branding/endbox.png / endbox.ico（透明背景）
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "icon.png"
OUT_DIR = ROOT / "assets" / "branding"
WHITE = 238          # 背景判定阈值（>= 视为白）
FEATHER = 1.2        # alpha 羽化半径


def strip_white(im: Image.Image, threshold: int = WHITE) -> Image.Image:
    """从四周洪水填充白色背景为透明；返回 RGBA。"""
    rgba = im.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    visited = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def is_background(x: int, y: int) -> bool:
        r, g, b, a = pixels[x, y]
        return a > 0 and r >= threshold and g >= threshold and b >= threshold

    for x in range(width):
        for y in (0, height - 1):
            if is_background(x, y) and not visited[y * width + x]:
                visited[y * width + x] = 1
                queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            if is_background(x, y) and not visited[y * width + x]:
                visited[y * width + x] = 1
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        pixels[x, y] = (255, 255, 255, 0)
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height and not visited[ny * width + nx]:
                if is_background(nx, ny):
                    visited[ny * width + nx] = 1
                    queue.append((nx, ny))

    # 对紧贴透明区域的浅色像素做半透明羽化，消掉白边
    alpha = rgba.getchannel("A").filter(ImageFilter.GaussianBlur(FEATHER))
    rgba.putalpha(alpha)
    return rgba


def crop_content(im: Image.Image) -> Image.Image:
    bbox = im.getbbox()
    if not bbox:
        return im
    left, top, right, bottom = bbox
    pad = int(max(right - left, bottom - top) * 0.06)
    return im.crop(
        (max(0, left - pad), max(0, top - pad), min(im.width, right + pad), min(im.height, bottom + pad))
    )


def square(im: Image.Image) -> Image.Image:
    side = max(im.width, im.height)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    canvas.alpha_composite(im, ((side - im.width) // 2, (side - im.height) // 2))
    return canvas


def main() -> int:
    source = Image.open(SRC)
    transparent = square(crop_content(strip_white(source)))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    big = transparent.resize((512, 512), Image.LANCZOS)
    big.save(OUT_DIR / "endbox.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    transparent.resize((256, 256), Image.LANCZOS).save(
        OUT_DIR / "endbox.ico", sizes=sizes
    )
    print(
        "wrote", OUT_DIR / "endbox.png", OUT_DIR / "endbox.ico",
        "| source", source.size, "->", big.size,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
