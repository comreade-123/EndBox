# -*- coding: utf-8 -*-
"""首页问候语测试：时段、名字（绑定账号 / 未绑定 / 演示）。"""
from __future__ import annotations

from datetime import datetime

from endbox.core.greeting import (
    display_name,
    greeting_for,
    time_fields,
    welcome_greeting,
    welcome_subline,
)


def test_greeting_by_hour():
    cases = {
        3: "夜深了",
        5: "早上好",
        7: "早上好",
        9: "上午好",
        11: "中午好",
        14: "下午好",
        18: "晚上好",
        23: "夜深了",
    }
    for hour, expected in cases.items():
        assert greeting_for(datetime(2026, 11, 13, hour, 30)) == expected


def test_display_name_prefers_bound_nickname():
    assert display_name({"nickname": "博士", "online": True}) == "博士"
    # 未绑定 / 演示数据 → 文案里的「管理员」
    from endbox.core.text import t

    admin = t("home.greeting.admin")
    assert display_name({"nickname": "", "online": True}) == admin
    assert display_name(None) == admin
    assert display_name({"nickname": "演示玩家", "demo": True}) == admin


def test_welcome_greeting_and_subline():
    moment = datetime(2026, 11, 13, 7, 5)
    assert welcome_greeting(None, moment) == "早上好，管理员"
    assert welcome_greeting({"nickname": "博士"}, moment) == "早上好，博士"
    assert "今天肝点什么" in welcome_subline(moment)
    late = datetime(2026, 11, 13, 1, 0)
    assert welcome_subline(late) != welcome_subline(moment)


def test_time_fields_are_real():
    fields = time_fields(datetime(2026, 11, 13, 22, 39))
    assert fields == ["2026.11.13", "22:39", "周五"]
