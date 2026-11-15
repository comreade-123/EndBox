# -*- coding: utf-8 -*-
"""渲染覆盖层截图（运行提示条 / HUD 本体），不弹窗。

    python tools/shot_overlay.py [输出目录]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENDBOX_NO_GLASS", "1")
os.environ.setdefault("ENDBOX_NO_UPDATE_CHECK", "1")
os.environ.setdefault("ENDBOX_DATA_DIR", str(ROOT / "_devdata"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from endbox import config  # noqa: E402
from endbox.theme import fonts  # noqa: E402
from endbox.theme.style import app_qss  # noqa: E402
from endbox.theme.tokens import theme  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "shots"


def operator_asset(name: str, kind: str = "") -> str:
    # 技能 / 连携 / 终结优先用 素材/gen 里生成的卡片图标
    if kind in ("skill", "link", "ult"):
        gen = ROOT / "素材" / "gen" / f"{kind}_{name}_x.png"
        if gen.is_file():
            return str(gen)
    if kind == "heavy":
        gen = ROOT / "素材" / "gen" / "heavy_x_x_nb.png"
        if gen.is_file():
            return str(gen)
    candidates = [
        config.GAME_ASSETS / "operators" / name / (f"{kind}.png" if kind else "avatar.png"),
        ROOT / "assets" / "gamedata" / "operators" / name / (f"{kind}.png" if kind else "avatar.png"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return ""


def run_cards() -> list[dict]:
    plan = [
        ("伊冯", "skill", "1", "past", "战技"),
        ("余烬", "link", "E", "past", "连携技"),
        ("伊冯", "link", "E", "current", "连携技"),
        ("佩丽卡", "ult", "长按3", "next", "终结技"),
        ("陈千语", "switch", "F4", "next", "切换干员"),
    ]
    cards = []
    for name, kind, key, state, label in plan:
        icon = ""
        if kind in ("skill", "link", "ult", "attack"):
            icon = operator_asset(name, kind)
        card = {
            "kind": kind,
            "kind_label": label,
            "operator_name": name,
            "key_text": key,
            "state": state,
            "avatar": operator_asset(name),
            "icon": icon,
        }
        cards.append(card)
    return cards


def shoot_run_bar(app) -> None:
    from endbox.modules.timeline.run_overlay import RunOverlay

    state = {
        "step": {"operator_name": "伊冯", "kind_label": "连携技", "duration": 4.3},
        "next": {"operator_name": "佩丽卡", "kind_label": "终结技"},
        "segment": {"index": 1},
        "loop": {"text": "反复 ∞（第 1 遍）"},
        "total": 5,
        "progress_text": "3 / 5",
        "display": "kb",
        "hint": "",
        "cards": run_cards(),
        "settings": {"scale": 1.5, "card_count": 5},
    }
    overlay = RunOverlay({"scale": 1.5})
    overlay.apply_state(state)
    overlay.resize(overlay.sizeHint() if overlay.sizeHint().isValid() else overlay.size())
    pix = overlay.grab()
    pix.save(str(OUT / "timeline_run_focus_dark.png"))
    print("saved", OUT / "timeline_run_focus_dark.png", pix.width(), pix.height())
    overlay.deleteLater()


def hud_snapshot() -> dict:
    return {
        "enabled": True,
        "mode": "compact",
        "anchor": "top-center",
        "scale": 1.5,
        "opacity": 0.96,
        "clock": "22:33:58",
        "clock_12h": False,
        "clock_seconds": True,
        "username": "ASUS",
        "account": {
            "connected": True,
            "online": True,
            "nickname": "博士",
            "avatar_path": operator_asset("伊冯"),
            "provider": "skland",
            "last_sync": 0,
            "error": None,
        },
        "battery": {"available": True, "percent": 76, "ac": False, "charging": False, "remaining_s": 7500},
        "show": {"account": True, "battery": True, "clock": True},
        "animations": False,
    }


def shoot_hud(app) -> None:
    from endbox.modules.hud.overlay import HudWindow

    class _StubService:
        def snapshot(self) -> dict:
            return hud_snapshot()

    window = HudWindow(_StubService())
    window.apply_snapshot(hud_snapshot())
    window.canvas.set_progress(1.0)
    window.resize(window.canvas.size())
    window.canvas.resize(window.size())
    window.canvas.setGeometry(0, 0, window.width(), window.height())
    app.processEvents()
    pix = window.canvas.grab()
    pix.save(str(OUT / "hud_body_dark.png"))
    print("saved", OUT / "hud_body_dark.png", pix.width(), pix.height())
    window.deleteLater()


def main() -> int:
    config.ensure_dirs()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    fonts.load_fonts()
    theme.set_mode("dark", emit=False)
    app.setStyleSheet(app_qss(theme.p))
    OUT.mkdir(parents=True, exist_ok=True)
    shoot_run_bar(app)
    app.processEvents()
    shoot_hud(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
