# -*- coding: utf-8 -*-
"""集成自检：装配完整应用，逐个打开所有视图（含插件视图），切主题，截图，检查异常。

    python tools/integration_check.py [输出目录]
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENDBOX_NO_GLASS", "1")
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

out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "shots" / "final"
failures: list[str] = []


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
    errors = load_views(registry)
    if errors:
        failures.extend(f"view import: {name}: {err}" for name, err in errors.items())
    _init_services(ctx)
    window = MainWindow(ctx, registry)
    ctx.toast = window.toast.show_message
    _load_plugins(ctx, registry)
    window.resize(1320, 860)
    window.show()
    out_dir.mkdir(parents=True, exist_ok=True)

    keys = [cls.key for cls in registry.classes()]
    print("views:", keys)
    print("services:", ctx.services.snapshot())
    plugins = ctx.services.plugins
    print("plugins:", len(getattr(plugins, "loaded", []) or []) if plugins else "no host")

    steps: list[tuple[str, str]] = []
    for key in keys:
        steps.append((f"dark_{key}", key))
    # 浅色主题下再走一遍主要视图
    for key in keys:
        steps.append((f"light_{key}", key))

    index = {"i": 0}

    def run_step() -> None:
        i = index["i"]
        if i >= len(steps):
            finish()
            return
        name, key = steps[i]
        index["i"] += 1
        want = "light" if name.startswith("light_") else "dark"
        if theme.mode != want:
            theme.set_mode(want)  # 主窗口会同步 QSS / 重绘 / 毛玻璃
        try:
            window.show_view(key)
            view = window.stack.currentWidget()
            if view is None or view.key != key:
                failures.append(f"{name}: current view mismatch")
        except Exception:  # noqa: BLE001
            failures.append(f"{name}: show_view failed\n{traceback.format_exc()}")
        QTimer.singleShot(420, grab_current)

    def grab_current() -> None:
        i = index["i"] - 1
        name, key = steps[i]
        try:
            window.grab().save(str(out_dir / f"{name}.png"))
        except Exception:  # noqa: BLE001
            failures.append(f"{name}: screenshot failed")
        QTimer.singleShot(30, run_step)

    def finish() -> None:
        # 关窗 + 清理服务，检查退出路径
        try:
            window.close()
        except Exception:  # noqa: BLE001
            failures.append("window.close failed")
        try:
            for service in (ctx.services.hud, ctx.services.timeline, ctx.services.account):
                stop = getattr(service, "stop", None)
                if callable(stop):
                    stop()
            host = ctx.services.plugins
            if host is not None:
                host.unload_all()
        except Exception:  # noqa: BLE001
            failures.append(f"cleanup failed\n{traceback.format_exc()}")
        app.quit()

    QTimer.singleShot(200, run_step)
    code = app.exec()
    print(f"\n=== integration check: {len(steps)} views, {len(failures)} failures ===")
    for failure in failures:
        print("FAIL:", failure)
    return code or (1 if failures else 0)


if __name__ == "__main__":
    raise SystemExit(main())
