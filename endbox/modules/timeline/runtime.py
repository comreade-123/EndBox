# -*- coding: utf-8 -*-
"""运行态（按键提示式按键提示）：把排轴编译成"按对才前进"的步骤序列 + 状态机。

这里不依赖 Qt，方便单元测试：
- ``RunSequence.from_model`` 把事件/分段/反复编译成步骤；
- ``RunEngine`` 负责按对推进、反复循环、回起点、反复开关；
- 输入 token 形如 ``1``（数字短按）/ ``HOLD+1``（长按数字）/ ``E``（连携）/ ``F1``（切换干员）/ ``F8``。

真实键位（对齐游戏内设置，来源 MaaEnd ``Keymap.json``）：
  战技 = 数字 1~4 短按；终结技 = 长按 1~4；连携 = E（全队共用）；
  切换干员 = F1~F4；重击 = 普攻连段末段（鼠标左键，仅提示，不参与按键）。
  运行控制：F8 退出、F9 回第一段、F6 变淡、F7 变实、F10 反复开关、F11 回反复起点。
  旧版 Q+n / E+n / ALT+n 组合键已废弃，输入层仍兼容解析，但运行态不再接受（见 docs/input.md）。
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from endbox.modules.timeline.model import BLOCK_KINDS, KEY_KINDS, KIND_LABELS, SLOT_COUNT
from endbox.core.text import t as tr

DEFAULT_KEYS: dict = {
    # 真实键位：连携 = E；切换干员 = F1~F4（不是数字键）；重击没有独立键盘键。
    "link": "E",
    "switch_1": "F1",
    "switch_2": "F2",
    "switch_3": "F3",
    "switch_4": "F4",
    "heavy": "H",  # 已废弃：只为兼容旧设置文件保留，运行态不再使用
    "exit": "F8",
    "reset": "F9",
    "alpha_down": "F6",
    "alpha_up": "F7",
    "loop_toggle": "F10",
    "loop_jump": "F11",
    "hold_number_ult": True,
    "hold_ms": 280,
}

KEY_FIELDS = (
    ("link", tr("timeline.keys.field.link")),
    ("switch_1", tr("timeline.keys.field.switch_1")),
    ("switch_2", tr("timeline.keys.field.switch_2")),
    ("switch_3", tr("timeline.keys.field.switch_3")),
    ("switch_4", tr("timeline.keys.field.switch_4")),
    ("exit", tr("timeline.keys.field.exit")),
    ("reset", tr("timeline.keys.field.reset")),
    ("alpha_down", tr("timeline.keys.field.alpha_down")),
    ("alpha_up", tr("timeline.keys.field.alpha_up")),
    ("loop_toggle", tr("timeline.keys.field.loop_toggle")),
    ("loop_jump", tr("timeline.keys.field.loop_jump")),
)

_ALIASES = {
    "CONTROL": "CTRL",
    "CTL": "CTRL",
    "OPTION": "ALT",
    "ESCAPE": "ESC",
    "RETURN": "ENTER",
    "SPACEBAR": "SPACE",
}
_TOKEN_RE = re.compile(r"^([A-Z]+)\+?(\d)$")


def normalize_key(value, default: str = "") -> str:
    """把用户填的键名规范成 ``Q`` / ``ALT`` / ``F8`` 这种短 token。"""
    text = str(value or "").strip().upper()
    if not text:
        return default
    text = _ALIASES.get(text, text)
    if text in ("ALT", "CTRL", "SHIFT", "SPACE", "TAB", "ENTER", "ESC"):
        return text
    if len(text) == 1 and (text.isalnum()):
        return text
    if re.fullmatch(r"F\d{1,2}", text):
        return text
    return default


def _legacy_key(value, default: str) -> str:
    """旧键位设置迁移：``Q+n`` / ``E+n`` / ``ALT+n`` 组合写法 → 规范单键。

    - 旧 ``link="E+2"`` → ``E``（连携键保留基础键）；
    - 旧 ``skill="Q"`` / ``ult="ALT"`` 这类修饰键 → 用默认值（新模型不再需要修饰键）；
    - 无法识别的值 → 默认值（不丢功能，只是回默认键位）。
    """
    text = str(value or "").strip().upper()
    if not text:
        return default
    if text.startswith("HOLD+"):
        return default
    if "+" in text:
        head, _, tail = text.partition("+")
        if head in ("Q", "E", "ALT") and tail.isdigit():
            return normalize_key(head, default) if head == "E" else default
    if text in ("Q", "ALT"):
        return default
    return normalize_key(text, default)


def keys_from_settings(raw) -> dict:
    """settings["timeline.keys"] → 完整键位配置（缺失字段用默认值，旧组合键迁移）。"""
    data = dict(DEFAULT_KEYS)
    if isinstance(raw, dict):
        for key, default in DEFAULT_KEYS.items():
            if key in ("hold_number_ult", "hold_ms"):
                continue
            data[key] = _legacy_key(raw.get(key), str(default))
        if "hold_number_ult" in raw:
            data["hold_number_ult"] = bool(raw.get("hold_number_ult"))
        try:
            hold = int(raw.get("hold_ms", data["hold_ms"]))
        except (TypeError, ValueError):
            hold = int(DEFAULT_KEYS["hold_ms"])
        data["hold_ms"] = max(80, min(2000, hold))
    return data


def normalize_token(token: str) -> str:
    """统一输入 token：``q+1``/``Q1`` → ``Q+1``；``f8`` → ``F8``。"""
    text = str(token or "").strip().upper().replace(" ", "")
    if not text:
        return ""
    if re.fullmatch(r"F\d{1,2}", text):
        return text
    match = _TOKEN_RE.match(text)
    if match:
        return f"{match.group(1)}+{match.group(2)}"
    return _ALIASES.get(text, text)


def combo_token(modifier: str, slot: int) -> str:
    """旧式「修饰键 + 数字」token（仅兼容外部调用；当前运行态不再使用）。"""
    return f"{normalize_key(modifier, 'Q')}+{int(slot) + 1}"


def switch_key(keys: dict | None, slot: int) -> str:
    """切换干员的键位：默认 F1/F2/F3/F4（不是数字键），可在键位设置里改。"""
    index = max(0, min(SLOT_COUNT - 1, int(slot)))
    data = keys or {}
    return normalize_key(data.get(f"switch_{index + 1}"), f"F{index + 1}")


def key_token_for(kind: str, operator: int, keys: dict | None = None) -> str:
    """步骤 → 运行态接受的键位 token（真实键位模型）。"""
    slot = max(0, min(SLOT_COUNT - 1, int(operator)))
    data = keys or DEFAULT_KEYS
    if kind == "skill":
        return str(slot + 1)               # 数字短按 = 战技
    if kind == "link":
        return normalize_key(data.get("link"), "E")   # E = 连携（全队共用）
    if kind == "ult":
        return f"HOLD+{slot + 1}"          # 长按数字 = 终结技
    if kind == "switch":
        return switch_key(data, slot)      # F1~F4 = 切换干员
    return ""


def display_token(token: str) -> str:
    return normalize_token(token).replace("+", "")


# -- 手柄映射（实机按键设定）-----------------------------------------------------
# 1~4 号位面键顺序：Xbox X / Y / B / A，DS □ / △ / ○ / ×（core.input 归一化后
# 两种手柄都是 PAD_X / PAD_Y / PAD_B / PAD_A，物理位置一一对应）。
PAD_FACE_ORDER: tuple[str, ...] = ("PAD_X", "PAD_Y", "PAD_B", "PAD_A")
PAD_FACE_GLYPHS: dict[str, tuple[str, str]] = {
    "PAD_X": ("X", "□"),
    "PAD_Y": ("Y", "△"),
    "PAD_B": ("B", "○"),
    "PAD_A": ("A", "×"),
}
PAD_MODIFIER = "PAD_MOD"
PAD_LINK_SHOULDER = "PAD_RB"


def pad_tokens_for(kind: str, operator: int) -> tuple[str, ...]:
    """一个步骤接受的手柄 token。

    - 战技：按住左肩键（LB / L1）+ 该号位面键；
    - 连携：单按该号位面键，或右肩键（RB / R1）；
    - 终结技 / 切换干员：core.input 没有对应 token，暂不支持（用键盘长按数字 / F1-F4）。
    """
    slot = max(0, min(SLOT_COUNT - 1, int(operator)))
    face = PAD_FACE_ORDER[slot]
    if kind == "skill":
        return (f"{PAD_MODIFIER}+{face}",)
    if kind == "link":
        return (face, PAD_LINK_SHOULDER)
    return ()


def pad_key_text(kind: str, operator: int, mode: str = "xbox") -> str:
    """提示条 / 画布上的手柄键位文本；终结技没有手柄键位，返回空串。"""
    slot = max(0, min(SLOT_COUNT - 1, int(operator)))
    face = PAD_FACE_ORDER[slot]
    xbox, ds = PAD_FACE_GLYPHS[face]
    is_ds = str(mode or "").strip().lower() == "ds"
    if kind == "skill":
        return f"L1+{ds}" if is_ds else f"LB+{xbox}"
    if kind == "link":
        return "R1" if is_ds else "RB"
    return ""


@dataclass(frozen=True)
class RunStep:
    index: int
    uid: str
    kind: str
    operator: int
    operator_name: str
    class_name: str
    element: str
    start: float
    duration: float
    label: str
    token: str
    alt_tokens: tuple[str, ...]
    segment: int
    segment_name: str
    pad_tokens: tuple[str, ...] = ()
    skill: str = ""

    @property
    def key_text(self) -> str:
        if self.kind == "skill":
            return str(self.operator + 1)
        if self.kind == "ult":
            return tr("timeline.run.hold_key", digit=self.operator + 1)
        return display_token(self.token)

    @property
    def is_block(self) -> bool:
        return self.kind in BLOCK_KINDS

    @property
    def is_key(self) -> bool:
        return self.kind in KEY_KINDS

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def title(self) -> str:
        name = self.operator_name or tr("timeline.defaults.slot_two_digit", slot=self.operator + 1)
        if self.kind == "heavy":
            return tr("timeline.run.title_heavy", name=name)
        text = tr("timeline.run.title_operator", name=name, kind=self.kind_label)
        if self.skill:
            text += f" · {self.skill}"
        if self.label:
            text += f" · {self.label}"
        return text

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "operator": self.operator,
            "operator_name": self.operator_name,
            "class": self.class_name,
            "element": self.element,
            "start": round(self.start, 3),
            "duration": round(self.duration, 3),
            "skill": self.skill,
            "label": self.label,
            "token": self.token,
            "key_text": self.key_text,
            "segment": self.segment,
            "segment_name": self.segment_name,
            "title": self.title,
        }


@dataclass(frozen=True)
class RunLoop:
    uid: str
    start: int
    end: int
    count: int
    depth: int

    def contains(self, index: int) -> bool:
        return self.start <= index <= self.end


@dataclass
class RunSequence:
    steps: list[RunStep] = field(default_factory=list)
    loops: list[RunLoop] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.steps)

    def step(self, index: int) -> RunStep | None:
        if 0 <= index < len(self.steps):
            return self.steps[index]
        return None

    def segment_of(self, index: int) -> dict:
        step = self.step(index)
        if step is None:
            return {"index": 0, "name": tr("timeline.defaults.opening"), "steps": []}
        indexes = [i for i, item in enumerate(self.steps) if item.segment == step.segment]
        return {
            "index": step.segment,
            "name": step.segment_name,
            "steps": indexes,
            "position": indexes.index(index) + 1 if index in indexes else 0,
            "total": len(indexes),
        }

    @classmethod
    def from_model(cls, model, team_names: list[str], keys: dict, lookup=None) -> "RunSequence":
        """把有序步骤序列编译成运行序列。

        实机规则：注释 / 换行 / 分段 / 重击只做提示或排版，运行时不按键；
        反复开始/结束成对使用（可嵌套），运行时按次数循环。
        """
        keys = dict(DEFAULT_KEYS, **(keys or {}))
        team = list(team_names or [])[:SLOT_COUNT]
        while len(team) < SLOT_COUNT:
            team.append("")

        steps: list[RunStep] = []
        loop_opens: dict[str, tuple[int, int]] = {}
        loop_stack: list[tuple[str, int, int]] = []
        loops: list[RunLoop] = []
        segments: list[dict] = [{"index": 0, "name": tr("timeline.defaults.segment_name", index=1), "start": 0, "end": 0}]
        segment_index = 0
        segment_name = tr("timeline.defaults.segment_name", index=1)

        for order, step in enumerate(model.steps):
            if step.kind == "segment":
                segment_index += 1
                segment_name = step.label or tr("timeline.defaults.segment_name", index=segment_index + 1)
                segments.append({"index": segment_index, "name": segment_name, "start": len(steps), "end": len(steps)})
                continue
            if step.kind == "loop_start":
                group = step.group or f"L{order}"
                step.group = group
                loop_opens[group] = (len(steps), step.loop_count)
                loop_stack.append((group, len(steps), step.loop_count))
                continue
            if step.kind == "loop_end":
                start_index = None
                count = 2
                if step.group and step.group in loop_opens:
                    start_index, count = loop_opens.pop(step.group)
                    loop_stack = [(g, i, c) for g, i, c in loop_stack if g != step.group]
                elif loop_stack:
                    group, start_index, count = loop_stack.pop()
                    step.group = group
                if start_index is not None and start_index < len(steps):
                    loops.append(RunLoop(uid=step.group or f"L{order}", start=start_index,
                                         end=len(steps) - 1, count=count, depth=0))
                continue
            if step.kind not in KEY_KINDS:
                continue  # 注释 / 换行 / 分段 / 重击：运行时不要求按键
            operator = int(step.operator)
            name = team[operator] if 0 <= operator < len(team) else ""
            operator_data = lookup(name) if lookup and name else None
            class_name = getattr(operator_data, "class_name", "") or ""
            element = getattr(operator_data, "element", "") or ""
            alt: tuple[str, ...] = ()
            token = key_token_for(step.kind, operator, keys)
            steps.append(
                RunStep(
                    index=len(steps),
                    uid=step.uid,
                    kind=step.kind,
                    operator=operator,
                    operator_name=name,
                    class_name=class_name,
                    element=element,
                    start=float(len(steps)),
                    duration=float(step.duration or 0.0),
                    skill=str(step.skill or ""),
                    label=step.label,
                    token=token,
                    alt_tokens=alt,
                    segment=segment_index,
                    segment_name=segment_name,
                    pad_tokens=pad_tokens_for(step.kind, operator),
                )
            )
        sized = []
        for pair in loops:
            depth = 0
            for other in loops:
                if other is pair:
                    continue
                if other.start <= pair.start and other.end >= pair.end and (
                    other.start < pair.start or other.end > pair.end
                ):
                    depth += 1
            sized.append(RunLoop(uid=pair.uid, start=pair.start, end=pair.end,
                                 count=pair.count, depth=min(depth, 3)))
        loops = sized
        loops.sort(key=lambda loop: (loop.start, -loop.end, -loop.depth))
        for segment in segments[1:]:
            segment["end"] = len(steps) - 1
        return cls(steps=steps, loops=loops, segments=segments)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "steps": [step.to_dict() for step in self.steps],
            "loops": [
                {"start": loop.start, "end": loop.end, "count": loop.count, "depth": loop.depth}
                for loop in self.loops
            ],
            "segments": list(self.segments),
        }


class RunEngine:
    """按键推进状态机：只在 token 匹配当前步骤时前进；反复按规则循环。

    默认整轴反复（``repeat_all=True``）：跑完最后一步回到第一步继续，永不结束；
    用户按 F8 才退出运行。关掉整轴反复时才在结尾 ``finished=True``。
    """

    def __init__(
        self,
        sequence: RunSequence,
        loops_enabled: bool = True,
        repeat_all: bool = True,
    ):
        self.sequence = sequence
        self.loops_enabled = bool(loops_enabled)
        self.repeat_all = bool(repeat_all)
        self.index = 0
        self.finished = False
        self.passes = 0                 # 整轴跑完的圈数（从 0 开始）
        self._played: dict[str, int] = {}

    @property
    def total(self) -> int:
        return self.sequence.total

    @property
    def current(self) -> RunStep | None:
        return self.sequence.step(self.index)

    @property
    def segment(self) -> dict:
        return self.sequence.segment_of(self.index)

    def peek(self) -> RunStep | None:
        if self.finished:
            return None
        return self.sequence.step(self.index + 1)

    # -- 控制 ---------------------------------------------------------------
    def reset(self) -> None:
        self.index = 0
        self.finished = False
        self.passes = 0
        self._played.clear()

    def set_repeat_all(self, enabled: bool) -> bool:
        self.repeat_all = bool(enabled)
        return self.repeat_all

    def toggle_loops(self) -> bool:
        self.loops_enabled = not self.loops_enabled
        return self.loops_enabled

    def jump_segment(self, delta: int) -> bool:
        """上一段 / 下一段：跳到相邻分段的第一个按键步骤。"""
        if self.finished or not self.sequence.steps:
            return False
        current = self.step_segment_span(self.index)
        target = current + int(delta)
        spans = self.segment_spans()
        if target < 0 or target >= len(spans):
            return False
        self.index = spans[target]
        return True

    def step_segment_span(self, index: int) -> int:
        step = self.sequence.step(index)
        return int(step.segment) if step is not None else 0

    def segment_spans(self) -> list[int]:
        spans: list[int] = []
        last = -1
        for i, step in enumerate(self.sequence.steps):
            if step.segment != last:
                spans.append(i)
                last = step.segment
        return spans

    def jump_loop_start(self) -> bool:
        """F11：回最内层反复起点；不在反复里就回本段开头。"""
        if self.finished or not self.sequence.loops:
            return False
        inside = [loop for loop in self.sequence.loops if loop.contains(self.index)]
        if inside:
            target = max(inside, key=lambda loop: (loop.depth, loop.start, loop.end))
            self.index = target.start
            return True
        segment = self.segment
        indexes = segment.get("steps") or []
        if indexes:
            self.index = indexes[0]
            return True
        return False

    # -- 推进 ---------------------------------------------------------------
    def advance(self) -> bool:
        """当前步骤按对后调用；返回 False 表示已到结尾（仅关闭整轴反复时）。"""
        if self.finished or not self.sequence.steps:
            self.finished = True
            return False
        jump = self._loop_jump_at(self.index)
        if jump is not None:
            self.index = jump.start
            return True
        self.index += 1
        if self.index >= self.total:
            if self.repeat_all and self.total:
                # 整轴反复：回到开头继续跑，圈数 +1，内层反复计数清零
                self.index = 0
                self.passes += 1
                self._played.clear()
                return True
            self.index = self.total - 1
            self.finished = True
            return False
        return True

    def matches(self, token: str) -> bool:
        step = self.current
        if step is None:
            return False
        wanted = normalize_token(token)
        accepted = {normalize_token(step.token), *(normalize_token(t) for t in step.alt_tokens)}
        accepted.update(step.pad_tokens)
        return wanted in accepted

    def _loop_jump_at(self, index: int) -> RunLoop | None:
        if not self.loops_enabled:
            return None
        endings = [loop for loop in self.sequence.loops if loop.end == index]
        # 内层先结算（depth 大的、范围小的优先）
        endings.sort(key=lambda loop: (-loop.depth, loop.end - loop.start))
        for loop in endings:
            played = self._played.get(loop.uid, 0)
            if loop.count < 0 or played < max(1, loop.count) - 1:
                self._played[loop.uid] = played + 1
                # 外圈重新开始：清掉它内部所有内层循环的计数
                for inner in self.sequence.loops:
                    if inner is not loop and inner.start >= loop.start and inner.end <= loop.end:
                        self._played.pop(inner.uid, None)
                return loop
        return None

    def loop_status(self) -> dict | None:
        """当前所在的（最内层）反复区间与圈数，供提示条显示；整轴反复显示圈数。"""
        if self.loops_enabled and self.sequence.loops:
            inside = [loop for loop in self.sequence.loops if loop.contains(self.index)]
            if inside:
                loop = max(inside, key=lambda item: (item.depth, item.start))
                played = self._played.get(loop.uid, 0)
                total = loop.count
                return {
                    "uid": loop.uid,
                    "played": played,
                    "total": total,
                    "current": played + 1 if total >= 0 else 0,
                    "depth": loop.depth,
                    "text": (
                        tr("timeline.overlay.loop_count", current=played + 1, total=total)
                        if total >= 0
                        else tr("timeline.overlay.loop_infinite", current=played + 1)
                    ),
                }
        if self.repeat_all:
            return {
                "uid": "__all__",
                "played": self.passes,
                "total": -1,
                "current": 0,
                "depth": 0,
                "text": tr("timeline.overlay.loop_infinite", current=self.passes + 1),
            }
        return None
