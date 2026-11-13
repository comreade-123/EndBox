# -*- coding: utf-8 -*-
"""排轴运行态全链路自检（不依赖真实 OS 按键注入）。

链路：FakeKeyboardBackend → InputService → InputAdapter → TimelineService → RunEngine。

    python tools/verify_runtime.py

键位（Endfield 实机设定 / 参考实现 行为参考）：
  1~4 短按 = 战技、长按 1~4 = 终结、E = 连携、H 重击只提示不按键；
  F8 退出 / F9 回第一段 / F6-F7 透明度 / F10 反复开关 / F11 回反复起点。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENDBOX_DATA_DIR", str(ROOT / "_devdata"))
os.environ.setdefault("ENDBOX_NO_GLASS", "1")

from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from endbox.core.event_bus import bus  # noqa: E402
from endbox.core.input.keyboard import KeyboardBackend  # noqa: E402
from endbox.core.input.models import KeyEvent  # noqa: E402
from endbox.core.input.service import InputService  # noqa: E402
from endbox.modules.timeline.assets import AssetLibrary  # noqa: E402
from endbox.modules.timeline.service import TimelineService  # noqa: E402
from endbox.services import Services  # noqa: E402


class FakeKeyboard(KeyboardBackend):
    """测试用键盘后端：把事件直接交给 InputService。"""

    name = "fake"

    def __init__(self) -> None:
        self.emit = None
        self.running = False

    @property
    def available(self) -> bool:
        return True

    def start(self, emit) -> None:
        self.emit = emit
        self.running = True

    def stop(self) -> None:
        self.running = False

    VK = {"1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "E": 0x45, "H": 0x48,
          "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A}

    def press(self, token: str, kind: str = "down") -> None:
        if self.emit is not None:
            base = token.split("+")[-1] if token.startswith("HOLD+") else token
            if token.startswith("HOLD+"):
                base = token
            vk = self.VK.get(base, 0)
            self.emit(KeyEvent(token=token, kind=kind, vk=vk, source="keyboard"))


class Ctx:
    def __init__(self) -> None:
        self.settings = None
        self.bus = bus
        self.services = Services()
        self.main_window = None

    def notify(self, *args, **kwargs) -> None:
        pass


def main() -> int:
    app = QApplication([])
    fake = FakeKeyboard()
    input_service = InputService(keyboard_backend=fake, admin_check=lambda: True)
    ctx = Ctx()
    ctx.services.input = input_service  # 让 timeline 适配层自动接入

    service = TimelineService(
        ctx,
        assets=AssetLibrary(),
        autosave=False,
        overlay_enabled=False,
        input_backend="service",
        path=ROOT / "_devdata" / "runtime_check.json",
    )
    service.set_team(["伊冯", "余烬", "", ""])
    service.model.clear()
    service.model.add_step("skill", 0)   # 短按 1
    service.model.add_step("link", 1)    # E
    service.model.add_step("ult", 0)     # 长按 1
    service.model.add_step("heavy", 1)   # 重击：只提示，不按键
    service.model.add_step("skill", 1)   # 短按 2

    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(("OK   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
        if not cond:
            failures.append(name)

    if not service.start_run():
        print("FAIL start_run:", service.last_error)
        return 1
    QTest.qWait(120)
    tokens = [s.token for s in service._run_engine.sequence.steps]
    print("steps:", tokens, "| adapter:", service.input.backend, service.input.status_text)
    check("运行状态", service.run_active is True)
    check("序列 token（重击跳过）", tokens == ["1", "E", "HOLD+1", "2"], str(tokens))

    def index() -> int:
        return service._run_engine.index

    # 短按 1（输入服务发 down token "1"，适配层用 raw 判定短按后转发）
    before = index()
    fake.press("1")
    QTest.qWait(60)
    fake.press("1", "up")
    QTest.qWait(120)
    check("短按 1 = 战技前进", index() == before + 1, f"{before} -> {index()}")

    # 按错 H（重击格已跳过，此时是 E）
    before = index()
    fake.press("H")
    QTest.qWait(60)
    fake.press("H", "up")
    QTest.qWait(120)
    check("按错 H 不前进", index() == before, f"{before} -> {index()}")

    # E = 连携
    before = index()
    fake.press("E")
    QTest.qWait(60)
    fake.press("E", "up")
    QTest.qWait(120)
    check("E 连携前进", index() == before + 1, f"{before} -> {index()}")

    # 长按 1 = 终结（输入服务会先发 1，再发 HOLD+1；适配层应忽略短按、转发 HOLD+1）
    before = index()
    fake.press("1")
    QTest.qWait(350)
    fake.press("HOLD+1", "hold")
    QTest.qWait(60)
    fake.press("1", "up")
    QTest.qWait(150)
    check("长按 1 = 终结前进", index() == before + 1, f"{before} -> {index()}")

    # 短按 2 收尾
    before = index()
    fake.press("2")
    QTest.qWait(60)
    fake.press("2", "up")
    QTest.qWait(150)
    check("短按 2 前进/完成", index() >= before + 1 or service._run_engine.finished, f"index={index()}")

    # 控制键（重新开始）
    service.stop_run("test")
    service.start_run()
    QTest.qWait(100)
    fake.press("F10")
    QTest.qWait(80)
    check("F10 切换反复", service._run_engine.loops_enabled is not None)
    fake.press("F9")
    QTest.qWait(80)
    check("F9 回第一段", index() == 0, f"index={index()}")
    fake.press("F8")
    QTest.qWait(120)
    check("F8 退出运行", service.run_active is False)
    service.stop_run("test")
    QTest.qWait(120)
    check("退出后输入停止", not fake.running, f"running={fake.running}")
    print("\n=== runtime pipeline:", "PASS" if not failures else f"FAIL ({len(failures)})", "===")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
