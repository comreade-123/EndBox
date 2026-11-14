# -*- coding: utf-8 -*-
"""输入事件模型：键盘 / 手柄统一成 KeyEvent。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


class InputBackendError(RuntimeError):
    """输入后端启动失败（可显示的中文原因）。"""


@dataclass(frozen=True)
class KeyEvent:
    """一次输入事件。

    ``token`` 是规范化后的动作 token（未绑定按键为空字符串）；
    ``kind`` 为 ``down`` / ``up`` / ``hold``；
    ``modifiers`` 是按下瞬间持有的修饰键（``ALT`` / ``Q`` / ``E`` / ``PAD_MOD``）；
    ``vk`` 键盘为虚拟键码、手柄为按钮位；``source`` 为 ``keyboard`` / ``gamepad``。
    ``injected`` 表示事件来自 SendInput/keybd_event（真实钩子测试时会置 True）。
    """

    token: str = ""
    kind: str = "down"
    modifiers: tuple[str, ...] = ()
    vk: int = 0
    timestamp: float = field(default_factory=time.monotonic)
    source: str = "keyboard"
    injected: bool = False

    @property
    def is_press(self) -> bool:
        return self.kind == "down"

    @property
    def is_release(self) -> bool:
        return self.kind == "up"

    @property
    def is_hold(self) -> bool:
        return self.kind == "hold"

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "kind": self.kind,
            "modifiers": list(self.modifiers),
            "vk": self.vk,
            "timestamp": self.timestamp,
            "source": self.source,
            "injected": self.injected,
        }

    def __str__(self) -> str:  # 便于日志排查
        mods = "+".join(self.modifiers)
        return f"KeyEvent({self.source}:{self.kind}:{self.token or '-'}:vk={self.vk}:mods={mods or '-'})"
