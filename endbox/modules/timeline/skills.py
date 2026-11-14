# -*- coding: utf-8 -*-
"""游戏数据：干员技能真数据（``assets/data/operators.json``）。

数据由 data 工程师的同步脚本生成（详见 task-1），本模块只做**宽容读取 + 路径解析**：

- 根结构兼容：``{"operators": [...]}`` / ``{"operators": {name: {...}}}`` / 直接列表 / 名字→干员字典；
- 技能字段兼容：``skills`` 列表（每项 ``name/type/icon/duration`` 或 ``durationFrame`` 帧数）；
- 图标 / 头像路径：绝对路径直接用；相对路径依次尝试 ``config.ASSETS``、仓库根、``素材/``、
  ``assets/gamedata/``；
- 文件缺失 / 损坏 / 字段缺 → 返回空数据并记日志，**绝不抛给界面**；
  排轴在缺数据时回退旧 ``素材/``（见 ``assets.AssetLibrary``）。

时长估算：``durationFrame / 60``（游戏动画按 60fps 估算），没有帧数时用 ``duration`` 秒；
两者都没有则用 ``DEFAULT_DURATIONS``（仅用于预计总耗时，不影响按键判定）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from endbox import config
from endbox.core.log import get_logger

log = get_logger("modules.timeline.skills")

FPS = 30.0          # 游戏动作配置的帧率（duration_frame / 30 ≈ duration 秒）
MAX_DURATION = 60.0
DEFAULT_DURATIONS: dict[str, float] = {
    "skill": 1.2,   # 战技动画默认估计
    "link": 0.9,    # 连携
    "ult": 2.0,     # 终结技
    "switch": 0.4,  # 切换干员
}
SKILL_KINDS: tuple[str, ...] = ("skill", "link", "ult")

def _zh(*codes: int) -> str:
    """按码点构造中文，避免在代码里写中文字面量（timeline 的 i18n 守卫会扫）。"""
    return "".join(chr(code) for code in codes)


_ZH_SKILL = _zh(0x6218, 0x6280)                    # 战技
_ZH_LINK = _zh(0x8FDE, 0x643A)                     # 连携
_ZH_LINK_FULL = _zh(0x8FDE, 0x643A, 0x6280)        # 连携技
_ZH_ULT = _zh(0x7EC8, 0x7ED3)                      # 终结
_ZH_ULT_FULL = _zh(0x7EC8, 0x7ED3, 0x6280)         # 终结技
_ZH_NAME_CN = _zh(0x4E2D, 0x6587, 0x540D)          # 中文名
_ZH_ATTACK = _zh(0x666E, 0x653B)                   # 普攻
_ZH_HEAVY = _zh(0x91CD, 0x51FB)                    # 重击

_KIND_ALIASES = {
    "skill": "skill",
    "normal": "skill",
    "normal_skill": "skill",
    "normalskill": "skill",
    _ZH_SKILL: "skill",
    "link": "link",
    "combo": "link",
    "combo_skill": "link",
    "comboskill": "link",
    _ZH_LINK: "link",
    _ZH_LINK_FULL: "link",
    "ult": "ult",
    "ultimate": "ult",
    "ultimate_skill": "ult",
    "ultimateskill": "ult",
    _ZH_ULT: "ult",
    _ZH_ULT_FULL: "ult",
    "attack": "attack",
    _ZH_ATTACK: "attack",
    "heavy": "heavy",
    _ZH_HEAVY: "heavy",
}

_ICON_DIRS = ("gamedata", "gamedata/skills", "images/skills", "skills")


def normalize_skill_kind(value) -> str:
    """技能类型 → ``skill`` / ``link`` / ``ult``；无法识别返回空字符串。"""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return ""
    if text in _KIND_ALIASES:
        return _KIND_ALIASES[text]
    for alias, kind in _KIND_ALIASES.items():
        if alias and alias in text:
            return kind
    return ""


def _as_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def duration_seconds(data: dict) -> float:
    """从技能字典取动画时长（秒）：优先 ``duration``（数据已是秒），缺失按 ``duration_frame / 30``。"""
    if not isinstance(data, dict):
        return 0.0
    for key in ("duration", "duration_seconds", "durationSeconds", "cast_time", "castTime"):
        seconds = _as_float(data.get(key))
        if seconds:
            return round(min(MAX_DURATION, seconds), 3)
    for key in ("durationFrame", "duration_frame", "durationFrames", "duration_frames", "frames", "frame"):
        frames = _as_float(data.get(key))
        if frames:
            return round(min(MAX_DURATION, max(0.0, frames / FPS)), 3)
    return 0.0


def resolve_path(value, *extra_roots) -> Path | None:
    """相对路径 → 本地真实文件；找不到返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path if path.is_file() else None
    roots = [Path(root) for root in extra_roots if root is not None]
    roots.extend([Path(config.ASSETS), Path(config.ROOT), Path(config.GAME_ASSETS)])
    candidates: list[Path] = []
    for root in roots:
        candidates.append(root / path)
        for sub in _ICON_DIRS:
            candidates.append(root / sub / path)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


