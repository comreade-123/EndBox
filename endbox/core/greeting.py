# -*- coding: utf-8 -*-
"""首次启动的问候语：按本地时间问候，名字取绑定的账号名，未绑定叫"管理员"。"""
from __future__ import annotations

from datetime import datetime

from endbox.core.text import t

FALLBACK_NAME = ""  # 运行时从文案目录取"管理员"

# (起始小时, 问候语文案 key)，按顺序匹配，最后一条兜底
_GREETINGS = [
    (5, "home.greeting.morning"),
    (9, "home.greeting.forenoon"),
    (11, "home.greeting.noon"),
    (13, "home.greeting.afternoon"),
    (18, "home.greeting.evening"),
    (23, "home.greeting.late_night"),
]


def _child_name() -> str:
    return t("home.greeting.admin", "管理员")


def greeting_for(moment: datetime | None = None) -> str:
    """返回当前时段的问候语（文案来自 assets/text/zh-CN.json）。"""
    moment = moment or datetime.now()
    hour = moment.hour
    result = "home.greeting.late_night"
    for start, key in _GREETINGS:
        if hour >= start:
            result = key
    return t(result)


def display_name(account_state: dict | None) -> str:
    """绑定的账号昵称；未绑定 / 未加载 / 演示之外一律叫"管理员"。"""
    if not account_state or account_state.get("demo"):
        return _child_name()
    nickname = account_state.get("nickname")
    if isinstance(nickname, str) and nickname.strip():
        return nickname.strip()
    return _child_name()


def welcome_greeting(account_state: dict | None, moment: datetime | None = None) -> str:
    """大标题：`早上好，管理员`。"""
    return t(
        "home.greeting.joined",
        "，",
        greeting=greeting_for(moment),
        name=display_name(account_state),
    )


def welcome_subline(moment: datetime | None = None) -> str:
    """副标题：今天肝点什么？"""
    moment = moment or datetime.now()
    if moment.hour >= 23 or moment.hour < 5:
        return t("home.greeting.subline_late", "今天肝点什么？别熬太晚。")
    return t("home.greeting.subline", "今天肝点什么？")


def time_fields(moment: datetime | None = None) -> list[str]:
    """真实的日期 / 时间 / 星期字段。"""
    moment = moment or datetime.now()
    weekday = t(f"home.weekdays.{moment.weekday() + 1}", str(moment.weekday() + 1))
    return [
        moment.strftime("%Y.%m.%d"),
        moment.strftime("%H:%M"),
        weekday,
    ]
