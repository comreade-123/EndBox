# -*- coding: utf-8 -*-
"""插件私有存储：``%APPDATA%/EndBox/plugin_data/<id>.json``。

每个插件只能读写自己的那份字典；插件之间互不可见。
写入用「临时文件 + os.replace」保证原子性，磁盘错误只记录日志、不把异常抛回插件：
存储失败不应该让主程序或插件生命周期崩掉。
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from endbox import config
from endbox.core.log import get_logger

log = get_logger("plugins.storage")

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def storage_filename(plugin_id: str) -> str:
    """插件 id → 安全的文件名（id 已由 manifest 校验，这里再兜一层）。"""
    safe = _UNSAFE.sub("_", str(plugin_id or "plugin")).strip("._") or "plugin"
    return f"{safe}.json"


class PluginStorage:
    """插件私有 KV 字典（JSON 落盘）。所有方法线程安全。"""

    def __init__(self, plugin_id: str, path: Path | None = None):
        self.plugin_id = str(plugin_id)
        self._path = Path(path) if path is not None else (
            config.DATA_DIR / "plugin_data" / storage_filename(plugin_id)
        )
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.load()

    # -- 路径 ---------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    # -- 读写 ---------------------------------------------------------------
    def load(self) -> None:
        """容错读取：文件不存在 / JSON 损坏 / 顶层不是对象都退回空字典。"""
        with self._lock:
            try:
                raw = json.loads(self._path.read_text("utf-8"))
            except FileNotFoundError:
                self._data = {}
                return
            except Exception:  # noqa: BLE001 - 损坏的存储不能阻塞加载
                log.warning("plugin storage unreadable, reset: %s", self._path)
                self._data = {}
                return
            self._data = raw if isinstance(raw, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(str(key), default)

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        with self._lock:
            self._data[str(key)] = value
        if save:
            self.save()

    def update(self, mapping: dict[str, Any], *, save: bool = True) -> None:
        with self._lock:
            for key, value in mapping.items():
                self._data[str(key)] = value
        if save:
            self.save()

    def delete(self, key: str, *, save: bool = True) -> None:
        with self._lock:
            self._data.pop(str(key), None)
        if save:
            self.save()

    def clear(self, *, save: bool = True) -> None:
        with self._lock:
            self._data.clear()
        if save:
            self.save()

    def all(self) -> dict[str, Any]:
        """只读快照。"""
        with self._lock:
            return json.loads(json.dumps(self._data))

    def save(self) -> bool:
        """原子写盘；成功 True，磁盘错误 False（只记日志）。"""
        with self._lock:
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(payload, "utf-8")
            os.replace(tmp, self._path)
            return True
        except Exception:  # noqa: BLE001 - 存储失败不应中断插件
            log.exception("plugin storage write failed: %s", self._path)
            return False

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return str(key) in self._data