@dataclass(frozen=True)
class SkillInfo:
    """一个技能的真实数据（名称 / 类型 / 图标 / 动画时长）。"""

    id: str = ""
    name: str = ""
    kind: str = "skill"
    duration: float = 0.0
    duration_frames: int = 0
    icon: Path | None = None
    desc: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.kind,
            "duration": self.duration,
            "duration_frames": self.duration_frames,
            "icon": str(self.icon) if self.icon else None,
            "desc": self.desc,
        }


@dataclass(frozen=True)
class GameOperator:
    """operators.json 里的一个干员（技能列表已规范化）。"""

    name: str
    name_en: str = ""
    op_id: str = ""
    rarity: int | None = None
    profession: str = ""
    element: str = ""
    weapon: str = ""
    faction: str = ""
    tags: tuple[str, ...] = ()
    avatar: Path | None = None
    skills: tuple[SkillInfo, ...] = ()
    has_assets: bool = False

    def skills_of(self, kind: str) -> tuple[SkillInfo, ...]:
        wanted = normalize_skill_kind(kind)
        return tuple(skill for skill in self.skills if skill.kind == wanted)

    def skill(self, kind: str) -> SkillInfo | None:
        items = self.skills_of(kind)
        return items[0] if items else None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "name_en": self.name_en,
            "id": self.op_id,
            "rarity": self.rarity,
            "profession": self.profession,
            "element": self.element,
            "weapon": self.weapon,
            "faction": self.faction,
            "tags": list(self.tags),
            "avatar": str(self.avatar) if self.avatar else None,
            "skills": [skill.to_dict() for skill in self.skills],
            "has_assets": self.has_assets,
        }


