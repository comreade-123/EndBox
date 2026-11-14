# -*- coding: utf-8 -*-
"""开发用截图：构建主窗口，切到指定视图，把窗口渲染成 PNG。

    python tools/shot.py timeline out.png
    python tools/shot.py settings out.png light
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENDBOX_NO_GLASS", "1")
os.environ.setdefault("ENDBOX_DATA_DIR", str(ROOT / "_devdata"))
os.environ.setdefault("ENDBOX_NO_UPDATE_CHECK", "1")

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
    out_path = sys.argv[2] if len(sys.argv) > 2 else "shots/endbox.png"
    mode = sys.argv[3] if len(sys.argv) > 3 else "dark"
    width = int(sys.argv[4]) if len(sys.argv) > 4 else 1320
    height = int(sys.argv[5]) if len(sys.argv) > 5 else 860

    config.ensure_dirs()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    fonts.load_fonts()
    theme.set_mode(mode, emit=False)
    app.setStyleSheet(app_qss(theme.p))

    ctx = AppContext(settings=config.settings, bus=bus, services=Services())
    from endbox.ui.main_window import MainWindow
    from endbox.ui.views import load_views
    from endbox.ui.views.base import ViewRegistry

    registry = ViewRegistry()
    errors = load_views(registry)
    if errors:
        print("view errors:", errors)
    try:
        from endbox.app import _init_services, _load_plugins

        _init_services(ctx)
        window = MainWindow(ctx, registry)
        ctx.toast = window.toast.show_message
        _load_plugins(ctx, registry)
    except Exception as exc:  # noqa: BLE001
        print("boot error:", exc)
        raise
    window.resize(width, height)
    window.show()
    window.show_view(view_key)

    def grab() -> None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix = window.grab()
        pix.save(str(out))
        print("saved", out, pix.width(), pix.height())
        app.quit()

    QTimer.singleShot(1400, grab)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
