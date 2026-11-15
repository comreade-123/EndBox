# -*- coding: utf-8 -*-
"""干员素材解析（只读 ``endbox.config.GAME_ASSETS``，不复制、不修改素材）。

素材目录结构（由用户提供的游戏素材）：

```
素材/
  operators/
    roster.json                 {"2": "祀", "3": "噗切娜", ...}  id -> 名字
    <名字>/
      avatar.png                立绘/头像
      link.png  skill.png  ult.png
      class.txt                 职业（先锋/术士/突击/辅助/近卫/重装）
      element.txt               属性（寒冷/灼热/物理/电磁/自然）
      color.txt / colors.json   可选专色（旧 Tkinter 版使用）
  classes/<职业>.png            职业图标
  elements/<属性>.png           属性图标
```

可选的真数据：``assets/data/operators.json``（data 同步脚本生成，见 ``skills.py``）。
有数据时合并进干员池（素材目录缺的干员也能选），技能名/图标/动画时长来自数据；
数据缺失或损坏时**优雅回退**为只读旧 ``素材/``（界面只显示技能类型，不报错）。

这里只解析路径与文本，不加载 QPixmap；图片缓存由视图负责（避免在 paintEvent 里读盘）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from endbox import config
from endbox.core.log import get_logger
from endbox.core.text import t as tr
from endbox.modules.timeline.skills import (
    GameDataLibrary,
    GameOperator,
    SkillInfo,
    _zh,
    normalize_skill_kind,
)

log = get_logger("modules.timeline.assets")

TEXT_FALLBACK = "—"
# 游戏数据里职业叫「术师」，现有素材目录叫「术士」：两套都认（按码点构造，避免中文硬编码）。
_ZH_SCHOLAR = _zh(0x672F, 0x58EB)   # 术士
_ZH_ALCHEMIST = _zh(0x672F, 0x5E08)  # 术师


def _read_text(path: Path) -> str:
    try:
        return path.read_text("utf-8-sig").strip()
    except (OSError, UnicodeError):
        return ""


def _resolve(directory: Path, name: str) -> Path | None:
    if not name:
        return None
    candidate = directory / name
    return candidate if candidate.is_file() else None


@dataclass(frozen=True)
class Operator:
    """一个干员的素材路径与职业/属性文本（全部来自本地素材）。"""

    name: str
    op_id: int | None
    class_name: str
    element: str
    avatar: Path | None = None
    link: Path | None = None
    skill: Path | None = None
    ult: Path | None = None
    class_icon: Path | None = None
    element_icon: Path | None = None
    color: str | None = None

    @property
    def class_label(self) -> str:
        return self.class_name or TEXT_FALLBACK

    @property
    def element_label(self) -> str:
        return self.element or TEXT_FALLBACK

    def asset(self, kind: str) -> Path | None:
        return {"skill": self.skill, "link": self.link, "ult": self.ult}.get(kind)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "id": self.op_id,
            "class": self.class_name,
            "element": self.element,
            "avatar": str(self.avatar) if self.avatar else None,
        }


@dataclass(frozen=True)
class OperatorExtra:
    """离线补充资料（fz.wiki 快照）；缺失字段为 None，界面显示 —。"""

    name: str
    name_en: str | None = None
    rarity: int | None = None
    profession: str | None = None
    sub_profession: str | None = None
    faction: str | None = None
    weapon_type: str | None = None
    tags: tuple[str, ...] = ()
    bio: str | None = None
    meta: dict[str, str] = field(default_factory=dict)
    skills: tuple[dict, ...] = ()
    source: str | None = None

    def meta_value(self, *labels: str) -> str | None:
        for label in labels:
            value = self.meta.get(label)
            if value:
                return value
        return None

    def to_dict(self) -> dict:
        return {
            "name_en": self.name_en,
            "rarity": self.rarity,
            "profession": self.profession,
            "sub_profession": self.sub_profession,
            "faction": self.faction,
            "weapon_type": self.weapon_type,
            "tags": list(self.tags),
            "bio": self.bio,
            "meta": dict(self.meta),
            "skills": [dict(item) for item in self.skills],
            "source": self.source,
        }


class AssetLibrary:
    """干员清单 + 职业/属性图标的惰性缓存；可选加载离线补充资料与真数据。"""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        extra_path: Path | str | None = None,
        game_data_path: Path | str | None = "auto",
    ):
        self.root = Path(root) if root is not None else Path(config.GAME_ASSETS)
        self.extra_path = (
            Path(extra_path)
            if extra_path is not None
            else Path(config.ASSETS) / "data" / "operators_extra.json"
        )
        # auto：使用默认素材根（root=None）时自动合并 assets/data/operators.json；
        # 显式传 root（测试 / 自定义素材）时不合并，保持原有清单。
        if game_data_path == "auto":
            game_data_path = None if root is not None else Path(config.ASSETS) / "data" / "operators.json"
        self.game_data = GameDataLibrary(game_data_path) if game_data_path is not None else None
        self._operators: list[Operator] | None = None
        self._by_name: dict[str, Operator] = {}
        self._class_icons: dict[str, Path | None] = {}
        self._element_icons: dict[str, Path | None] = {}
        self._extras: dict[str, OperatorExtra] | None = None
        self._extra_meta: dict = {}
        self._extra_error: str | None = None

    # -- 目录 ---------------------------------------------------------------
    @property
    def operators_dir(self) -> Path:
        return self.root / "operators"

    @property
    def available(self) -> bool:
        """素材目录或真数据是否存在。"""
        if self.operators_dir.is_dir():
            return True
        return bool(self.game_data is not None and self.game_data.available)

    # -- 真数据（技能名 / 图标 / 动画时长）----------------------------------
    @property
    def game_data_available(self) -> bool:
        return bool(self.game_data is not None and self.game_data.available)

    @property
    def game_data_version(self) -> str:
        return self.game_data.version if self.game_data is not None else ""

    def skills_for(self, name: str) -> tuple[SkillInfo, ...]:
        return self.game_data.skills_for(name) if self.game_data is not None else ()

    def skill(self, name: str, kind: str) -> SkillInfo | None:
        return self.game_data.skill(name, kind) if self.game_data is not None else None

    def skill_icon(self, name: str, kind: str) -> Path | None:
        generated = self.gen_icon(name, kind)
        if generated is not None:
            return generated
        info = self.skill(name, kind)
        if info is not None and info.icon is not None:
            return info.icon
        operator = self.get(name) if name else None
        normalized = normalize_skill_kind(kind)
        return operator.asset(normalized) if operator is not None and normalized else None

    def gen_icon(self, name: str, kind: str) -> Path | None:
        """``素材/gen`` 里按干员生成的卡片图标（优先于旧素材/数据站图标）。

        skill_/link_/ult_ + 干员名 + ``_x.png``；重击固定 ``heavy_x_x_nb.png``。
        """
        gen_dir = self.root / "gen"
        if not gen_dir.is_dir():
            return None
        normalized = normalize_skill_kind(kind) or str(kind or "").strip().lower()
        if normalized in ("skill", "link", "ult") and name:
            candidate = gen_dir / f"{normalized}_{name}_x.png"
            return candidate if candidate.is_file() else None
        if str(kind or "").strip().lower() == "heavy":
            candidate = gen_dir / "heavy_x_x_nb.png"
            return candidate if candidate.is_file() else None
        return None

    def duration(self, name: str, kind: str, default: float = 0.0) -> float:
        if self.game_data is not None:
            return self.game_data.duration(name, kind, default)
        from endbox.modules.timeline.skills import DEFAULT_DURATIONS

        normalized = normalize_skill_kind(kind)
        return float(default or DEFAULT_DURATIONS.get(normalized, 0.0))

    def reload(self) -> None:
        self._operators = None
        self._by_name = {}
        self._class_icons = {}
        self._element_icons = {}
        self._extras = None
        self._extra_meta = {}
        self._extra_error = None
        if self.game_data is not None:
            self.game_data.reload()

    # -- 离线补充资料 -------------------------------------------------------
    @property
    def extra_available(self) -> bool:
        return bool(self._load_extras())

    @property
    def extra_meta(self) -> dict:
        self._load_extras()
        return dict(self._extra_meta)

    @property
    def extra_error(self) -> str | None:
        self._load_extras()
        return self._extra_error

    def extra_for(self, name: str) -> OperatorExtra | None:
        if not name:
            return None
        return self._load_extras().get(str(name))

    def _load_extras(self) -> dict[str, OperatorExtra]:
        if self._extras is not None:
            return self._extras
        self._extras = {}
        self._extra_meta = {}
        self._extra_error = None
        path = self.extra_path
        if not path.is_file():
            return self._extras
        try:
            raw = json.loads(path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 补充资料坏了不影响素材本身
            self._extra_error = tr("timeline.errors.assets_extra", error=exc)
            log.warning("operators_extra.json 读取失败：%s", exc)
            return self._extras
        if not isinstance(raw, dict):
            return self._extras
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        self._extra_meta = dict(meta)
        source = raw.get("source") or meta.get("source")
        operators = raw.get("operators") if isinstance(raw.get("operators"), dict) else {}
        for name, data in operators.items():
            if not isinstance(data, dict):
                continue
            tags = data.get("tags") if isinstance(data.get("tags"), list) else []
            skills = data.get("skills") if isinstance(data.get("skills"), list) else []
            raw_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            try:
                rarity = int(data.get("rarity")) if data.get("rarity") is not None else None
            except (TypeError, ValueError):
                rarity = None
            self._extras[str(name)] = OperatorExtra(
                name=str(name),
                name_en=(str(data.get("name_en")) if data.get("name_en") else None),
                rarity=rarity,
                profession=(str(data.get("profession")) if data.get("profession") else None),
                sub_profession=(
                    str(data.get("sub_profession")) if data.get("sub_profession") else None
                ),
                faction=(str(data.get("faction")) if data.get("faction") else None),
                weapon_type=(str(data.get("weapon_type")) if data.get("weapon_type") else None),
                tags=tuple(str(t) for t in tags if t),
                bio=(str(data.get("bio")) if data.get("bio") else None),
                meta={str(k): str(v) for k, v in raw_meta.items() if v},
                skills=tuple(item for item in skills if isinstance(item, dict)),
                source=(str(data.get("source") or source or "") or None),
            )
        log.info("离线干员资料就绪：%s 名（%s）", len(self._extras), path)
        return self._extras

    # -- 解析 ---------------------------------------------------------------
    @property
    def operators(self) -> list[Operator]:
        if self._operators is None:
            self._parse()
        return self._operators or []

    @property
    def names(self) -> list[str]:
        return [op.name for op in self.operators]

    def get(self, name: str) -> Operator | None:
        if not name:
            return None
        if self._operators is None:
            self._parse()
        return self._by_name.get(str(name))

    @property
    def count(self) -> int:
        return len(self.operators)

    def class_icon(self, class_name: str) -> Path | None:
        if class_name not in self._class_icons:
            self._class_icons[class_name] = self._find_class_icon(class_name)
        return self._class_icons[class_name]

    def element_icon(self, element: str) -> Path | None:
        if element not in self._element_icons:
            self._element_icons[element] = self._find_element_icon(element)
        return self._element_icons[element]

    @property
    def gamedata_root(self) -> Path:
        """data 同步脚本落盘的游戏素材缓存（默认 ``assets/gamedata``，gitignored 本地缓存）。"""
        if self.game_data is not None:
            return Path(self.game_data.gamedata_root)
        return Path(config.ASSETS) / "gamedata"

    def _find_class_icon(self, class_name: str) -> Path | None:
        # 游戏数据里职业叫「术师」，现有素材目录叫「术士」：两套都认。
        names = [class_name] if class_name else []
        if class_name == _ZH_SCHOLAR:
            names = [_ZH_SCHOLAR, _ZH_ALCHEMIST]
        elif class_name == _ZH_ALCHEMIST:
            names = [_ZH_ALCHEMIST, _ZH_SCHOLAR]
        for name in names:
            for root in (self.root, self.gamedata_root):
                path = _resolve(root / "classes", f"{name}.png")
                if path is not None:
                    return path
        return None

    def _find_element_icon(self, element: str) -> Path | None:
        for root in (self.root, self.gamedata_root):
            path = _resolve(root / "elements", f"{element}.png")
            if path is not None:
                return path
        return None

    def _data_skill_icon(self, entry: GameOperator, kind: str) -> Path | None:
        info = entry.skill(kind)
        if info is not None and info.icon is not None:
            return info.icon
        for base in (self.gamedata_root, Path(config.ASSETS) / "gamedata"):
            candidate = base / "operators" / entry.name / "skills" / f"{kind}.png"
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def _gamedata_avatar(self, name: str) -> Path | None:
        for base in (self.gamedata_root, Path(self.root)):
            candidate = base / "operators" / name / "avatar.png"
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def _roster(self) -> dict[int, str]:
        path = self.operators_dir / "roster.json"
        roster: dict[int, str] = {}
        if not path.is_file():
            return roster
        try:
            raw = json.loads(path.read_text("utf-8-sig"))
        except Exception:  # noqa: BLE001 - 坏 roster 时退回目录扫描
            log.warning("roster.json 解析失败：%s", path)
            return roster
        if not isinstance(raw, dict):
            return roster
        for key, value in raw.items():
            try:
                op_id = int(key)
            except (TypeError, ValueError):
                continue
            name = str(value or "").strip()
            if name:
                roster[op_id] = name
        return roster

    def _parse(self) -> None:
        self._operators = []
        self._by_name = {}
        base = self.operators_dir
        directories: dict[str, Path] = {}
        if base.is_dir():
            directories = {p.name: p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")}
        else:
            log.info("未找到素材目录：%s（尝试游戏数据）", base)
        roster = self._roster()
        ordered: list[Operator] = []
        seen: set[str] = set()

        def add(name: str, op_id: int | None) -> None:
            directory = directories.get(name)
            if directory is None or name in seen:
                return
            operator = self._operator(name, op_id, directory)
            ordered.append(operator)
            seen.add(name)

        for op_id, name in sorted(roster.items()):
            add(name, op_id)
        for name in sorted(directories):
            add(name, None)

        # 游戏数据（operators.json）：补干员、补技能图标/职业/属性；素材目录优先。
        game_ops: list[GameOperator] = []
        if self.game_data is not None and self.game_data.available:
            game_ops = self.game_data.operators
        index_by_name = {operator.name: index for index, operator in enumerate(ordered)}
        for entry in game_ops:
            name = entry.name
            if not name:
                continue
            if name in seen:
                index = index_by_name.get(name)
                if index is not None:
                    ordered[index] = self._merge_game_operator(ordered[index], entry)
                continue
            ordered.append(self._operator_from_game(entry))
            seen.add(name)

        self._operators = ordered
        self._by_name = {op.name: op for op in ordered}
        log.info(
            "素材就绪：%s 名干员（素材 %s / 游戏数据 %s）",
            len(ordered),
            len(directories),
            len(game_ops),
        )

    def _merge_game_operator(self, operator: Operator, entry: GameOperator) -> Operator:
        """素材目录优先、游戏数据补缺（只补空字段，不覆盖用户素材）。"""
        class_name = operator.class_name or entry.profession
        element = operator.element or entry.element
        return replace(
            operator,
            class_name=class_name,
            element=element,
            avatar=operator.avatar or entry.avatar or self._gamedata_avatar(entry.name),
            link=self.gen_icon(entry.name, "link") or operator.link or self._data_skill_icon(entry, "link"),
            skill=self.gen_icon(entry.name, "skill") or operator.skill or self._data_skill_icon(entry, "skill"),
            ult=self.gen_icon(entry.name, "ult") or operator.ult or self._data_skill_icon(entry, "ult"),
            class_icon=self.class_icon(class_name),
            element_icon=self.element_icon(element),
        )

    def _operator_from_game(self, entry: GameOperator) -> Operator:
        """只有游戏数据、没有素材目录的干员（头像/技能图标来自 gamedata 缓存，缺图用占位）。"""
        return Operator(
            name=entry.name,
            op_id=None,
            class_name=entry.profession,
            element=entry.element,
            avatar=entry.avatar or self._gamedata_avatar(entry.name),
            link=self.gen_icon(entry.name, "link") or self._data_skill_icon(entry, "link"),
            skill=self.gen_icon(entry.name, "skill") or self._data_skill_icon(entry, "skill"),
            ult=self.gen_icon(entry.name, "ult") or self._data_skill_icon(entry, "ult"),
            class_icon=self.class_icon(entry.profession),
            element_icon=self.element_icon(entry.element),
            color=None,
        )

    def _operator(self, name: str, op_id: int | None, directory: Path) -> Operator:
        class_name = _read_text(directory / "class.txt")
        element = _read_text(directory / "element.txt")
        color = ""
        raw_color = _read_text(directory / "color.txt").lstrip("#").strip()
        if len(raw_color) == 6:
            try:
                int(raw_color, 16)
                color = f"#{raw_color.upper()}"
            except ValueError:
                color = ""
        return Operator(
            name=name,
            op_id=op_id,
            class_name=class_name,
            element=element,
            avatar=_resolve(directory, "avatar.png"),
            link=self.gen_icon(name, "link") or _resolve(directory, "link.png"),
            skill=self.gen_icon(name, "skill") or _resolve(directory, "skill.png"),
            ult=self.gen_icon(name, "ult") or _resolve(directory, "ult.png"),
            class_icon=self.class_icon(class_name),
            element_icon=self.element_icon(element),
            color=color or None,
        )

    # -- 便捷 ---------------------------------------------------------------
    def sorted_names(self) -> list[str]:
        return list(self.names)
