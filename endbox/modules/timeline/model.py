# -*- coding: utf-8 -*-
"""排轴数据模型（v3，按键提示式**有序步骤序列**）。

排轴 = 一串按顺序执行的步骤，没有时间轴/开始时间/时长。
步骤类型：
    skill       战技（绑定 1~4 号位；真实键位 = 数字键 1~4 短按）
    link        连携技（绑定 1~4 号位；真实键位 = E，全队共用）
    ult         终结技（绑定 1~4 号位；真实键位 = 长按数字键 1~4）
    switch      切换干员（绑定 1~4 号位；真实键位 = F1/F2/F3/F4）
    heavy       重击（只做提示，运行时自动跳过；真实键位 = 鼠标左键 / 普攻连段末段，没有独立键盘键）
    comment     注释（纯文字，运行时跳过）
    break       换行（仅编辑期排版，运行时跳过）
    segment     分段标记（显示「第 N 段」，运行时按段显示进度）
    loop_start  反复开始（带组号 + 次数，-1 = 无限）
    loop_end    反复结束（与最近的开始配对，可嵌套）

键位来源：MaaEnd 项目 ``assets/tasks/setting/Keymap.json``（对齐游戏内设置）：
战技 = 数字 1~4 短按；终结技 = 长按同一数字；连携 = E；切换干员 = F1~F4。
旧存档里的 Q+n / E+n / ALT+n 属于旧版键位语义，读取时按新语义迁移（步骤信息不丢）。

这里不依赖 Qt，所有编辑逻辑都能在命令行 / 单元测试里跑。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from endbox.core.text import t as tr

KINDS = ("skill", "link", "ult", "switch", "heavy", "comment", "break", "segment", "loop_start", "loop_end")
BLOCK_KINDS = ("skill", "link", "ult", "switch", "heavy")
KEY_KINDS = ("skill", "link", "ult", "switch")
MARKER_KINDS = ("comment", "break", "segment", "loop_start", "loop_end")
SLOT_COUNT = 4
DEFAULT_LOOP_COUNT = 2
MAX_LOOP_COUNT = 99
MAX_LABEL = 200
MAX_STEPS = 2000
MAX_DURATION = 60.0
SCHEMA_VERSION = 3

KIND_LABELS = {
    "skill": tr("timeline.kind.skill"),
    "link": tr("timeline.kind.link"),
    "ult": tr("timeline.kind.ult"),
    "switch": tr("timeline.kind.switch"),
    "heavy": tr("timeline.kind.heavy"),
    "comment": tr("timeline.kind.comment"),
    "break": tr("timeline.kind.break"),
    "segment": tr("timeline.kind.segment"),
    "loop_start": tr("timeline.kind.loop_start"),
    "loop_end": tr("timeline.kind.loop_end"),
}
KIND_ORDER = {kind: index for index, kind in enumerate(KINDS)}


def new_uid() -> str:
    return uuid.uuid4().hex[:12]


def new_loop_group() -> str:
    return "L" + uuid.uuid4().hex[:6].upper()


def normalize_loop_count(value) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LOOP_COUNT
    if count == -1:
        return -1
    return max(1, min(MAX_LOOP_COUNT, count))


@dataclass
class Step:
    """序列里的一步。只有 kind 决定它的语义。

    ``duration`` 是「预计耗时」（秒，手动可改；0 = 未设置，运行时按技能动画估算）；
    ``skill`` 是绑定的技能名/技能 id（来自游戏数据，可空 = 用干员默认技能）。
    """

    kind: str = "skill"
    operator: int = 0
    label: str = ""
    loop_count: int = 0
    group: str = ""
    duration: float = 0.0
    skill: str = ""
    uid: str = field(default_factory=new_uid)

    def clamp(self) -> "Step":
        if self.kind not in KINDS:
            self.kind = "skill"
        self.operator = int(max(0, min(SLOT_COUNT - 1, int(self.operator))))
        self.label = str(self.label or "").strip()[:MAX_LABEL]
        try:
            duration = float(self.duration or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        self.duration = round(max(0.0, min(MAX_DURATION, duration)), 3)
        self.skill = str(self.skill or "").strip()[:80]
        if self.kind not in KEY_KINDS:
            self.duration = 0.0
            self.skill = ""
        if self.kind == "loop_start":
            self.loop_count = normalize_loop_count(self.loop_count or DEFAULT_LOOP_COUNT)
            self.group = str(self.group or new_loop_group())
        elif self.kind == "loop_end":
            self.loop_count = 0
        else:
            self.loop_count = 0
            self.group = ""
        return self

    @property
    def is_block(self) -> bool:
        return self.kind in BLOCK_KINDS

    @property
    def is_key(self) -> bool:
        """需要玩家按键的步骤（重击只是提示，不算按键步骤）。"""
        return self.kind in KEY_KINDS

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def to_dict(self) -> dict:
        data = {"kind": self.kind, "operator": int(self.operator)}
        if self.label:
            data["label"] = self.label
        if self.kind == "loop_start":
            data["loop_count"] = int(self.loop_count)
        if self.kind in ("loop_start", "loop_end"):
            data["group"] = self.group
        if self.kind in KEY_KINDS and self.duration:
            data["duration"] = round(float(self.duration), 3)
        if self.kind in KEY_KINDS and self.skill:
            data["skill"] = self.skill
        if self.uid:
            data["uid"] = self.uid
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Step | None":
        if not isinstance(data, dict):
            return None
        kind = str(data.get("kind") or "").strip()
        if kind not in KINDS:
            return None
        try:
            operator = int(data.get("operator", 0))
        except (TypeError, ValueError):
            operator = 0
        step = cls(
            kind=kind,
            operator=max(0, min(SLOT_COUNT - 1, operator)),
            label=str(data.get("label") or "")[:MAX_LABEL],
            loop_count=normalize_loop_count(data.get("loop_count", DEFAULT_LOOP_COUNT)),
            group=str(data.get("group") or ""),
            duration=data.get("duration", 0.0),
            skill=str(data.get("skill") or ""),
            uid=str(data.get("uid") or new_uid()),
        )
        return step.clamp()


@dataclass
class Team:
    slots: list[str] = field(default_factory=lambda: ["" for _ in range(SLOT_COUNT)])

    def normalized(self) -> "Team":
        names = [str(n or "").strip() for n in (self.slots or [])][:SLOT_COUNT]
        while len(names) < SLOT_COUNT:
            names.append("")
        self.slots = names
        return self

    def set(self, index: int, name: str) -> None:
        self.normalized()
        if 0 <= int(index) < SLOT_COUNT:
            self.slots[int(index)] = str(name or "").strip()

    def name(self, index: int) -> str:
        self.normalized()
        try:
            return self.slots[int(index)]
        except (IndexError, ValueError):
            return ""

    def filled(self) -> int:
        return sum(1 for n in self.slots if n)

    def to_list(self) -> list[str]:
        return list(self.normalized().slots)

    @classmethod
    def from_list(cls, data) -> "Team":
        return cls(slots=list(data) if isinstance(data, (list, tuple)) else []).normalized()


# --- 序列模型 -------------------------------------------------------------


@dataclass
class TimelineModel:
    """一条排轴（有序步骤）。类名保留以兼容旧导入。"""

    steps: list[Step] = field(default_factory=list)

    def normalize(self) -> "TimelineModel":
        clean: list[Step] = []
        for step in self.steps[:MAX_STEPS]:
            if isinstance(step, Step):
                clean.append(step.clamp())
        self.steps = clean
        return self

    # -- 查询 ---------------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.steps)

    def counts(self) -> dict[str, int]:
        out = {kind: 0 for kind in KINDS}
        for step in self.steps:
            out[step.kind] = out.get(step.kind, 0) + 1
        return out

    def total_duration(self) -> float:
        """步骤的预计总耗时（秒）：只累加已设置 duration 的按键步骤。"""
        return round(sum(float(step.duration) for step in self.steps if step.kind in KEY_KINDS), 3)

    def energy_cost(self) -> int:
        """技力消耗估算：每步战技消耗 1 格（全队 3 格技力共享）。"""
        return sum(1 for step in self.steps if step.kind == "skill")

    def key_steps(self) -> list["Step"]:
        return [step for step in self.steps if step.kind in KEY_KINDS]

    def kind_sequence(self) -> list[str]:
        return [step.kind for step in self.steps]

    def segments(self) -> list[dict]:
        """按分段标记切分；返回 [{index,name,start,end,count}]（step 下标）。"""
        markers = [(i, step) for i, step in enumerate(self.steps) if step.kind == "segment"]
        segments: list[dict] = []
        if not markers:
            return [{"index": 0, "name": tr("timeline.defaults.segment_name", index=1), "start": 0, "end": len(self.steps) - 1,
                     "count": len(self.steps)}]
        first_start = markers[0][0]
        segments.append({
            "index": 0,
            "name": tr("timeline.defaults.segment_name", index=1),
            "start": 0,
            "end": max(0, first_start - 1),
            "count": first_start,
        })
        for order, (position, step) in enumerate(markers):
            end = (markers[order + 1][0] - 1) if order + 1 < len(markers) else len(self.steps) - 1
            segments.append({
                "index": order + 1,
                "name": step.label or tr("timeline.defaults.segment_name", index=order + 2),
                "start": position,
                "end": max(position, end),
                "count": max(0, end - position),
            })
        return segments

    def segment_of(self, step_index: int) -> dict:
        for segment in self.segments():
            if segment["start"] <= step_index <= segment["end"]:
                return segment
        return {"index": 0, "name": tr("timeline.defaults.segment_name", index=1), "start": 0, "end": 0, "count": 0}

    def loops(self) -> list[dict]:
        """配对反复开始/结束，返回 [{start,end,count,depth,group}]（step 下标，可嵌套）。"""
        open_by_group: dict[str, int] = {}
        stack: list[tuple[str, int]] = []
        pairs: list[dict] = []
        for index, step in enumerate(self.steps):
            if step.kind == "loop_start":
                group = step.group or new_loop_group()
                step.group = group
                stack.append((group, index))
                open_by_group[group] = index
            elif step.kind == "loop_end":
                start = None
                if step.group and step.group in open_by_group:
                    start = open_by_group.pop(step.group)
                    stack = [(g, i) for g, i in stack if g != step.group]
                elif stack:
                    group, start = stack.pop()
                    step.group = group
                if start is None or start >= index:
                    continue
                count = 0
                for item in self.steps[start:index]:
                    if item.kind == "loop_start":
                        count = item.loop_count
                        break
                pairs.append({
                    "group": step.group or "",
                    "start": start,
                    "end": index,
                    "count": count or DEFAULT_LOOP_COUNT,
                    "depth": 0,
                })
        for pair in pairs:
            depth = 0
            for other in pairs:
                if other is pair:
                    continue
                if (
                    other["start"] <= pair["start"]
                    and other["end"] >= pair["end"]
                    and (other["start"] < pair["start"] or other["end"] > pair["end"])
                ):
                    depth += 1
            pair["depth"] = min(depth, 3)
        pairs.sort(key=lambda item: (item["start"], -item["end"]))
        return pairs

    # -- 编辑 ---------------------------------------------------------------
    def add_step(self, kind: str, operator: int = 0, at: int | None = None, *, label: str = "",
                 loop_count: int = DEFAULT_LOOP_COUNT, group: str = "",
                 duration: float = 0.0, skill: str = "") -> Step:
        kind = kind if kind in KINDS else "skill"
        step = Step(
            kind=kind,
            operator=int(operator),
            label=str(label or "")[:MAX_LABEL],
            loop_count=normalize_loop_count(loop_count) if kind == "loop_start" else 0,
            group=str(group or "")[:24],
            duration=duration,
            skill=skill,
            uid=new_uid(),
        )
        if kind == "loop_start" and not step.group:
            step.group = new_loop_group()
        step.clamp()
        if kind == "loop_end":
            open_loops = [(i, item) for i, item in enumerate(self.steps) if item.kind == "loop_start"
                          and item.group not in {s.group for s in self.steps if s.kind == "loop_end"}]
            if open_loops and not step.group:
                step.group = open_loops[-1][1].group
        if at is None:
            at = len(self.steps)
        at = max(0, min(len(self.steps), int(at)))
        self.steps.insert(at, step)
        return step

    def insert_step(self, step: Step, at: int | None = None) -> Step:
        step.clamp()
        index = len(self.steps) if at is None else max(0, min(len(self.steps), int(at)))
        self.steps.insert(index, step)
        return step

    def remove_steps(self, steps: Iterable[Step]) -> int:
        targets = {id(step) for step in steps}
        before = len(self.steps)
        self.steps = [step for step in self.steps if id(step) not in targets]
        return before - len(self.steps)

    def move_steps(self, steps: Iterable[Step], delta: int) -> bool:
        """把选中的步骤整体左移/右移（保持相对顺序，不越界）。"""
        chosen = [step for step in self.steps if any(step is item for item in steps)]
        if not chosen or delta == 0:
            return False
        indexes = [self.steps.index(step) for step in chosen]
        low, high = min(indexes), max(indexes)
        if delta < 0 and low <= 0:
            return False
        if delta > 0 and high >= len(self.steps) - 1:
            return False
        if delta == 1:
            for index in reversed(indexes):
                self.steps[index], self.steps[index + 1] = self.steps[index + 1], self.steps[index]
        elif delta == -1:
            for index in indexes:
                self.steps[index], self.steps[index - 1] = self.steps[index - 1], self.steps[index]
        elif delta > 0:
            for _ in range(abs(delta)):
                self.move_steps(chosen, 1)
        else:
            for _ in range(abs(delta)):
                self.move_steps(chosen, -1)
        return True

    def reorder_steps(self, steps: Iterable[Step], target_index: int) -> bool:
        """把选中的步骤移动到 target_index 之前（拖拽重排）。"""
        chosen = [step for step in self.steps if any(step is item for item in steps)]
        if not chosen:
            return False
        remaining = [step for step in self.steps if not any(step is item for item in chosen)]
        insert = max(0, min(len(remaining), int(target_index)))
        block = [step for step in self.steps if any(step is item for item in chosen)]
        self.steps = remaining[:insert] + block + remaining[insert:]
        return True

    def duplicate_steps(self, steps: Iterable[Step]) -> list[Step]:
        copies: list[Step] = []
        for step in steps:
            data = step.to_dict()
            data["uid"] = new_uid()
            clone = Step.from_dict(data)
            if clone is not None:
                copies.append(clone)
        return copies

    def clear(self) -> int:
        count = len(self.steps)
        self.steps.clear()
        return count

    # -- 序列化 -------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"steps": [step.to_dict() for step in self.steps]}

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineModel":
        model = cls()
        if isinstance(data, dict):
            raw_steps = data.get("steps") if isinstance(data.get("steps"), list) else []
            for raw in raw_steps:
                step = Step.from_dict(raw)
                if step is not None:
                    model.steps.append(step)
        return model.normalize()

    @classmethod
    def from_any(cls, data: dict) -> "TimelineModel":
        """兼容读取：``steps`` 序列优先，否则按旧 v1/v2 时间轴 ``events`` 迁移。"""
        if not isinstance(data, dict):
            return cls()
        if isinstance(data.get("steps"), list):
            return cls.from_dict(data)
        return cls.from_legacy(data)

    # -- 旧时间轴（v1 / v2）最小迁移 ----------------------------------------
    @classmethod
    def from_legacy(cls, data: dict) -> "TimelineModel":
        """v1/v2 时间轴 → 序列。按 start 排序展开；反复取最后一次的区间近似。

        时间信息会被丢弃，Buff 覆盖也丢弃（v3 不再有时间概念）。
        """
        model = cls()
        if not isinstance(data, dict):
            return model
        events: list[dict] = []
        model_data = data.get("model") if isinstance(data.get("model"), dict) else data
        if isinstance(model_data, dict) and isinstance(model_data.get("events"), list):
            events = [event for event in model_data["events"] if isinstance(event, dict)]
        elif isinstance(data.get("events"), list):
            events = [event for event in data["events"] if isinstance(event, dict)]
        events = [event for event in events if event.get("kind") in KINDS or event.get("kind") in BLOCK_KINDS]
        events.sort(key=lambda event: (float(event.get("start", 0.0) or 0.0), int(event.get("operator", 0) or 0)))
        loops: list[tuple[float, float, int]] = []
        for event in events:
            kind = str(event.get("kind") or "")
            if kind == "repeat":
                try:
                    start_time = float(event.get("loop_from", event.get("start", 0.0)) or 0.0)
                    end_time = float(event.get("loop_to", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                loops.append((start_time, end_time, normalize_loop_count(event.get("loop_count", 2))))
                continue
            if kind not in KINDS:
                continue
            step = Step.from_dict({
                "kind": kind,
                "operator": event.get("operator", 0),
                "label": event.get("label", ""),
            })
            if step is not None:
                model.steps.append(step)
        for start_time, end_time, count in loops:
            model.steps.insert(0, Step.from_dict({
                "kind": "loop_start", "loop_count": count, "label": tr("timeline.defaults.legacy_loop"),
            }) or Step(kind="loop_start"))
            model.steps.append(Step.from_dict({"kind": "loop_end"}) or Step(kind="loop_end"))
        return model.normalize()

    @classmethod
    def load(cls, path: Path | str) -> "TimelineModel | None":
        try:
            raw = json.loads(Path(path).read_text("utf-8"))
        except FileNotFoundError:
            return None
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(raw, dict):
            return None
        try:
            if isinstance(raw.get("steps"), list):
                return cls.from_dict(raw)
            return cls.from_legacy(raw)
        except Exception:  # noqa: BLE001
            return None

    def save(self, path: Path | str) -> bool:
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), "utf-8")
            os.replace(temp, target)
            return True
        except OSError:
            return False