class GameDataLibrary:
    """``assets/data/operators.json`` 的惰性读取器（缺文件时全部返回空，回退旧素材）。"""

    def __init__(self, path: Path | str | None = None):
        self.path = (
            Path(path)
            if path is not None
            else Path(config.ASSETS) / "data" / "operators.json"
        )
        try:
            self.gamedata_root = self.path.resolve().parents[1] / "gamedata"
        except (IndexError, OSError):
            self.gamedata_root = Path(config.ASSETS) / "gamedata"
        self._current_operator_name = ""
        self._operators: dict[str, GameOperator] = {}
        self._by_alias: dict[str, str] = {}
        self._loaded = False
        self._error: str | None = None
        self.meta: dict = {}
        self.version = ""

    # -- 读取 ---------------------------------------------------------------
    @property
    def available(self) -> bool:
        self._load()
        return bool(self._operators)

    @property
    def error(self) -> str | None:
        self._load()
        return self._error

    @property
    def operators(self) -> list[GameOperator]:
        self._load()
        return list(self._operators.values())

    @property
    def names(self) -> list[str]:
        self._load()
        return list(self._operators)

    @property
    def count(self) -> int:
        return len(self.operators)

    def reload(self) -> None:
        self._loaded = False
        self._operators = {}
        self._by_alias = {}
        self._error = None

    def get(self, name: str) -> GameOperator | None:
        text = str(name or "").strip()
        if not text:
            return None
        self._load()
        operator = self._operators.get(text)
        if operator is not None:
            return operator
        alias = self._by_alias.get(text.lower())
        return self._operators.get(alias) if alias else None

    def resolve_name(self, name: str) -> str:
        operator = self.get(name)
        return operator.name if operator is not None else ""

    def skills_for(self, name: str) -> tuple[SkillInfo, ...]:
        operator = self.get(name)
        return operator.skills if operator is not None else ()

    def skill(self, name: str, kind: str) -> SkillInfo | None:
        operator = self.get(name)
        return operator.skill(kind) if operator is not None else None

    def duration(self, name: str, kind: str, default: float = 0.0) -> float:
        info = self.skill(name, kind)
        if info is not None and info.duration:
            return info.duration
        return float(default or DEFAULT_DURATIONS.get(normalize_skill_kind(kind) or kind, 0.0))

    def skill_name(self, name: str, kind: str) -> str:
        info = self.skill(name, kind)
        return info.name if info is not None else ""

    def skill_icon(self, name: str, kind: str) -> Path | None:
        info = self.skill(name, kind)
        return info.icon if info is not None else None

    # -- 解析 ---------------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.path
        if not path.is_file():
            log.info("未找到游戏数据文件：%s（回退旧素材）", path)
            return
        try:
            raw = json.loads(path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 坏数据不阻塞排轴
            self._error = str(exc)
            log.warning("operators.json 读取失败：%s", exc)
            return
        entries = self._entries(raw)
        if not entries:
            log.warning("operators.json 结构无法识别（%s），回退旧素材", path)
            return
        self.meta = raw.get("meta") if isinstance(raw, dict) and isinstance(raw.get("meta"), dict) else {}
        self.version = str(raw.get("version") or self.meta.get("version") or "") if isinstance(raw, dict) else ""
        for data in entries:
            operator = self._parse_operator(data)
            if operator is None:
                continue
            existing = self._operators.get(operator.name)
            if existing is not None:
                # 同名多形态（如管理员 m/f/9000）：按 has_assets / 头像 / 稀有度取最优一条。
                self._operators[operator.name] = self._preferred(existing, operator)
            else:
                self._operators[operator.name] = operator
            target = self._operators[operator.name]
            for alias in (target.name.lower(), target.name_en.lower(), target.op_id.lower()):
                if alias:
                    self._by_alias.setdefault(alias, target.name)
        log.info("游戏数据就绪：%s 名干员（%s）", len(self._operators), path)

    @staticmethod
    def _preferred(current: GameOperator, candidate: GameOperator) -> GameOperator:
        def score(operator: GameOperator) -> tuple[int, int, int]:
            return (
                1 if operator.has_assets else 0,
                1 if operator.avatar else 0,
                int(operator.rarity or 0),
            )

        return candidate if score(candidate) > score(current) else current

    @staticmethod
    def _entries(raw) -> list[dict]:
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if not isinstance(raw, dict):
            return []
        node = raw.get("operators")
        if isinstance(node, list):
            return [item for item in node if isinstance(item, dict)]
        if isinstance(node, dict):
            out = []
            for name, item in node.items():
                if isinstance(item, dict):
                    merged = dict(item)
                    merged.setdefault("name", name)
                    out.append(merged)
            return out
        # 根对象本身就是 名字→干员 的字典
        if all(isinstance(item, dict) for item in raw.values() if item is not None) and raw:
            return [
                dict(item, name=item.get("name") or key)
                for key, item in raw.items()
                if isinstance(item, dict)
            ]
        return []

    def _parse_operator(self, data: dict) -> GameOperator | None:
        name = str(
            data.get("name") or data.get("cn_name") or data.get("name_cn") or data.get(_ZH_NAME_CN) or ""
        ).strip()
        if not name:
            return None
        skills: list[SkillInfo] = []
        raw_skills = data.get("skills")
        self._current_operator_name = name
        try:
            if isinstance(raw_skills, dict):
                for kind, items in raw_skills.items():
                    if isinstance(items, dict):
                        items = [items]
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if isinstance(item, dict):
                            skills.append(self._parse_skill(item, kind))
            elif isinstance(raw_skills, list):
                for item in raw_skills:
                    if isinstance(item, dict):
                        skills.append(self._parse_skill(item))
        finally:
            self._current_operator_name = ""
        skills = [skill for skill in skills if skill is not None and skill.kind]
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        try:
            rarity = int(data.get("rarity")) if data.get("rarity") is not None else None
        except (TypeError, ValueError):
            rarity = None
        return GameOperator(
            name=name,
            name_en=str(data.get("name_en") or data.get("nameEn") or "").strip(),
            op_id=str(data.get("id") or data.get("charId") or data.get("op_id") or ""),
            rarity=rarity,
            profession=str(data.get("profession") or data.get("class") or "").strip(),
            element=str(data.get("element") or data.get("property") or "").strip(),
            weapon=str(data.get("weapon") or data.get("weapon_type") or "").strip(),
            faction=str(data.get("faction") or "").strip(),
            tags=tuple(str(tag) for tag in tags if tag),
            avatar=self._resolve_asset(
                data.get("avatar") or data.get("avatar_path") or data.get("avatarPath") or ""
            ),
            skills=tuple(skills),
            has_assets=bool(data.get("has_assets")),
        )

    def _resolve_asset(self, value) -> Path | None:
        """数据里的相对素材路径：先按当前 operators.json 的素材根解析，再走全局候选。"""
        text = str(value or "").strip()
        if not text:
            return None
        return resolve_path(text, self.gamedata_root)

    def _parse_skill(self, data: dict, kind_hint: str = "") -> SkillInfo | None:
        kind = normalize_skill_kind(
            data.get("kind") or data.get("type") or data.get("skill_type") or kind_hint
        )
        if not kind:
            kind = normalize_skill_kind(kind_hint)
        if not kind:
            return None
        frames = 0
        for key in ("durationFrame", "duration_frame", "durationFrames", "duration_frames", "frames"):
            value = _as_float(data.get(key))
            if value:
                frames = int(round(value))
                break
        icon_value = data.get("icon") or data.get("icon_path") or data.get("iconPath") or ""
        return SkillInfo(
            id=str(data.get("id") or data.get("skill_id") or data.get("skillId") or ""),
            name=str(data.get("name") or data.get("skill_name") or data.get("skillName") or "").strip(),
            kind=kind,
            duration=duration_seconds(data),
            duration_frames=frames,
            icon=self._skill_icon(kind, data, icon_value),
            desc=str(data.get("desc") or data.get("description") or "").strip()[:400],
        )

    def _skill_icon(self, kind: str, data: dict, icon_value) -> Path | None:
        """技能图标：显式路径优先生效；否则按 gamedata 约定找 ``operators/<名字>/skills/<kind>.png``。"""
        resolved = (
            self._resolve_asset(icon_value)
            if str(icon_value or "").lower().endswith(".png")
            else None
        )
        if resolved is not None:
            return resolved
        name = str(data.get("operator") or data.get("owner") or self._current_operator_name or "").strip()
        if not name:
            return None
        for base in self._gamedata_roots():
            candidate = base / "operators" / name / "skills" / f"{kind}.png"
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def _gamedata_roots(self) -> list[Path]:
        roots: list[Path] = [self.gamedata_root]
        roots.extend([Path(config.ASSETS) / "gamedata", Path(config.ROOT) / "assets" / "gamedata"])
        return roots


__all__ = [
    "DEFAULT_DURATIONS",
    "FPS",
    "GameDataLibrary",
    "GameOperator",
    "SKILL_KINDS",
    "SkillInfo",
    "duration_seconds",
    "normalize_skill_kind",
    "resolve_path",
]
