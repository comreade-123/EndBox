# -*- coding: utf-8 -*-
"""账号模块的数据模型与状态结构。

账号状态是主程序与插件之间的公开契约（见 docs/CONTRACT.md §7），
所以这里只依赖标准库与 endbox 自己的路径配置，不引用 UI。

2026-11 扩展：除契约原有的 ``roles`` 列表外，state 新增 ``player``（card/detail
整理后的玩家档案）、``operators``（干员列表）、``role_index``（当前选择的角色）
与 ``data_error``（绑定成功但完整数据失败时的人话提示）。**旧字段一个都没删**，
旧版本代码只读 ``online / nickname / uid_masked / roles / last_sync / demo / error``
时行为不变。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from endbox import config
from endbox.core.text import t

PROVIDER_SKLAND = "skland"
PROVIDER_BILIBILI = "bilibili"

PROVIDER_LABELS = {
    PROVIDER_SKLAND: t("account.provider.skland_label"),
    PROVIDER_BILIBILI: t("account.provider.bili_label"),
}

# state / snapshot 的固定字段（缺失字段一律 None / 0 / []，见契约）
STATE_KEYS = (
    "online",
    "provider",
    "nickname",
    "uid_masked",
    "avatar_path",
    "roles",
    "last_sync",
    "demo",
    "error",
    # -- 2026-11 真实游戏数据（旧字段全部保留，向后兼容）--
    "player",
    "operators",
    "role_index",
    "data_error",
)

# secure_store 的键名（DPAPI 加密落盘，绝不写明文到磁盘）
BILI_COOKIE_PREFIX = "bili."

# 本地素材目录（用户提供的游戏素材，不随程序分发；只读路径拼接）
OPERATOR_ASSETS = config.GAME_ASSETS / "operators"


class AccountError(Exception):
    """账号流程中面向用户的错误：message 直接可以显示在界面上。"""

    def __init__(self, message: str, *, hint: str = "", status: int | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status

    def display(self) -> str:
        if self.hint:
            return f"{self.message} {self.hint}"
        return self.message


@dataclass
class Role:
    """一个游戏角色。字段缺失用 None，界面上显示 —，不编造。

    ``role_id`` / ``server_id`` 只用于调用 card/detail（roleId / serverId 参数与
    ``sk-game-role`` 请求头），**不写进 state 的 roles 列表**，避免完整 UID 外泄。
    """

    name: str
    level: int | None = None
    server: str | None = None
    klass: str | None = None
    element: str | None = None
    role_id: str | None = None
    server_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        # 契约字段保持原样（旧消费方按这 5 个键读取）
        return {
            "name": self.name,
            "level": self.level,
            "server": self.server,
            "class": self.klass,
            "element": self.element,
        }

    @property
    def api_ready(self) -> bool:
        """是否拿到拉取 card/detail 所需的 roleId / serverId。"""
        return bool(self.role_id and self.server_id)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Role":
        return cls(
            name=str(data.get("name") or t("common.placeholder")),
            level=to_int(data.get("level")),
            server=none_or_str(data.get("server")),
            klass=none_or_str(data.get("class")),
            element=none_or_str(data.get("element")),
        )


@dataclass
class Operator:
    """一条拥有记录里的干员（来自 card/detail 的 chars[]）。"""

    name: str
    char_id: str | None = None
    rarity: int | None = None
    rarity_key: str | None = None
    profession: str | None = None
    element: str | None = None
    level: int | None = None
    evolve_phase: int | None = None
    potential_level: int | None = None
    weapon_name: str | None = None
    weapon_rarity: int | None = None
    weapon_level: int | None = None
    avatar_url: str | None = None
    # 本地素材解析结果（不来自网络；缺图时界面用名称首字占位）
    avatar_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "char_id": self.char_id,
            "rarity": self.rarity,
            "rarity_key": self.rarity_key,
            "profession": self.profession,
            "element": self.element,
            "level": self.level,
            "evolve_phase": self.evolve_phase,
            "potential_level": self.potential_level,
            "weapon_name": self.weapon_name,
            "weapon_rarity": self.weapon_rarity,
            "weapon_level": self.weapon_level,
            "avatar_url": self.avatar_url,
            "avatar_path": self.avatar_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Operator":
        name = none_or_str(data.get("name")) or t("common.placeholder")
        return cls(
            name=name,
            char_id=none_or_str(data.get("char_id")),
            rarity=to_int(data.get("rarity")),
            rarity_key=none_or_str(data.get("rarity_key")),
            profession=none_or_str(data.get("profession")),
            element=none_or_str(data.get("element")),
            level=to_int(data.get("level")),
            evolve_phase=to_int(data.get("evolve_phase")),
            potential_level=to_int(data.get("potential_level")),
            weapon_name=none_or_str(data.get("weapon_name")),
            weapon_rarity=to_int(data.get("weapon_rarity")),
            weapon_level=to_int(data.get("weapon_level")),
            avatar_url=none_or_str(data.get("avatar_url")),
            avatar_path=none_or_str(data.get("avatar_path")),
        )


@dataclass
class PlayerCard:
    """card/detail 解析后的完整玩家数据（字段缺失为 None，界面显示 —）。"""

    role_id: str | None = None
    server_id: str | None = None
    server_name: str | None = None
    nickname: str | None = None
    level: int | None = None
    world_level: int | None = None
    exp: int | None = None
    avatar_url: str | None = None
    # 本地头像路径（本版不下载远程图片；缺图时界面用昵称首字占位）
    avatar_path: str | None = None
    char_num: int | None = None
    weapon_num: int | None = None
    doc_num: int | None = None
    create_time: int | None = None
    last_login_time: int | None = None
    save_time: int | None = None
    main_mission: str | None = None
    cur_stamina: int | None = None
    max_stamina: int | None = None
    stamina_full_ts: int | None = None
    bp_level: int | None = None
    bp_max_level: int | None = None
    daily_activation: int | None = None
    daily_max_activation: int | None = None
    achieve_count: int | None = None
    domains: list[dict[str, Any]] = field(default_factory=list)
    spaceship_rooms: list[dict[str, Any]] = field(default_factory=list)
    operators: list[Operator] = field(default_factory=list)


@dataclass
class PlayerProfile:
    """一次成功拉取后的玩家资料（由具体渠道的解析器返回）。"""

    provider: str
    nickname: str | None = None
    uid: str | None = None
    roles: list[Role] = field(default_factory=list)
    avatar_path: str | None = None
    # 2026-11：card/detail 完整数据；绑定成功但完整数据失败时 card=None + data_error
    card: PlayerCard | None = None
    data_error: str | None = None
    role_index: int = 0


def none_or_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def mask_uid(uid: Any) -> str | None:
    """UID 脱敏：保留头尾，中间用 • 代替（界面与插件都不该看到完整 UID）。"""
    text = none_or_str(uid)
    if not text:
        return None
    if len(text) <= 4:
        return "•" * len(text)
    if len(text) <= 7:
        return text[:1] + "•" * (len(text) - 2) + text[-1]
    return text[:2] + "•" * (len(text) - 4) + text[-2:]


def format_sync(ts: Any) -> str:
    """unix 秒 → `2026.11.13 22:40`；0 表示从未同步。"""
    stamp = to_int(ts) or 0
    if stamp <= 0:
        return t("account.time.never_synced")
    try:
        return datetime.fromtimestamp(stamp).strftime("%Y.%m.%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return t("account.time.never_synced")


def format_timestamp(ts: Any) -> str:
    """unix 秒 → `2026.11.13 22:40`；没有数据返回 —（不编造）。"""
    stamp = to_int(ts) or 0
    if stamp <= 0:
        return t("common.placeholder")
    try:
        return datetime.fromtimestamp(stamp).strftime("%Y.%m.%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return t("common.placeholder")


def operator_avatar(name: str | None) -> str | None:
    """按干员名在本地素材里找头像；没有就 None（界面用首字占位）。"""
    text = none_or_str(name)
    if not text or any(ch in text for ch in ("/", "\\", ":", "\x00")):
        return None
    try:
        candidate = OPERATOR_ASSETS / text / "avatar.png"
        return str(candidate) if candidate.is_file() else None
    except OSError:
        return None


def operator_state(operator: Operator) -> dict[str, Any]:
    if not operator.avatar_path:
        operator.avatar_path = operator_avatar(operator.name)
    return operator.as_dict()


def card_to_state(card: PlayerCard) -> dict[str, Any]:
    """PlayerCard → state["player"]（UID 只留脱敏值）。"""
    return {
        "nickname": card.nickname,
        "uid_masked": mask_uid(card.role_id),
        "server_name": card.server_name,
        "level": card.level,
        "world_level": card.world_level,
        "exp": card.exp,
        "avatar_path": card.avatar_path,
        "char_num": card.char_num,
        "weapon_num": card.weapon_num,
        "doc_num": card.doc_num,
        "create_time": card.create_time,
        "last_login_time": card.last_login_time,
        "save_time": card.save_time,
        "main_mission": card.main_mission,
        "stamina": {
            "cur": card.cur_stamina,
            "max": card.max_stamina,
            "full_ts": card.stamina_full_ts,
        },
        "bp": {"cur": card.bp_level, "max": card.bp_max_level},
        "daily": {"cur": card.daily_activation, "max": card.daily_max_activation},
        "achieve_count": card.achieve_count,
        "domains": [dict(item) for item in card.domains],
        "spaceship_rooms": [dict(item) for item in card.spaceship_rooms],
    }


def blank_state(**overrides: Any) -> dict[str, Any]:
    """契约里的空状态：所有字段都在，失败与未连接都是合法状态。"""
    state: dict[str, Any] = {
        "online": False,
        "provider": "",
        "nickname": None,
        "uid_masked": None,
        "avatar_path": None,
        "roles": [],
        "last_sync": 0,
        "demo": False,
        "error": None,
        "player": None,
        "operators": [],
        "role_index": 0,
        "data_error": None,
    }
    for key, value in overrides.items():
        if key in state:
            state[key] = value
    return state


def state_from_profile(profile: PlayerProfile, *, last_sync: int, demo: bool = False) -> dict[str, Any]:
    """把渠道返回的资料整理成契约状态（旧字段 + 新的 player/operators）。"""
    roles = [role if isinstance(role, Role) else Role.from_dict(role) for role in profile.roles]
    card = profile.card
    operators = [operator_state(op) for op in card.operators] if card else []
    player = card_to_state(card) if card else None
    nickname = (card.nickname if card and card.nickname else None) or profile.nickname
    uid = (card.role_id if card and card.role_id else None) or profile.uid
    return blank_state(
        online=True,
        provider=profile.provider,
        nickname=nickname,
        uid_masked=mask_uid(uid),
        avatar_path=profile.avatar_path,
        roles=[role.as_dict() for role in roles],
        last_sync=int(last_sync),
        demo=bool(demo),
        error=None,
        player=player,
        operators=operators,
        role_index=max(0, to_int(profile.role_index) or 0),
        data_error=none_or_str(profile.data_error),
    )


# -- 演示数据 ---------------------------------------------------------------
_DEMO_DOMAIN = {
    "name": "塔卫二",
    "level": 12,
    "settlements": 3,
    "settlement_max_level": 5,
    "puzzle": 12,
    "chest": 8,
    "piece": 15,
    "blackbox": 2,
}


def demo_state(now: int | None = None) -> dict[str, Any]:
    """演示数据：仅用于界面评审，state["demo"] = True 会在界面上明确标注。

    结构与真实 card/detail 解析结果一致（见 skland.parse_card_detail），
    这样演示模式能完整演练新界面，同时不触碰任何真实凭据。
    """
    stamp = int(now) if now is not None else int(datetime.now().timestamp())
    demo_roles = [
        Role("伊冯", 60, "官服", "近卫", "寒冷", role_id="10448293", server_id="1"),
        Role("佩丽卡", 50, "官服", "术师", "电磁", role_id="10448001", server_id="1"),
        Role("余烬", 45, "官服", "重装", "灼热", role_id="10448002", server_id="1"),
        Role("陈千语", 40, "官服", "先锋", "物理", role_id="10448003", server_id="1"),
    ]
    operators = [
        Operator(
            name="伊冯",
            char_id="chr_0001_yifeng",
            rarity=6,
            rarity_key="rarity_6",
            profession="近卫",
            element="寒冷",
            level=60,
            evolve_phase=4,
            potential_level=2,
            weapon_name="演示武器·霜痕",
            weapon_rarity=6,
            weapon_level=60,
        ),
        Operator(
            name="佩丽卡",
            char_id="chr_0002_peilika",
            rarity=6,
            rarity_key="rarity_6",
            profession="术师",
            element="电磁",
            level=50,
            evolve_phase=3,
            potential_level=1,
        ),
        Operator(
            name="余烬",
            char_id="chr_0003_yujin",
            rarity=5,
            rarity_key="rarity_5",
            profession="重装",
            element="灼热",
            level=45,
            evolve_phase=2,
            potential_level=0,
        ),
        Operator(
            name="陈千语",
            char_id="chr_0004_chenqianyu",
            rarity=4,
            rarity_key="rarity_4",
            profession="先锋",
            element="物理",
            level=40,
            evolve_phase=1,
            potential_level=3,
        ),
    ]
    card = PlayerCard(
        role_id="10448293",
        server_id="1",
        server_name="官服",
        nickname="演示玩家",
        level=60,
        world_level=5,
        exp=12800,
        char_num=len(operators),
        weapon_num=5,
        doc_num=12,
        create_time=stamp - 90 * 86400,
        last_login_time=stamp - 3600,
        save_time=stamp - 120,
        main_mission="演示数据：前往塔卫二",
        cur_stamina=92,
        max_stamina=135,
        # 演示数据按真实恢复速度（6 分钟 1 点）算满溢时间，界面反推恢复速度才真实
        stamina_full_ts=stamp + (135 - 92) * 6 * 60,
        bp_level=27,
        bp_max_level=60,
        daily_activation=3,
        daily_max_activation=5,
        achieve_count=46,
        domains=[
            dict(_DEMO_DOMAIN),
            {
                "name": "四号谷地",
                "level": 8,
                "settlements": 2,
                "settlement_max_level": 3,
                "puzzle": 5,
                "chest": 3,
                "piece": 7,
                "blackbox": 1,
            },
        ],
        spaceship_rooms=[{"type": 0, "level": 3}, {"type": 1, "level": 2}],
        operators=operators,
    )
    profile = PlayerProfile(
        provider=PROVIDER_SKLAND,
        nickname=card.nickname,
        uid=card.role_id,
        roles=demo_roles,
        card=card,
        avatar_path=operator_avatar("伊冯"),
    )
    state = state_from_profile(profile, last_sync=stamp, demo=True)
    return state


def coerce_roles(values: Iterable[Any]) -> list[Role]:
    out: list[Role] = []
    for value in values:
        if isinstance(value, Role):
            out.append(value)
        elif isinstance(value, dict):
            out.append(Role.from_dict(value))
        elif value:
            out.append(Role(str(value)))
    return out


def avatar_exists(path: str | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False
