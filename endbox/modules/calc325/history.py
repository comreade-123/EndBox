# -*- coding: utf-8 -*-
"""325 随机/求解模式的历史与收藏（本地 JSON，原子写）。

参考 Calculate325-main：随机生成自动记录历史、可收藏喜欢的式子。
路径固定在 ``config.DATA_DIR`` 下（EndBox 的数据目录，默认 %APPDATA%/EndBox）：

  · ``calc325_history.json``    生成历史，最多 100 条；
  · ``calc325_favorites.json``  收藏，最多 100 条。

损坏的 JSON 会被当成空列表（容错），写盘失败只记日志，不打断界面。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from endbox import config
from endbox.core.log import get_logger

log = get_logger("calc325.history")

MAX_ENTRIES = 100


@dataclass
class CalcEntry:
    """一条历史 / 收藏记录。"""

    seed: str
    level: int
    seed_type: str = "number"
    formula: str = ""
    latex: str = ""
    created_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.seed, int(self.level), self.seed_type)

    @property
    def time_text(self) -> str:
        return time.strftime("%m-%d %H:%M", time.localtime(self.created_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "level": int(self.level),
            "seed_type": self.seed_type,
            "formula": self.formula,
            "latex": self.latex,
            "created_at": float(self.created_at),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CalcEntry | None":
        if not isinstance(raw, dict):
            return None
        seed = str(raw.get("seed") or "").strip()
        if not seed:
            return None
        try:
            level = max(1, min(5, int(raw.get("level", 2))))
        except (TypeError, ValueError):
            level = 2
        seed_type = "zc" if raw.get("seed_type") == "zc" else "number"
        try:
            created = float(raw.get("created_at", time.time()))
        except (TypeError, ValueError):
            created = time.time()
        extra = raw.get("extra")
        return cls(
            seed=seed,
            level=level,
            seed_type=seed_type,
            formula=str(raw.get("formula") or ""),
            latex=str(raw.get("latex") or ""),
            created_at=created,
            extra=extra if isinstance(extra, dict) else {},
        )


class EntryStore:
    """带容量上限的 JSON 条目存储（历史 / 收藏共用）。"""

    def __init__(self, path: Path | None = None, *, limit: int = MAX_ENTRIES):
        self._path = Path(path) if path is not None else (config.DATA_DIR / "calc325_history.json")
        self._limit = max(1, int(limit))
        self._lock = threading.RLock()
        self._entries: list[CalcEntry] = []
        self.load()

    # -- 读写 ---------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    @property
    def limit(self) -> int:
        return self._limit

    def load(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text("utf-8"))
            except FileNotFoundError:
                self._entries = []
                return
            except Exception:  # noqa: BLE001 - 数据损坏不应打断界面
                log.warning("calc325 store unreadable, reset: %s", self._path)
                self._entries = []
                return
            items = raw.get("entries") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                self._entries = []
                return
            entries = [entry for entry in (CalcEntry.from_dict(item) for item in items) if entry]
            self._entries = entries[: self._limit]

    def save(self) -> bool:
        with self._lock:
            payload = {
                "version": 1,
                "entries": [entry.to_dict() for entry in self._entries],
            }
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_name(self._path.name + ".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
                os.replace(tmp, self._path)
                return True
            except Exception:  # noqa: BLE001
                log.exception("calc325 store write failed: %s", self._path)
                return False

    # -- 条目 ---------------------------------------------------------------
    def entries(self) -> list[CalcEntry]:
        with self._lock:
            return [CalcEntry(**{**entry.to_dict()}) for entry in self._entries]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def add(self, entry: CalcEntry, *, save: bool = True) -> bool:
        """新增或把同一条（种子+等级+编码）移到最前；超出上限丢弃最旧。"""
        with self._lock:
            self._entries = [item for item in self._entries if item.key != entry.key]
            self._entries.insert(0, entry)
            self._entries = self._entries[: self._limit]
        if save:
            self.save()
        return True

    def remove(self, key: tuple[str, int, str], *, save: bool = True) -> bool:
        with self._lock:
            before = len(self._entries)
            self._entries = [item for item in self._entries if item.key != key]
            removed = len(self._entries) != before
        if removed and save:
            self.save()
        return removed

    def clear(self, *, save: bool = True) -> None:
        with self._lock:
            self._entries = []
        if save:
            self.save()

    def has(self, key: tuple[str, int, str]) -> bool:
        with self._lock:
            return any(item.key == key for item in self._entries)


class HistoryStore(EntryStore):
    """随机模式生成历史（最多 100 条）。"""

    def __init__(self, path: Path | None = None, *, limit: int = MAX_ENTRIES):
        super().__init__(path or (config.DATA_DIR / "calc325_history.json"), limit=limit)


class FavoriteStore(EntryStore):
    """收藏（最多 100 条，按 LaTeX 去重）。"""

    def __init__(self, path: Path | None = None, *, limit: int = MAX_ENTRIES):
        super().__init__(path or (config.DATA_DIR / "calc325_favorites.json"), limit=limit)

    def add(self, entry: CalcEntry, *, save: bool = True) -> bool:  # type: ignore[override]
        with self._lock:
            latex = entry.latex.strip()
            self._entries = [
                item for item in self._entries
                if item.key != entry.key and (not latex or item.latex.strip() != latex)
            ]
            self._entries.insert(0, entry)
            self._entries = self._entries[: self._limit]
        if save:
            self.save()
        return True
