# -*- coding: utf-8 -*-
"""排轴服务（v3）：有序步骤序列 + 轴库/编队库 + 分享码 + 按键提示式运行态。

没有时间轴：一条轴就是一串按顺序执行的步骤（战技/连携/终结/重击/注释/换行/
分段/反复开始/结束）。运行态用同一份序列编译成按键提示，按对才推进。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer

from endbox import config
from endbox.core.event_bus import Topics, bus as global_bus
from endbox.core.log import get_logger
from endbox.modules.timeline.assets import AssetLibrary
from endbox.modules.timeline.input_adapter import InputAdapter
from endbox.modules.timeline.library import AxisLibrary
from endbox.modules.timeline.model import (
    DEFAULT_LOOP_COUNT,
    KEY_KINDS,
    KINDS,
    KIND_LABELS,
    SLOT_COUNT,
    Step,
    Team,
    TimelineModel,
)
from endbox.modules.timeline.run_overlay import normalize_run_settings
from endbox.modules.timeline.runtime import (
    RunEngine,
    RunSequence,
    keys_from_settings,
    normalize_token,
    pad_key_text,
    switch_key,
)
from endbox.modules.timeline import share as share_code
from endbox.modules.timeline.skills import DEFAULT_DURATIONS
from endbox.core.text import t as tr

log = get_logger("modules.timeline")

SNAPSHOT_STEPS_MAX = 2000
SCHEMA_VERSION = 3
RUN_TOPIC = "timeline.run"
HINT_MS = 1400
FINISH_MS = 2000

def _pad_key_for(step, display: str, mode: str = "xbox") -> str:
    """提示条「键位显示 = 手柄」时的按键文本（终结技没有手柄键位，回退键盘）。"""
    if display != "pad" or not step.is_block or step.kind == "heavy":
        return ""
    return pad_key_text(step.kind, step.operator, mode)


RUN_DEFAULTS = {
    "style": "standard",
    "anchor": "top-center",
    "scale": 1.0,
    "opacity": 0.94,
    "preview": True,
    "progress": True,
    "minimize": True,
    "gamepad": "off",
    "hold_ms": 280,
    "card_count": 5,
    "next_count": 3,
    "repeat_all": True,
    "input_display": "kb",
    "pos": None,
}


class TimelineService(QObject):
    """排轴模块服务。视图只渲染；运行时钟/输入/轴库都在这里。"""

    def __init__(
        self,
        ctx=None,
        *,
        path: Path | str | None = None,
        library_path: Path | str | None = None,
        bus=None,
        assets: AssetLibrary | None = None,
        autosave: bool = True,
        input_backend: str | None = None,
        overlay_enabled: bool = True,
    ):
        super().__init__()
        self.ctx = ctx
        self._bus = bus if bus is not None else (getattr(ctx, "bus", None) if ctx is not None else None)
        if self._bus is None:
            self._bus = global_bus
        self.assets = assets if assets is not None else AssetLibrary()
        self.model = TimelineModel()
        self.team = Team()
        self.axis_name: str = ""
        self.last_error: str | None = None

        self._path = Path(path) if path is not None else (config.DATA_DIR / "timeline.json")
        self._library_path = (
            Path(library_path)
            if library_path is not None
            else (config.DATA_DIR / "timeline_library.json")
        )
        self.library = AxisLibrary(self._library_path)
        self._autosave = bool(autosave)
        self._undo: list[dict] = []
        self._redo: list[dict] = []

        self._t = 0.0
        self._playing = False
        self._run_active = False
        self._run_engine: RunEngine | None = None
        self._run_overlay = None
        self._run_hint = ""
        self._wrong_seq = 0
        self._advance_seq = 0
        self._wrong_gindex: int | None = None
        self._overlay_enabled = bool(overlay_enabled)
        self.input = InputAdapter(ctx, backend=input_backend, parent=self)
        self.input.set_keys(self.keys())
        self.input.token.connect(self.handle_input_token)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.setInterval(HINT_MS)
        self._hint_timer.timeout.connect(self._clear_run_hint)

        self._finish_timer = QTimer(self)
        self._finish_timer.setSingleShot(True)
        self._finish_timer.setInterval(FINISH_MS)
        self._finish_timer.timeout.connect(self._on_finish_timeout)

        self.load()

    # -- 基础 ---------------------------------------------------------------
    @property
    def t(self) -> float:
        return self._t

    @property
    def playing(self) -> bool:
        return self._playing

    # -- 旧接口兼容（序列模式没有时间概念；保留给 home/HUD 旧调用方）--------
    def play(self) -> None:
        self._playing = True
        self._emit_state()

    def pause(self) -> None:
        self._playing = False
        self._emit_state()

    def seek(self, t: float) -> None:
        try:
            self._t = max(0.0, float(t))
        except (TypeError, ValueError):
            self._t = 0.0
        self._emit_state()

    def toggle(self) -> None:
        self.pause() if self._playing else self.play()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def library_path(self) -> Path:
        return self._library_path

    def state(self) -> dict:
        return self.snapshot()

    def events(self) -> list:
        return list(self.model.steps)

    # -- 快照（v3 序列结构；保留旧字段给 home/HUD 的 len 用法）-------------
    def snapshot(self) -> dict:
        team_payload = self._team_payload()
        steps = []
        for index, step in enumerate(self.model.steps[-SNAPSHOT_STEPS_MAX:]):
            operator = self.assets.get(self.team.name(step.operator)) if step.is_block else None
            steps.append(
                {
                    "index": index,
                    "kind": step.kind,
                    "kind_label": KIND_LABELS.get(step.kind, step.kind),
                    "operator": int(step.operator),
                    "operator_name": self.team.name(step.operator) if step.is_block else "",
                    "class": operator.class_name if operator else None,
                    "element": operator.element if operator else None,
                    "label": step.label,
                    "loop_count": int(step.loop_count),
                    "group": step.group,
                    "duration": round(float(step.duration), 3),
                    "duration_estimate": self.estimate_duration(
                        step.kind, self.team.name(step.operator), step.duration
                    ),
                    "skill": step.skill,
                }
            )
        segments = [
            {"index": seg["index"], "name": seg["name"], "start": seg["start"], "end": seg["end"]}
            for seg in self.model.segments()
        ]
        loops = [
            {"start": loop["start"], "end": loop["end"], "count": loop["count"], "depth": loop["depth"]}
            for loop in self.model.loops()
        ]
        stats = self.stats()
        return {
            # 旧字段（home 只取 len；HUD 已与排轴解耦）
            "t": round(float(self._t), 3),
            "playing": bool(self._playing),
            "duration": float(len(self.model.steps)),
            "team": team_payload,
            "active": [],
            "events": steps,
            "buffs": [],
            # v3 序列字段
            "name": self.axis_name or tr("timeline.defaults.unnamed_axis"),
            "steps": steps,
            "step_count": len(self.model.steps),
            "event_count": len(self.model.steps),
            "kinds": self.model.counts(),
            "segments": segments,
            "loops": loops,
            "run_active": self._run_active,
            # 统计（真实计算：预计总耗时 / 技力消耗；缺耗时的步骤按技能动画估算）
            "total_duration": stats["total_duration"],
            "energy_cost": stats["energy_cost"],
            "duration_missing": stats["duration_missing"],
        }

    def _team_payload(self) -> list[dict]:
        payload = []
        for name in self.team.to_list():
            operator = self.assets.get(name) if name else None
            payload.append(
                {
                    "name": name,
                    "class": operator.class_name if operator else None,
                    "element": operator.element if operator else None,
                    "avatar": str(operator.avatar) if operator and operator.avatar else None,
                }
            )
        return payload

    # -- 队伍 ---------------------------------------------------------------
    def set_team(self, names: list[str]) -> None:
        team = Team.from_list(list(names or []))
        if self.assets.available:
            for index, name in enumerate(team.slots):
                if name and self.assets.get(name) is None:
                    team.slots[index] = ""
        self._push_undo()
        self.team = team
        self._commit()

    def set_slot(self, index: int, name: str) -> None:
        self._push_undo()
        self.team.set(index, name)
        if self.assets.available and name and self.assets.get(name) is None:
            self.team.set(index, "")
        self._commit()

    def clear_team(self) -> None:
        self._push_undo()
        self.team = Team()
        self._commit()

    def set_axis_name(self, name: str) -> None:
        self.axis_name = str(name or "").strip()[:60]
        self._commit()

    # -- 步骤编辑 -----------------------------------------------------------
    def add_step(self, kind: str, operator: int = 0, at: int | None = None, *, label: str = "",
                 loop_count: int = DEFAULT_LOOP_COUNT, group: str = "",
                 duration: float = 0.0, skill: str = "") -> Step:
        self._push_undo()
        step = self.model.add_step(kind, operator, at, label=label, loop_count=loop_count,
                                   group=group, duration=duration, skill=skill)
        self._commit()
        return step

    def remove_steps(self, steps) -> int:
        if not steps:
            return 0
        self._push_undo()
        removed = self.model.remove_steps(steps)
        if removed:
            self._commit()
        else:
            self._undo.pop()
        return removed

    def move_steps(self, steps, delta: int) -> bool:
        self._push_undo()
        moved = self.model.move_steps(steps, delta)
        if moved:
            self._commit()
        else:
            self._undo.pop()
        return moved

    def reorder_steps(self, steps, target_index: int) -> bool:
        self._push_undo()
        moved = self.model.reorder_steps(steps, target_index)
        if moved:
            self._commit()
        else:
            self._undo.pop()
        return moved

    def duplicate_steps(self, steps, at: int | None = None) -> list[Step]:
        copies = self.model.duplicate_steps(steps)
        if not copies:
            return []
        self._push_undo()
        if at is None:
            at = len(self.model.steps)
        for offset, step in enumerate(copies):
            self.model.insert_step(step, at + offset)
        self._commit()
        return copies

    def update_step(self, step: Step, *, operator: int | None = None, label: str | None = None,
                    loop_count: int | None = None, kind: str | None = None,
                    duration: float | None = None, skill: str | None = None) -> None:
        self._push_undo()
        if kind and kind in KINDS:
            step.kind = kind
        if operator is not None:
            step.operator = int(max(0, min(SLOT_COUNT - 1, operator)))
        if label is not None:
            step.label = str(label or "").strip()[:200]
        if duration is not None:
            step.duration = duration
        if skill is not None:
            step.skill = str(skill or "").strip()[:80]
        if loop_count is not None and step.kind == "loop_start":
            from endbox.modules.timeline.model import normalize_loop_count

            step.loop_count = normalize_loop_count(loop_count)
        step.clamp()
        self._commit()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot_doc())
        self._restore_doc(self._undo.pop())
        self._commit(save=False)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot_doc())
        self._restore_doc(self._redo.pop())
        self._commit(save=False)
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear_steps(self) -> int:
        self._push_undo()
        count = self.model.clear()
        if count:
            self._commit()
        else:
            self._undo.pop()
        return count

    def _snapshot_doc(self) -> dict:
        return {"name": self.axis_name, "team": self.team.to_list(), "model": self.model.to_dict()}

    def _restore_doc(self, doc: dict) -> None:
        self.axis_name = str(doc.get("name") or "")
        self.team = Team.from_list(doc.get("team") or [])
        self.model = TimelineModel.from_dict(doc.get("model") or {})

    def _push_undo(self) -> None:
        self._undo.append(self._snapshot_doc())
        if len(self._undo) > 50:
            self._undo = self._undo[-50:]
        self._redo.clear()

    def _commit(self, *, save: bool = True) -> None:
        self._emit_state()
        if save and self._autosave:
            self._save_timer.start()

    def _emit_state(self) -> None:
        try:
            self._bus.emit(Topics.TIMELINE_STATE, state=self.snapshot())
        except Exception:  # noqa: BLE001
            log.exception("timeline.state 广播失败")

    # -- 设置 ---------------------------------------------------------------
    def _settings_get(self, path: str, default):
        if self.ctx is None:
            return default
        try:
            return self.ctx.settings.get(path, default)
        except Exception:  # noqa: BLE001
            return default

    def _settings_set(self, path: str, value) -> None:
        if self.ctx is None:
            return
        try:
            self.ctx.settings.set(path, value, save=True)
        except Exception:  # noqa: BLE001
            pass

    def keys(self) -> dict:
        return keys_from_settings(self._settings_get("timeline.keys", {}))

    def set_keys(self, mapping: dict) -> dict:
        # 局部更新：未传的字段沿用当前设置，避免一次改键把其它自定义键冲回默认。
        merged = self.keys()
        if isinstance(mapping, dict):
            merged.update(mapping)
        keys = keys_from_settings(merged)
        self._settings_set("timeline.keys", keys)
        self.input.set_keys(keys)
        return keys

    def run_settings(self) -> dict:
        data = normalize_run_settings(self._settings_get("timeline.run", {}))
        data.setdefault("gamepad", RUN_DEFAULTS["gamepad"])
        data.setdefault("hold_ms", RUN_DEFAULTS["hold_ms"])
        raw = self._settings_get("timeline.run", {})
        if isinstance(raw, dict):
            gamepad = str(raw.get("gamepad") or "").strip().lower()
            if gamepad in ("off", "xbox", "ds"):
                data["gamepad"] = gamepad
            try:
                data["hold_ms"] = max(80, min(2000, int(raw.get("hold_ms", data["hold_ms"]))))
            except (TypeError, ValueError):
                data["hold_ms"] = RUN_DEFAULTS["hold_ms"]
            try:
                if raw.get("card_count") is not None:
                    data["card_count"] = max(3, min(9, int(raw.get("card_count"))))
                else:
                    data["card_count"] = max(3, min(9, int(raw.get("next_count", 3)) + 2))
            except (TypeError, ValueError):
                data["card_count"] = RUN_DEFAULTS["card_count"]
            data["next_count"] = max(0, data["card_count"] - 2)
            if "repeat_all" in raw:
                data["repeat_all"] = bool(raw.get("repeat_all"))
            display = str(raw.get("input_display") or data.get("input_display") or "kb").lower()
            data["input_display"] = "pad" if display == "pad" else "kb"
            position = raw.get("pos")
            if isinstance(position, (list, tuple)) and len(position) == 2:
                data["pos"] = [int(position[0]), int(position[1])]
        return data

    def set_run_settings(self, updates: dict) -> dict:
        merged = self.run_settings()
        if isinstance(updates, dict):
            merged.update(updates)
        merged = normalize_run_settings(merged)
        gamepad = str(merged.get("gamepad") or "off").lower()
        merged["gamepad"] = gamepad if gamepad in ("off", "xbox", "ds") else "off"
        try:
            merged["hold_ms"] = max(80, min(2000, int(merged.get("hold_ms", 280))))
        except (TypeError, ValueError):
            merged["hold_ms"] = 280
        try:
            merged["card_count"] = max(3, min(9, int(merged.get("card_count", RUN_DEFAULTS["card_count"]))))
        except (TypeError, ValueError):
            merged["card_count"] = RUN_DEFAULTS["card_count"]
        merged["next_count"] = max(0, merged["card_count"] - 2)
        merged["repeat_all"] = bool(merged.get("repeat_all", RUN_DEFAULTS["repeat_all"]))
        merged["input_display"] = "pad" if str(merged.get("input_display") or "kb").lower() == "pad" else "kb"
        position = merged.get("pos")
        if isinstance(position, (list, tuple)) and len(position) == 2:
            merged["pos"] = [int(position[0]), int(position[1])]
        else:
            merged.pop("pos", None)
        self._settings_set("timeline.run", merged)
        if self._run_overlay is not None:
            self._run_overlay.set_settings(merged)
        self.input.set_runtime_options(hold_ms=merged["hold_ms"], gamepad=merged["gamepad"])
        return merged

    # -- 运行态 -------------------------------------------------------------
    def step_defaults(self, kind: str, operator: int) -> dict:
        """插入新步骤时的默认技能名与预计耗时（来自 operators.json 真数据，缺失回退估计值）。"""
        normalized = kind if kind in KEY_KINDS else ""
        if not normalized:
            return {"duration": 0.0, "skill": ""}
        name = self.team.name(operator)
        info = self.assets.skill(name, normalized) if name else None
        duration = float(info.duration) if info is not None and info.duration else float(
            DEFAULT_DURATIONS.get(normalized, 0.0)
        )
        return {"duration": round(duration, 3), "skill": info.name if info is not None else ""}

    def estimate_duration(self, kind: str, name: str, duration: float = 0.0) -> float:
        """单步预计耗时：手填优先；否则用真数据动画时长；再否则用默认估计值。"""
        value = float(duration or 0.0)
        if value:
            return round(value, 3)
        if kind not in KEY_KINDS:
            return 0.0
        return round(float(self.assets.duration(name, kind, DEFAULT_DURATIONS.get(kind, 0.0))), 3)

    def stats(self) -> dict:
        """真实统计：预计总耗时（缺耗时用技能动画估算）/ 技力消耗 / 缺耗时步骤数。"""
        model = self.model
        key_steps = model.key_steps()
        total = 0.0
        missing = 0
        for step in key_steps:
            duration = float(step.duration or 0.0)
            if not duration:
                missing += 1
            total += self.estimate_duration(step.kind, self.team.name(step.operator), duration)
        return {
            "total_duration": round(total, 3),
            "set_duration": model.total_duration(),
            "energy_cost": model.energy_cost(),
            "key_steps": len(key_steps),
            "duration_missing": missing,
        }

    def input_hints(self) -> dict:
        """当前键位提示（真实键位：数字短按 / 长按 / E / F1-F4）。"""
        keys = self.keys()
        return {
            "skill": tr("timeline.run.hint_skill"),
            "link": str(keys.get("link") or "E"),
            "ult": tr("timeline.run.hint_ult"),
            "switch": [switch_key(keys, slot) for slot in range(SLOT_COUNT)],
            "switch_text": tr(
                "timeline.run.hint_switch",
                first=switch_key(keys, 0),
                last=switch_key(keys, SLOT_COUNT - 1),
            ),
        }

    def _ensure_overlay(self):
        if not self._overlay_enabled:
            return None
        if self._run_overlay is not None:
            return self._run_overlay
        try:
            from PyQt6.QtWidgets import QApplication

            if QApplication.instance() is None:
                return None
            from endbox.modules.timeline.run_overlay import RunOverlay

            self._run_overlay = RunOverlay(self.run_settings())
            self._run_overlay.action.connect(self.handle_overlay_action)
            self._run_overlay.position_changed.connect(self.save_overlay_position)
        except Exception:  # noqa: BLE001
            log.exception("运行提示条创建失败")
            self._run_overlay = None
        return self._run_overlay

    def overlay(self):
        return self._run_overlay

    @property
    def run_active(self) -> bool:
        return self._run_active

    def start_run(self) -> bool:
        if self._run_active:
            self.stop_run("restart")
        sequence = RunSequence.from_model(self.model, self.team.to_list(), self.keys(), lookup=self.assets.get)
        if sequence.total == 0:
            self.last_error = tr("timeline.run.no_steps")
            self._emit_run({"active": False, "error": self.last_error})
            return False
        settings = self.run_settings()
        self._run_engine = RunEngine(
            sequence,
            loops_enabled=True,
            repeat_all=bool(settings.get("repeat_all", True)),
        )
        self._run_active = True
        self._run_hint = ""
        self._wrong_seq = 0
        self._advance_seq = 0
        self._wrong_gindex = None
        self.input.set_keys(self.keys())
        self.input.set_runtime_options(hold_ms=settings["hold_ms"], gamepad=settings["gamepad"])
        self.input.start()
        overlay = self._ensure_overlay()
        if overlay is not None:
            overlay.set_settings(settings)
            overlay.apply_state(self.run_state())
            overlay.show_overlay()
        self._emit_run()
        log.info("run started: %s key steps", sequence.total)
        return True

    def stop_run(self, reason: str = "") -> None:
        was_active = self._run_active
        self._run_active = False
        self._run_engine = None
        self._run_hint = ""
        self._hint_timer.stop()
        self._finish_timer.stop()
        try:
            self.input.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._run_overlay is not None:
            try:
                self._run_overlay.hide_overlay()
            except Exception:  # noqa: BLE001
                pass
        if was_active:
            self._emit_run({"active": False, "exit_reason": reason})

    def feed_input(self, token: str) -> str:
        return self.handle_input_token(token)

    def _admin_info(self) -> tuple[bool, str]:
        try:
            from endbox.core.elevation import admin_hint, is_admin

            return bool(is_admin()), str(admin_hint())
        except Exception:  # noqa: BLE001
            return False, tr("timeline.run.admin_unknown")

    def handle_input_token(self, token: str) -> str:
        token = normalize_token(token)
        if not token:
            return "ignored"
        # 数字 token 已由输入适配层区分好：短按 = 战技（1~4），长按 = 终结（HOLD+n）
        keys = self.keys()
        if token == normalize_token(keys.get("exit", "F8")):
            if self._run_active:
                self.stop_run("F8")
                return "control"
            return "ignored"
        if not self._run_active or self._run_engine is None:
            return "ignored"
        engine = self._run_engine
        if token == normalize_token(keys.get("reset", "F9")):
            engine.reset()
            self._run_hint = ""
            self._wrong_gindex = None
            self._advance_seq += 1
            self._emit_run({"reset": True})
            return "control"
        if token in (normalize_token(keys.get("alpha_down", "F6")), normalize_token(keys.get("alpha_up", "F7"))):
            delta = -0.1 if token == normalize_token(keys.get("alpha_down", "F6")) else 0.1
            self.set_run_settings({"opacity": round(self.run_settings()["opacity"] + delta, 2)})
            self._emit_run()
            return "control"
        if token == normalize_token(keys.get("loop_toggle", "F10")):
            # F10：整轴反复开关（关掉才会在结尾结束）
            engine.set_repeat_all(not engine.repeat_all)
            self._emit_run()
            return "control"
        if token == normalize_token(keys.get("loop_jump", "F11")):
            engine.jump_loop_start()
            self._emit_run()
            return "control"
        # 重击格只做提示、运行时不按键：H 收到也忽略，不能算"按错"（实机重击设定）
        heavy_key = normalize_token(keys.get("heavy", "H"))
        if heavy_key and token == heavy_key:
            return "ignored"
        if engine.finished:
            return "ignored"
        if engine.matches(token):
            engine.advance()
            self._run_hint = ""
            self._advance_seq += 1
            if engine.finished:
                self._emit_run({"finished": True})
                self._finish_timer.start(FINISH_MS)
            else:
                self._emit_run({"correct": True})
            return "ok"
        # 按错：直接跳过这一步（不提示），提示条那边把这张卡标红并抖一下
        skipped = engine.index
        self._run_hint = ""
        engine.advance()
        self._wrong_gindex = int(skipped)
        self._wrong_seq += 1
        if engine.finished:
            self._emit_run({"finished": True, "wrong": True})
            self._finish_timer.start(FINISH_MS)
        else:
            self._emit_run({"wrong": True})
        return "wrong"

    def handle_overlay_action(self, action: str) -> None:
        """提示条右键菜单 / 拖动位置回调。"""
        engine = self._run_engine
        if action == "close":
            self.stop_run("overlay-close")
            return
        if action == "open_main":
            window = getattr(self.ctx, "main_window", None)
            if window is not None:
                try:
                    window.showNormal()
                    window.raise_()
                    window.activateWindow()
                except Exception:  # noqa: BLE001
                    pass
            return
        if engine is None or not self._run_active:
            return
        if action == "next_segment":
            engine.jump_segment(1)
        elif action == "prev_segment":
            engine.jump_segment(-1)
        elif action == "restart":
            engine.reset()
        self._emit_run()

    def save_overlay_position(self, x: int, y: int) -> None:
        settings = self.run_settings()
        settings["pos"] = [int(x), int(y)]
        self._settings_set("timeline.run", settings)

    def _clear_run_hint(self) -> None:
        if not self._run_hint:
            return
        self._run_hint = ""
        self._emit_run()

    def _on_finish_timeout(self) -> None:
        if self._run_active and self._run_engine is not None and self._run_engine.finished:
            self.stop_run("completed")

    def _emit_run(self, extra: dict | None = None) -> None:
        state = self.run_state()
        if extra:
            state.update(extra)
        try:
            self._bus.emit(RUN_TOPIC, state=state)
        except Exception:  # noqa: BLE001
            log.exception("timeline.run 广播失败")
        if self._run_overlay is not None:
            try:
                self._run_overlay.apply_state(state)
            except Exception:  # noqa: BLE001
                pass

    def run_state(self) -> dict:
        engine = self._run_engine
        admin, admin_hint = self._admin_info()
        base = {
            "active": self._run_active,
            "finished": bool(engine.finished) if engine is not None else False,
            "index": engine.index if engine is not None else 0,
            "total": engine.total if engine is not None else 0,
            "loops_enabled": engine.loops_enabled if engine is not None else True,
            "hint": self._run_hint,
            "wrong_seq": self._wrong_seq,
            "advance_seq": self._advance_seq,
            "wrong_gindex": self._wrong_gindex,
            "keys": self.keys(),
            "settings": self.run_settings(),
            "input_available": self.input.available,
            "input_status": self.input.status_text,
            "admin": admin,
            "admin_hint": admin_hint,
            "progress_text": "0 / 0",
            "step": None,
            "next": None,
            "loop": {},
            "segment": {},
        }
        if engine is not None and engine.total:
            base["progress_text"] = f"{engine.index + 1} / {engine.total}"
            base["segment"] = engine.segment
            base["loop"] = engine.loop_status() or {}
            step = engine.current
            base["step"] = step.to_dict() if step is not None else None
            preview = engine.peek()
            base["next"] = preview.to_dict() if preview is not None else None
            settings = base["settings"]
            card_count = int(settings.get("card_count", 5) or 5)
            card_count = max(3, min(9, card_count))
            next_count = max(0, card_count - 2)
            display = str(settings.get("input_display") or "kb")
            pad_mode = str(settings.get("gamepad") or "xbox").lower()
            cards = []
            # 往前 1 格（刚过去的）+ 当前 1 格 + 往后 card_count-2 格；起点不够时往后补满
            start = engine.index - 1
            end = engine.index + card_count - 2
            if start < 0:
                end += -start
                start = 0
            for idx in range(start, end + 1):
                card_step = engine.sequence.step(idx)
                if card_step is None:
                    continue
                card = card_step.to_dict()
                card["gindex"] = int(idx)
                card["kind_label"] = card_step.kind_label
                card["state"] = "past" if idx < engine.index else ("current" if idx == engine.index else "next")
                card["pad_key"] = _pad_key_for(card_step, display, pad_mode)
                card["duration"] = self.estimate_duration(
                    card_step.kind, card_step.operator_name, card_step.duration
                )
                if card_step.is_block:
                    operator = self.assets.get(self.team.name(card_step.operator))
                    if operator is not None:
                        card["avatar"] = str(operator.avatar) if operator.avatar else None
                        if card_step.kind in ("skill", "link", "ult"):
                            icon = operator.asset(card_step.kind)
                        else:
                            icon = None
                        if icon is None and card_step.kind == "heavy":
                            icon = self.assets.gen_icon(name, "heavy")
                            if icon is None:
                                candidate = Path(self.assets.root) / "glyph" / "heavy.png"
                                icon = candidate if candidate.is_file() else None
                        card["icon"] = str(icon) if icon else None
                cards.append(card)
            base["cards"] = cards
            base["display"] = display
            base["card_count"] = card_count
            base["next_count"] = next_count
            base["input_hints"] = self.input_hints()
            base["stats"] = self.stats()
            if engine.finished:
                base["finish_text"] = tr("timeline.run.finish_text", total=engine.total)
            if not self.input.available and self.input.last_error:
                base["hint"] = self.input.last_error
            if not admin:
                base["warning"] = tr("timeline.run.admin_warning")
        return base

    # -- 轴库 ---------------------------------------------------------------
    def library_list(self, query: str = "") -> list[dict]:
        return self.library.list(query)

    def library_save(self, name: str, *, overwrite_id: str | None = None):
        entry = self.library.add(
            name or self.axis_name or tr("timeline.defaults.unnamed_axis"),
            self.team.to_list(),
            self.model.to_dict(),
            entry_id=overwrite_id,
        )
        if entry is not None:
            self.axis_name = entry["name"]
            self._emit_state()
        return entry

    def library_load(self, entry_id: str) -> bool:
        entry = self.library.get(entry_id)
        if entry is None:
            return False
        self._push_undo()
        self.axis_name = str(entry.get("name") or "")
        self.team = Team.from_list(entry.get("team") or [])
        self.model = TimelineModel.from_any(entry.get("model") or {})
        self._commit()
        return True

    def library_delete(self, entry_id: str) -> bool:
        return self.library.delete(entry_id)

    # -- 编队库 -------------------------------------------------------------
    def team_library_list(self, query: str = "") -> list[dict]:
        return self.library.team_list(query)

    def team_library_save(self, name: str, *, overwrite_id: str | None = None):
        return self.library.team_add(name or self.team_summary(), self.team.to_list(), entry_id=overwrite_id)

    def team_library_load(self, entry_id: str) -> bool:
        entry = self.library.team_get(entry_id)
        if entry is None:
            return False
        self._push_undo()
        self.team = Team.from_list(entry.get("slots") or [])
        self._commit()
        return True

    def team_library_delete(self, entry_id: str) -> bool:
        return self.library.team_delete(entry_id)

    # -- 分享码 -------------------------------------------------------------
    def export_share(self) -> str:
        return share_code.encode(
            share_code.make_payload(self.axis_name or tr("timeline.defaults.unnamed_axis"), self.team.to_list(), self.model.to_dict())
        )

    def import_share(self, text: str) -> dict:
        data = share_code.decode(text)
        model = TimelineModel.from_dict(data["model"])
        team = Team.from_list(data.get("team") or [])
        if self.assets.available:
            for index, name in enumerate(team.to_list()):
                if name and self.assets.get(name) is None:
                    team.set(index, "")
        if not model.steps:
            raise share_code.ShareCodeError(tr("timeline.share.errors.no_steps"))
        self._push_undo()
        self.axis_name = (data.get("name") or "").strip() or tr("timeline.defaults.imported_axis")
        self.team = team
        self.model = model
        self._commit()
        return {
            "name": self.axis_name,
            "team": self.team.to_list(),
            "steps": len(self.model.steps),
            "migrated": bool(data.get("migrated")),
        }

    # -- 持久化 -------------------------------------------------------------
    def _payload(self) -> dict:
        return {"version": SCHEMA_VERSION, "name": self.axis_name, "team": self.team.to_list(),
                "model": self.model.to_dict()}

    def load(self) -> None:
        self.last_error = None
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:  # noqa: BLE001
            self.last_error = tr("timeline.errors.file_read", error=exc)
            return
        if not isinstance(raw, dict):
            self.last_error = tr("timeline.errors.file_format")
            return
        try:
            version = int(raw.get("version", 0) or 0)
            self.team = Team.from_list(raw.get("team") if isinstance(raw.get("team"), list) else [])
            self.axis_name = str(raw.get("name") or "").strip()[:60]
            model_data = raw.get("model") if isinstance(raw.get("model"), dict) else raw
            if isinstance(model_data.get("steps"), list):
                self.model = TimelineModel.from_dict(model_data)
            else:
                self.model = TimelineModel.from_any(model_data)
                self.last_error = tr("timeline.errors.file_migrated")
            if self.assets.available:
                for index, name in enumerate(self.team.to_list()):
                    if name and self.assets.get(name) is None:
                        self.team.set(index, "")
        except Exception as exc:  # noqa: BLE001
            self.last_error = tr("timeline.errors.file_format_error", error=exc)
            self.model = TimelineModel()
            self.team = Team()

    def save(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix(".tmp")
            temp.write_text(json.dumps(self._payload(), ensure_ascii=False, indent=2), "utf-8")
            os.replace(temp, self._path)
            self.last_error = None
            return True
        except OSError as exc:  # noqa: PERF203
            self.last_error = tr("timeline.errors.file_save", error=exc)
            return False

    def stop(self) -> None:
        self.stop_run("app-stop")
        self._save_timer.stop()
        self.save()

    # -- 文案 ---------------------------------------------------------------
    @staticmethod
    def kind_label(kind: str) -> str:
        return KIND_LABELS.get(kind, kind)

    def team_summary(self) -> str:
        names = [name for name in self.team.to_list() if name]
        return " / ".join(names) if names else tr("timeline.defaults.empty_team")
