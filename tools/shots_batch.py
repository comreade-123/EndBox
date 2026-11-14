# -*- coding: utf-8 -*-
"""批量生成 README 用的界面截图（深色），统一数据与滚动位置。

    python tools/shots_batch.py [输出目录]
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

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from endbox import config  # noqa: E402
from endbox.app_context import AppContext  # noqa: E402
from endbox.core.event_bus import bus  # noqa: E402
from endbox.services import Services  # noqa: E402
from endbox.theme import fonts  # noqa: E402
from endbox.theme.style import app_qss  # noqa: E402
from endbox.theme.tokens import theme  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "shots"
WINDOW = (1320, 860)


def main() -> int:
    config.ensure_dirs()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    fonts.load_fonts()
    theme.set_mode("dark", emit=False)
    app.setStyleSheet(app_qss(theme.p))

    ctx = AppContext(settings=config.settings, bus=bus, services=Services())
    from endbox.app import _init_services, _load_plugins
    from endbox.ui.main_window import MainWindow
    from endbox.ui.views import load_views
    from endbox.ui.views.base import ViewRegistry

    registry = ViewRegistry()
    load_views(registry)
    _init_services(ctx)
    window = MainWindow(ctx, registry)
    ctx.toast = window.toast.show_message
    _load_plugins(ctx, registry)
    window.resize(*WINDOW)
    window.show()

    account = ctx.services.account
    if account is not None:
        account.set_demo_mode(True)

    steps = [
        ("home_dark.png", "home", None, 0),
        ("timeline_dark.png", "timeline", None, 0),
        ("account_data_dark.png", "account", None, 0),
        ("hud_view_dark.png", "hud", None, 0),
        ("plugins_installed_dark.png", "plugins", "tab0", 300),
        ("plugins_market_dark.png", "plugins", "tab1", 900),
        ("settings_update_dark.png", "settings", "scroll_bottom", 200),
    ]
    index = {"i": 0}

    def save(name: str) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        pix = window.grab()
        pix.save(str(OUT / name))
        print("saved", name, pix.width(), pix.height())

    def run_step() -> None:
        i = index["i"]
        if i >= len(steps):
            finish()
            return
        name, key, action, delay = steps[i]
        try:
            window.show_view(key)
            view = window.stack.currentWidget()
            if action == "tab0" and hasattr(view, "_tabs"):
                view._tabs.set_index(0)  # noqa: SLF001
            elif action == "tab1" and hasattr(view, "_tabs"):
                view._tabs.set_index(1)  # noqa: SLF001
                loader = getattr(view, "_load_market", None)
                if callable(loader):
                    try:
                        loader(force=False)
                    except TypeError:
                        loader()
            elif action == "scroll_bottom":
                for child in view.findChildren(QScrollArea):
                    bar = child.verticalScrollBar()
                    QTimer.singleShot(60, lambda b=bar: b.setValue(b.maximum()))
        except Exception as exc:  # noqa: BLE001 - 截图失败不影响其它视图
            print("step failed:", name, exc)
        index["i"] += 1
        QTimer.singleShot(delay + 200, lambda n=name: (save(n), QTimer.singleShot(120, run_step)))

    def finish() -> None:
        window.close()
        for service in (ctx.services.hud, ctx.services.timeline, ctx.services.account):
            stop = getattr(service, "stop", None)
            if callable(stop):
                stop()
        app.quit()

    QTimer.singleShot(400, run_step)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
