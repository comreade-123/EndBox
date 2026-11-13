# -*- coding: utf-8 -*-
"""排轴运行态按键验证（真实 keybd_event 优先，受限环境自动退回适配层注入）。

键位（实机设定）：1~4 短按 = 战技、长按 1~4 = 终结、E = 连携；
重击格只提示不按键；F8 退出 / F9 回第一段 / F6-F7 透明度 / F10 反复 / F11 回反复起点。

    python tools/verify_runtime_os.py
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENDBOX_DATA_DIR", str(ROOT / "_devdata"))
os.environ.setdefault("ENDBOX_NO_GLASS", "1")

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from endbox.core.event_bus import bus  # noqa: E402
from endbox.core.input import is_admin  # noqa: E402
from endbox.modules.timeline.assets import AssetLibrary  # noqa: E402
from endbox.modules.timeline.service import TimelineService  # noqa: E402
from endbox.services import Services  # noqa: E402

VK = {
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "E": 0x45, "H": 0x48,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A,
}
KEYEVENTF_KEYUP = 0x0002


class Ctx:
    def __init__(self) -> None:
        self.settings = None
        self.bus = bus
        self.services = Services()
        self.main_window = None

    def notify(self, *args, **kwargs) -> None:
        pass


def main() -> int:
    app = QApplication(sys.argv[:1])
    window = QWidget()
    window.resize(420, 130)
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("EndBox 排轴按键自检中…（约 6 秒，勿操作）"))
    window.show()
    window.raise_()
    window.activateWindow()

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_size_t]
    user32.keybd_event.restype = None
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_int
    kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
    hwnd = int(window.winId())

    def foreground() -> bool:
        if int(user32.GetForegroundWindow() or 0) == hwnd:
            return True
        window.raise_(); window.activateWindow()
        try:
            fg = int(user32.GetForegroundWindow() or 0)
            tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(fg), None)
            cur = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(tid, cur, 1)
            try:
                user32.BringWindowToTop(ctypes.c_void_p(hwnd))
                user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
            finally:
                user32.AttachThreadInput(tid, cur, 0)
        except Exception:  # noqa: BLE001
            pass
        return int(user32.GetForegroundWindow() or 0) == hwnd

    for name in VK:
        user32.keybd_event(VK[name], 0, KEYEVENTF_KEYUP, 0)

    def key(name: str, up: bool = False) -> bool:
        if not foreground():
            return False
        user32.keybd_event(VK[name], 0, KEYEVENTF_KEYUP if up else 0, 0)
        return True

    ctx = Ctx()
    service = TimelineService(
        ctx, assets=AssetLibrary(), autosave=False, overlay_enabled=False,
        input_backend="auto", path=ROOT / "_devdata" / "runtime_os_check.json",
    )
    service.set_team(["伊冯", "余烬", "", ""])
    service.model.clear()
    service.model.add_step("skill", 0)   # 1
    service.model.add_step("link", 1)    # E
    service.model.add_step("ult", 0)     # 长按 1
    service.model.add_step("heavy", 1)   # 重击（跳过）
    service.model.add_step("skill", 1)   # 2

    results: list[tuple[str, bool, str]] = []
    fallback = False

    def check(name: str, cond: bool, detail: str = "") -> None:
        results.append((name, cond, detail))
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

    if not service.start_run():
        print("FAIL start_run:", service.last_error)
        return 1
    QTimer.singleShot(300, lambda: print("adapter:", service.input.backend, service.input.status_text))

    def index() -> int:
        engine = service._run_engine
        return int(getattr(engine, "index", -1)) if engine is not None else -2

    def after(before: int, token: str) -> None:
        nonlocal fallback
        engine = service._run_engine
        if index() > before or engine is None or engine.finished:
            return
        fallback = True
        service.feed_input(token)
        print(f"  (fallback: 真实按键未生效，注入 {token})")

    def press(name: str, token: str) -> None:
        before = index()
        key(name)
        QTimer.singleShot(80, lambda: key(name, True))
        QTimer.singleShot(320, lambda: after(before, token))

    def hold(name: str, token: str) -> None:
        before = index()
        key(name)
        QTimer.singleShot(430, lambda: key(name, True))
        QTimer.singleShot(780, lambda: after(before, token))

    def schedule() -> None:
        steps = [s.token for s in service._run_engine.sequence.steps]
        check("序列 token（重击跳过）", steps == ["1", "E", "HOLD+1", "2"], str(steps))
        check("适配层可用", service.input.available, service.input.status_text)

    QTimer.singleShot(500, schedule)
    QTimer.singleShot(700, lambda: press("1", "1"))
    QTimer.singleShot(1150, lambda: check("短按 1 战技前进", index() == 1, f"index={index()}"))
    QTimer.singleShot(1350, lambda: press("H", "H"))
    QTimer.singleShot(1800, lambda: check("按错 H 不前进", index() == 1, f"index={index()}"))
    QTimer.singleShot(2000, lambda: press("E", "E"))
    QTimer.singleShot(2450, lambda: check("E 连携前进", index() == 2, f"index={index()}"))
    QTimer.singleShot(2650, lambda: hold("1", "HOLD+1"))
    QTimer.singleShot(3550, lambda: check("长按 1 终结前进", index() == 3, f"index={index()}"))
    QTimer.singleShot(3750, lambda: press("2", "2"))
    QTimer.singleShot(4200, lambda: check("短按 2 前进/完成", index() >= 4 or service._run_engine.finished, f"index={index()}"))
    QTimer.singleShot(4400, lambda: press("F9", "F9"))
    QTimer.singleShot(4850, lambda: check("F9 回第一段", index() == 0, f"index={index()}"))
    QTimer.singleShot(5050, lambda: press("F8", "F8"))

    def finalize() -> None:
        check("F8 退出运行", service.run_active is False)
        service.stop_run("test")
        window.close()
        failed = [name for name, ok, _ in results if not ok]
        print(f"\n=== OS runtime check: {'PASS' if not failed else f'FAIL ({len(failed)})'} ===")
        print("admin:", is_admin(), "| adapter:", service.input.status_text, "| fallback:", fallback)
        app.quit()

    QTimer.singleShot(5500, finalize)
    app.exec()
    failed = [name for name, ok, _ in results if not ok]
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
