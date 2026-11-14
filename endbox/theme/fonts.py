# -*- coding: utf-8 -*-
"""字体：宽体 Saira（Latin）+ 系统中文字体。

Saira 的宽体实例由 tools/make_fonts.py 从可变字体切出，许可证随 assets/fonts 分发。
中文用 Windows 自带的微软雅黑；没有 Saira 时退回系统界面字体。
"""
from __future__ import annotations

import glob
import os

from PyQt6.QtGui import QFont, QFontDatabase

from endbox import config
from endbox.core.log import get_logger

log = get_logger("theme.fonts")

FONT_UI = "Saira"
FONT_WIDE = "Saira Wide"
FONT_FIELD = "Saira Field"
FONT_CJK = "Microsoft YaHei UI"
FONT_MONO = "Cascadia Mono"

_loaded = False
_families: set[str] = set()


def load_fonts() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    font_dir = config.ASSETS / "fonts"
    patterns = ["Saira*.ttf", "Saira*.otf"]
    for pattern in patterns:
        for path in sorted(glob.glob(str(font_dir / pattern))):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                _families.update(QFontDatabase.applicationFontFamilies(fid))
            else:
                log.warning("font load failed: %s", os.path.basename(path))
    log.info("fonts loaded: %s", sorted(_families))


def _family(wide: bool = False, field: bool = False) -> str:
    if field and FONT_FIELD in _families:
        return FONT_FIELD
    if wide and FONT_WIDE in _families:
        return FONT_WIDE
    if FONT_UI in _families:
        return FONT_UI
    return "Segoe UI"


def ui_families(wide: bool = False, field: bool = False) -> list[str]:
    """Latin 用 Saira，逐字回退到中文字体。"""
    return [_family(wide=wide, field=field), FONT_CJK, "Segoe UI"]


def font(
    size: int,
    *,
    weight: QFont.Weight = QFont.Weight.Normal,
    wide: bool = False,
    field: bool = False,
    italic: bool = False,
    letter_spacing: float = 0.0,
    cjk_first: bool = False,
) -> QFont:
    f = QFont()
    if cjk_first:
        f.setFamilies([FONT_CJK] + ui_families(wide=wide, field=field))
    else:
        f.setFamilies(ui_families(wide=wide, field=field))
    f.setPixelSize(max(1, int(size)))
    f.setWeight(weight)
    f.setItalic(italic)
    if letter_spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(letter_spacing))
    return f


def eyebrow_font(size: int = 11) -> QFont:
    """宽体大写小字，字距 0.14em。"""
    return font(size, weight=QFont.Weight.DemiBold, wide=True, letter_spacing=size * 0.14)


def field_font(size: int = 13) -> QFont:
    return font(size, weight=QFont.Weight.Medium, field=True, letter_spacing=size * 0.04)


def display_font(size: int) -> QFont:
    return font(size, weight=QFont.Weight.ExtraBold, wide=True)


def number_font(size: int) -> QFont:
    """数字：宽体等宽数字。"""
    f = font(size, weight=QFont.Weight.Bold, wide=True)
    f.setFeature(QFont.Tag("tnum"), 1)
    return f


def mono_font(size: int = 12) -> QFont:
    f = QFont(FONT_MONO)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(size)
    return f
