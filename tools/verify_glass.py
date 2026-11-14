# -*- coding: utf-8 -*-
"""验证实时毛玻璃：窗口移动后毛玻璃帧按偏移更换（真实抓屏，不关毛玻璃）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENDBOX_NO_UPDATE_CHECK", "1")
os.environ.setdefault("ENDBOX_DATA_DIR", str(ROOT / "_devdata"))
os.environ.pop("ENDBOX_NO_GLASS", None)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from endbox import config  # noqa: E402
from endbox.app_context import AppContext  # noqa: E402
from endbox.core.event_bus import bus  # noqa: E402
from endbox.services import Services  # noqa: E402
from endbox.theme import fonts  # noqa: E402
from endbox.theme.style import app_qss  # noqa: E402
from endbox.theme.tokens import theme  # noqa: E402


def image_signature(pixmap):
    if pixmap is None or pixmap.isNull():
        return b""
    image = pixmap.toImage()
    return bytes(image.constBits().asstring(image.sizeInBytes()))


def main() -> int:
    config.ensure_dirs()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    fonts.load_fonts()
    theme.set_mode("dark", emit=False)
    app.setStyleSheet(app_qss(theme.p))

    ctx = AppContext(settings=config.settings, bus=bus, services=Services())
    from endbox.app import _init_services
    from endbox.ui.main_window import MainWindow
    from endbox.ui.views import load_views
    from endbox.ui.views.base import ViewRegistry

    registry = ViewRegistry()
    load_views(registry)
    _init_services(ctx)
    window = MainWindow(ctx, registry)
    window.resize(900, 600)
    window.move(120, 120)
    window.show()
    app.processEvents()

    print("glass_enabled:", config.GLASS_ENABLED)
    print("field:", window._glass_field is not None)  # noqa: SLF001
    first = image_signature(window._glass_base)  # noqa: SLF001
    print("frame_a_bytes:", len(first))

    window.move(420, 260)
    for _ in range(5):
        app.processEvents()
    window._sync_glass_frame()  # noqa: SLF001
    second = image_signature(window._glass_base)  # noqa: SLF001
    print("frame_b_bytes:", len(second))
    print("frame_changed:", first != second and bool(first) and bool(second))

    window.close()
    for service in (ctx.services.account, ctx.services.timeline, ctx.services.hud):
        stop = getattr(service, "stop", None)
        if callable(stop):
            stop()
    return 0 if (first and second and first != second) else 1


if __name__ == "__main__":
    raise SystemExit(main())
