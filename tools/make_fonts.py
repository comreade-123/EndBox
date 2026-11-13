# -*- coding: utf-8 -*-
"""Build-time helper: slice static instances out of the Saira variable font.

EndBox needs the *wide* Saira look (font-stretch 112–125 %) for labels and
numerals. Qt does not expose the variable axes reliably on Windows, so we
generate static instances once and ship them in assets/fonts.

Run once (needs fonttools; not a runtime dependency):
    pip install fonttools
    python tools/make_fonts.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "assets", "fonts", "Saira.ttf")
OUT = os.path.join(ROOT, "assets", "fonts")

WEIGHTS = {
    400: ("Regular", "Saira Regular", 400),
    600: ("SemiBold", "Saira SemiBold", 600),
    700: ("Bold", "Saira Bold", 700),
    800: ("ExtraBold", "Saira ExtraBold", 800),
}


def set_names(font, family: str, style: str, weight: int) -> None:
    name = font["name"]
    full = f"{family} {style}"
    ps = full.replace(" ", "")
    for nid, val in ((1, family), (2, style), (3, full), (4, full), (6, ps), (16, family), (17, style)):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    os2 = font["OS/2"]
    os2.usWeightClass = weight
    os2.fsSelection = (os2.fsSelection & ~0x21) | (0x20 if weight >= 700 else 0x40)
    font["head"].macStyle = 1 if weight >= 700 else 0


def main() -> int:
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    for width, suffix in ((125, "Saira Wide"), (112, "Saira Field")):
        for weight, (style, _old, wght) in WEIGHTS.items():
            var = TTFont(SRC)
            inst = instantiateVariableFont(
                var, {"wdth": float(width), "wght": float(wght)}, updateFontNames=False
            )
            new_style = style if width == 125 else style
            set_names(inst, suffix, new_style, weight)
            out = os.path.join(OUT, f"{suffix.replace(' ', '')}-{style}.ttf")
            inst.save(out)
            print("wrote", os.path.basename(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
