# -*- coding: utf-8 -*-
"""技能轴库：``%APPDATA%/EndBox/timeline_library.json`` 的多套排轴（轴名 / 编队 / 内容）。

与「当前轴」的自动保存（timeline.json）分开：
- 当前轴每次编辑自动落盘；
- 轴库是用户显式「保存到轴库 / 另存为」的多套快照，可搜索、载入、删除。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from endbox.core.log import get_logger
from endbox.core.text import t as tr

log = get_logger("modules.timeline.library")

LIBRARY_VERSION = 1


def _entry_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> int:
    return int(time.time())


class AxisLibrary:
    """轴库文件读写；方法都返回简单结构或布尔，绝不抛给界面。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.entries: list[dict] = []
        self._teams: list[dict] = []
        self.last_error: str | None = None
        self.load()

    # -- 读写 ---------------------------------------------------------------
    def load(self) -> None:
        self.last_error = None
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            self.entries = []
            return
        except Exception as exc:  # noqa: BLE001 - 轴库坏了不阻塞排轴
            self.last_error = tr("timeline.errors.axis_read", error=exc)
            log.warning("轴库读取失败：%s", exc)
            self.entries = []
            return
        axes = raw.get("axes") if isinstance(raw, dict) else None
        self._teams = list(raw.get("teams") or []) if isinstance(raw, dict) else []
        if not isinstance(axes, list):
            self.entries = []
            return
        clean: list[dict] = []
        seen: set[str] = set()
        for item in axes:
            entry = self._normalize(item)
            if entry is None:
                continue
            if entry["id"] in seen:
                entry["id"] = _entry_id()
            seen.add(entry["id"])
            clean.append(entry)
        self.entries = clean

    def save(self) -> bool:
        payload = {"version": LIBRARY_VERSION, "axes": self.entries, "teams": list(getattr(self, "_teams", []))}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            os.replace(temp, self.path)
            self.last_error = None
            return True
        except OSError as exc:
            self.last_error = tr("timeline.errors.axis_save", error=exc)
            log.warning("轴库保存失败：%s", exc)
            return False

    @staticmethod
    def _normalize(item) -> dict | None:
        if not isinstance(item, dict):
            return None
        name = str(item.get("name") or "").strip()[:60]
        if not name:
            name = tr("timeline.defaults.unnamed_axis")
        model = item.get("model") if isinstance(item.get("model"), dict) else {}
        team = item.get("team") if isinstance(item.get("team"), list) else []
        events = model.get("events") if isinstance(model.get("events"), list) else []
        try:
            duration = float(model.get("duration", 30.0))
        except (TypeError, ValueError):
            duration = 30.0
        return {
            "id": str(item.get("id") or _entry_id()),
            "name": name,
            "team": [str(n or "") for n in team][:4],
            "model": model,
            "updated": int(item.get("updated") or _now()),
            "event_count": len(events),
            "duration": round(duration, 3),
        }

    # -- 查询 ---------------------------------------------------------------
    def list(self, query: str = "") -> list[dict]:
        needle = str(query or "").strip().lower()
        items = list(self.entries)
        if needle:
            matched: list[dict] = []
            for entry in items:
                haystack = " ".join(
                    [
                        str(entry.get("name") or ""),
                        " ".join(str(n) for n in entry.get("team") or []),
                        " ".join(
                            str(e.get("label") or "")
                            for e in (entry.get("model") or {}).get("events") or []
                            if isinstance(e, dict)
                        ),
                        " ".join(
                            str(b.get("name") or "")
                            for b in (entry.get("model") or {}).get("buffs") or []
                            if isinstance(b, dict)
                        ),
                    ]
                ).lower()
                if needle in haystack:
                    matched.append(entry)
            items = matched
        return sorted(items, key=lambda e: int(e.get("updated") or 0), reverse=True)

    def get(self, entry_id: str) -> dict | None:
        for entry in self.entries:
            if entry.get("id") == entry_id:
                return entry
        return None

    # -- 编辑 ---------------------------------------------------------------
    def add(
        self,
        name: str,
        team: list[str],
        model: dict,
        *,
        entry_id: str | None = None,
    ) -> dict | None:
        name = str(name or "").strip()[:60] or tr("timeline.defaults.unnamed_axis")
        model = model if isinstance(model, dict) else {}
        existing = self.get(entry_id) if entry_id else None
        if existing is not None:
            existing["name"] = name
            existing["team"] = [str(n or "") for n in (team or [])][:4]
            existing["model"] = model
            existing["updated"] = _now()
            existing["event_count"] = len(model.get("events") or [])
            try:
                existing["duration"] = round(float(model.get("duration", 30.0)), 3)
            except (TypeError, ValueError):
                existing["duration"] = 30.0
            if not self.save():
                return None
            return existing
        entry = self._normalize(
            {
                "id": _entry_id(),
                "name": name,
                "team": list(team or []),
                "model": model,
                "updated": _now(),
            }
        )
        if entry is None:
            return None
        self.entries.append(entry)
        if not self.save():
            self.entries.pop()
            return None
        return entry

    def delete(self, entry_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.get("id") != entry_id]
        if len(self.entries) == before:
            return False
        return self.save()

    def rename(self, entry_id: str, name: str) -> bool:
        entry = self.get(entry_id)
        if entry is None:
            return False
        entry["name"] = str(name or "").strip()[:60] or entry["name"]
        entry["updated"] = _now()
        return self.save()

    # -- 编队库 -------------------------------------------------------------
    def team_list(self, query: str = "") -> list[dict]:
        needle = str(query or "").strip().lower()
        teams = list(getattr(self, "_teams", []))
        if needle:
            teams = [t for t in teams if needle in (str(t.get("name") or "") + " " + " ".join(t.get("slots") or [])).lower()]
        return sorted(teams, key=lambda t: int(t.get("updated") or 0), reverse=True)

    def team_add(self, name: str, slots: list[str], entry_id: str | None = None) -> dict | None:
        teams = list(getattr(self, "_teams", []))
        name = str(name or "").strip()[:60] or tr("timeline.defaults.unnamed_team")
        clean = [str(n or "") for n in (slots or [])][:4]
        while len(clean) < 4:
            clean.append("")
        existing = next((t for t in teams if t.get("id") == entry_id), None)
        if existing is not None:
            existing.update({"name": name, "slots": clean, "updated": _now()})
            self._teams = teams
            self.save_teams()
            return existing
        entry = {"id": _entry_id(), "name": name, "slots": clean, "updated": _now()}
        teams.append(entry)
        self._teams = teams
        self.save_teams()
        return entry

    def team_get(self, entry_id: str) -> dict | None:
        return next((t for t in getattr(self, "_teams", []) if t.get("id") == entry_id), None)

    def team_delete(self, entry_id: str) -> bool:
        teams = list(getattr(self, "_teams", []))
        before = len(teams)
        self._teams = [t for t in teams if t.get("id") != entry_id]
        if len(self._teams) == before:
            return False
        return self.save_teams()

    def save_teams(self) -> bool:
        payload = {"version": LIBRARY_VERSION, "axes": self.entries, "teams": list(getattr(self, "_teams", []))}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            os.replace(temp, self.path)
            self.last_error = None
            return True
        except OSError as exc:
            self.last_error = tr("timeline.errors.team_save", error=exc)
            return False

    # -- 便捷 ---------------------------------------------------------------
    def team_names(self) -> list[str]:
        seen: set[str] = set()
        for entry in self.entries:
            for name in entry.get("team") or []:
                if name:
                    seen.add(str(name))
        return sorted(seen)
