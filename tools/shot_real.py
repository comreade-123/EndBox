# -*- coding: utf-8 -*-
"""真实屏幕截图（含 Windows 毛玻璃）：用于检查窗口在桌面上的实际观感。

    python tools/shot_real.py timeline shots/real_timeline.png dark
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENDBOX_DATA_DIR", str(ROOT / "_devdata"))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from endbox import config  # noqa: E402
from endbox.app_context import AppContext  # noqa: E402
from endbox.core.event_bus import bus  # noqa: E402
from endbox.services import Services  # noqa: E402
from endbox.theme import fonts  # noqa: E402
from endbox.theme.style import app_qss  # noqa: E402
from endbox.theme.tokens import theme  # noqa: E402


def main() -> int:
    view_key = sys.argv[1] if len(sys.argv) > 1 else "timeline"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "shots/real.png"
    mode = sys.argv[3] if len(sys.argv) > 3 else "dark"

    config.ensure_dirs()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    fonts.load_fonts()
    theme.set_mode(mode, emit=False)
    app.setStyleSheet(app_qss(theme.p))

    ctx = AppContext(settings=config.settings, bus=bus, services=Services())
    from endbox.app import _init_services, _load_plugins
    from endbox.ui.main_window import MainWindow
    from endbox.ui.views import load_views
    from endbox.ui.views.base import ViewRegistry

    registry = ViewRegistry()
    load_views(registry)
    try:
        _init_services(ctx)
        window = MainWindow(ctx, registry)
        ctx.toast = window.toast.show_message
        _load_plugins(ctx, registry)
    except Exception as exc:  # noqa: BLE001
        print("boot error:", exc)
        raise
    window.resize(1320, 860)
    window.show()
    window.raise_()
    window.activateWindow()
    window.show_view(view_key)

    def grab() -> None:
        from PIL import ImageGrab

        dpr = window.devicePixelRatioF()
        geo = window.frameGeometry()
        bbox = (
            int(geo.x() * dpr),
            int(geo.y() * dpr),
            int((geo.x() + geo.width()) * dpr),
            int((geo.y() + geo.height()) * dpr),
        )
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        print("saved", out, img.size, "dpr", dpr)
        app.quit()

    QTimer.singleShot(1800, grab)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
