# -*- coding: utf-8 -*-
"""界面文案目录：所有用户可见文字集中在 ``assets/text/zh-CN.json``。

用法::

    from endbox.core.text import t

    label = t("home.quick.start_timeline")          # 直接取
    text = t("account.status.synced", time="12:30")  # {time} 占位符

约定：
  · key 用点分命名空间（app / shell / home / account / timeline / hud / calc325 / plugins / settings）；
  · JSON 是 UTF-8，改完重启生效，或在设置页点「重新加载文案」；
  · 找不到 key 时返回 key 本身（并在日志里 warning），不会让界面崩；
  · 占位符用 ``str.format`` 的 ``{name}`` 形式。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from endbox import config
from endbox.core.log import get_logger

log = get_logger("core.text")

TEXT_FILE = config.ASSETS / "text" / "zh-CN.json"


class TextCatalog:
    def __init__(self, path: Path = TEXT_FILE):
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._flat: dict[str, str] = {}
        self.load()

    # -- 加载 ---------------------------------------------------------------
    def load(self) -> bool:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text("utf-8"))
            except FileNotFoundError:
                log.warning("文案文件不存在：%s", self._path)
                return False
            except Exception:  # noqa: BLE001 - 文案损坏不能导致启动失败
                log.exception("文案文件读取失败：%s", self._path)
                return False
            if not isinstance(raw, dict):
                log.error("文案文件顶层必须是 JSON 对象：%s", self._path)
                return False
            self._data = raw
            self._flat = {}
            self._flatten(raw, "")
            log.info("文案已加载：%s 条（%s）", len(self._flat), self._path.name)
            return True

    def reload(self) -> bool:
        return self.load()

    def _flatten(self, node: dict, prefix: str) -> None:
        for key, value in node.items():
            if str(key).startswith("_"):
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                self._flatten(value, path)
            elif isinstance(value, (str, int, float)):
                self._flat[path] = str(value)

    # -- 读取 ---------------------------------------------------------------
    def t(self, key: str, default: str | None = None, **fmt: Any) -> str:
        with self._lock:
            value = self._flat.get(key)
        if value is None:
            if default is None:
                log.warning("缺少文案 key：%s", key)
                return key
            value = default
        if fmt:
            try:
                value = value.format(**fmt)
            except (KeyError, ValueError):
                log.warning("文案占位符不匹配：%s（%r）", key, value)
        return value

    def section(self, name: str) -> dict[str, Any]:
        with self._lock:
            data = self._data.get(name)
            return json.loads(json.dumps(data)) if isinstance(data, dict) else {}

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._flat

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._flat.keys())

    @property
    def path(self) -> Path:
        return self._path


catalog = TextCatalog()
t = catalog.t
reload_texts = catalog.reload
