# -*- coding: utf-8 -*-
"""账号模块测试：不联网，只验证数据结构、扫码状态机解析、签名、存凭据与错误人话。

运行：
    python run_tests.py tests/test_account.py -q
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from endbox.config import Settings
from endbox.core.event_bus import bus as global_bus
from endbox.core.net import NetworkError
from endbox.core.secure_store import SecureStore
from endbox.modules.account import bilibili, qr, skland
from endbox.modules.account import service as service_mod
from endbox.modules.account.models import (
    PROVIDER_SKLAND,
    STATE_KEYS,
    AccountError,
    PlayerProfile,
    Role,
    blank_state,
    demo_state,
    mask_uid,
    state_from_profile,
)
from endbox.modules.account.service import AccountService
from endbox.services import Services


# -- 测试用临时上下文 -------------------------------------------------------
class _Ctx:
    """足够小的 AppContext 替身：设置写临时文件，事件总线用进程级实现。"""

    def __init__(self, tmp_path: Path):
        self.settings = Settings(tmp_path / "settings.json")
        self.bus = global_bus
        self.services = Services()
        self.main_window = None
        self._toasts: list[tuple[str, str]] = []

    def notify(self, text: str, kind: str = "info") -> None:
        self._toasts.append((text, kind))


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """把 B 站 / 森空岛模块里的 secure_store 换成临时文件，避免动真实凭据。"""
    store = SecureStore(tmp_path / "secure.dat")
    monkeypatch.setattr(skland, "secure_store", store)
    monkeypatch.setattr(bilibili, "secure_store", store)
    return store


@pytest.fixture()
def ctx(tmp_path):
    return _Ctx(tmp_path)


# -- 凭据存储 ---------------------------------------------------------------
def test_secure_store_roundtrip_is_not_plaintext(tmp_path):
    store = SecureStore(tmp_path / "secure.dat")
    store.set("skland.credential", "very-secret-credential")
    assert store.get("skland.credential") == "very-secret-credential"
    raw = (tmp_path / "secure.dat").read_bytes()
    assert b"very-secret-credential" not in raw
    store.delete("skland.credential")
    assert store.get("skland.credential") is None


# -- 状态结构（契约 §7）-----------------------------------------------------
def test_blank_state_has_all_contract_keys():
    state = blank_state()
    assert set(state) == set(STATE_KEYS)
    assert state["online"] is False
    assert state["roles"] == []
    assert state["last_sync"] == 0
    assert state["demo"] is False
    assert state["error"] is None


def test_mask_uid_keeps_head_and_tail_only():
    masked = mask_uid("10448293")
    assert masked == "10••••93"
    assert mask_uid(None) is None


def test_state_from_profile_matches_contract():
    profile = PlayerProfile(
        provider=PROVIDER_SKLAND,
        nickname="伊冯",
        uid="10448293",
        roles=[Role("伊冯", 60, "官服", "突击", "寒冷")],
    )
    state = state_from_profile(profile, last_sync=1700000000)
    assert set(state) == set(STATE_KEYS)
    assert state["online"] is True
    assert state["uid_masked"] == "10••••93"
    role = state["roles"][0]
    assert set(role) == {"name", "level", "server", "class", "element"}
    assert role["class"] == "突击"


def test_demo_state_is_explicitly_flagged():
    state = demo_state(now=1700000000)
    assert state["demo"] is True
    assert state["online"] is True
    assert state["roles"]


# -- 森空岛：设备 ID / 扫码创建 ---------------------------------------------
def test_device_id_is_stable_and_persisted(isolated_store):
    first = skland.device_id()
    second = skland.device_id()
    assert first == second
    assert len(first) >= 8
    assert isolated_store.get(skland.DEVICE_KEY) == first


def test_scan_created_parses_scan_id_and_url():
    qr_info = skland.parse_scan_created(
        {
            "status": 0,
            "msg": "OK",
            "data": {
                "scanId": "3c64d7b5ec529b035504abbfea769938",
                "scanUrl": "hypergryph://scan_login?scanId=3c64d7b5ec529b035504abbfea769938",
            },
        }
    )
    assert qr_info.scan_id == "3c64d7b5ec529b035504abbfea769938"
    assert qr_info.scan_url.startswith("hypergryph://scan_login")


def test_scan_created_missing_url_is_human_error():
    with pytest.raises(AccountError) as info:
        skland.parse_scan_created({"status": 0, "data": {"scanId": "x", "scanUrl": ""}})
    assert "扫码链接" in info.value.display()


def test_scan_created_error_code_is_human_error():
    with pytest.raises(AccountError) as info:
        skland.parse_scan_created({"status": 1001, "msg": "请求过于频繁"})
    assert "1001" in info.value.display() and ("重试" in info.value.display() or "再试" in info.value.display())


def test_scan_create_network_failure_maps_to_account_error(monkeypatch):
    def offline(*_args, **_kwargs):
        raise NetworkError("网络请求失败：timed out")

    monkeypatch.setattr(skland.net, "request", offline)
    with pytest.raises(AccountError) as info:
        skland.create_scan_login()
    text = info.value.display()
    assert "网络" in text and ("重试" in text or "再试" in text)


# -- 森空岛：扫码状态 -------------------------------------------------------
def test_scan_status_waiting():
    result = skland.parse_scan_status({"status": 100, "msg": "未扫码"})
    assert result.status == skland.SCAN_WAITING
    assert result.terminal is False


def test_scan_status_scanned():
    result = skland.parse_scan_status({"status": 101, "msg": "已扫码，等待确认"})
    assert result.status == skland.SCAN_SCANNED


def test_scan_status_code_100_without_message_is_waiting():
    result = skland.parse_scan_status({"status": 100})
    assert result.status == skland.SCAN_WAITING


def test_scan_status_expired_and_canceled():
    expired = skland.parse_scan_status({"status": 102, "msg": "二维码已失效"})
    assert expired.status == skland.SCAN_EXPIRED
    assert expired.terminal is True
    canceled = skland.parse_scan_status({"status": 103, "msg": "已取消登录"})
    assert canceled.status == skland.SCAN_CANCELED


def test_scan_status_confirmed_returns_scan_code():
    result = skland.parse_scan_status({"status": 0, "data": {"scanCode": "abc123"}})
    assert result.status == skland.SCAN_CONFIRMED
    assert result.scan_code == "abc123"
    assert result.terminal is True


def test_scan_status_confirmed_without_scan_code_is_error():
    with pytest.raises(AccountError):
        skland.parse_scan_status({"status": 0, "data": {}})


def test_scan_status_unknown_message_is_error():
    with pytest.raises(AccountError) as info:
        skland.parse_scan_status({"status": 500, "msg": "服务异常"})
    assert "扫码状态" in info.value.display()


# -- 森空岛：token / grant / cred -------------------------------------------
def test_parse_scan_token_success_and_expired():
    assert skland.parse_scan_token({"status": 0, "data": {"token": "oauth-token"}}) == "oauth-token"
    with pytest.raises(AccountError) as info:
        skland.parse_scan_token({"status": 1, "msg": "扫码凭证已失效"})
    assert "过期" in info.value.display() or "失效" in info.value.display()


def test_parse_grant_code():
    assert (
        skland.parse_grant_code({"status": 0, "data": {"code": "grant-code"}})
        == "grant-code"
    )
    with pytest.raises(AccountError):
        skland.parse_grant_code({"status": 1, "msg": "token invalid"})


def test_parse_credential_builds_credential():
    credential = skland.parse_credential(
        {
            "code": 0,
            "data": {
                "cred": "cred-value",
                "token": "sign-token",
                "userId": "8****3",
            },
        },
        oauth_token="oauth-token",
    )
    assert credential["cred"] == "cred-value"
    assert credential["token"] == "sign-token"
    assert credential["oauthToken"] == "oauth-token"
    assert credential["userId"] == "8****3"


def test_parse_credential_missing_fields_is_human_error():
    with pytest.raises(AccountError) as info:
        skland.parse_credential({"code": 0, "data": {"cred": "c"}})
    assert "凭据" in info.value.display()


# -- 森空岛：签名 -----------------------------------------------------------
def test_build_signed_headers_matches_community_algorithm(monkeypatch):
    monkeypatch.setattr(skland, "_now_ts_fresh", lambda: 1700000123)
    headers = {"cred": "cred-value", "token": "sign-token", "Content-Type": "application/json"}
    signed = skland.build_signed_headers(
        "sign-token",
        path="/api/v1/game/player/binding",
        query="",
        device="dev123456",
        headers=headers,
        platform="1",
        version="1.21.0",
    )
    timestamp = "1700000121"  # 服务器时间 - 2
    signature_headers = {
        "platform": "1",
        "timestamp": timestamp,
        "dId": "dev123456",
        "vName": "1.21.0",
    }
    text = (
        "/api/v1/game/player/binding"
        + ""
        + timestamp
        + json.dumps(signature_headers, separators=(",", ":"))
    )
    expected = hashlib.md5(
        hmac.new(b"sign-token", text.encode("utf-8"), hashlib.sha256).hexdigest().encode()
    ).hexdigest()
    assert signed["sign"] == expected
    assert signed["timestamp"] == timestamp
    assert signed["dId"] == "dev123456"
    assert "token" not in signed, "签名 token 只用于计算，不应出现在请求头里"
    assert signed["cred"] == "cred-value"


# -- 森空岛：凭据存储 / 绑定解析 -------------------------------------------
def test_credential_store_roundtrip(isolated_store):
    credential = {
        "cred": "cred-value",
        "token": "sign-token",
        "userId": "8****3",
        "oauthToken": "oauth-token",
    }
    skland.save_credential(credential)
    loaded = skland.load_credential()
    assert loaded == credential
    assert skland.has_credential() is True
    raw = isolated_store.get(skland.CREDENTIAL_KEY)
    assert raw and "cred-value" in raw  # 存储层是 DPAPI，明文只在本进程内
    skland.clear_credential()
    assert skland.load_credential() is None
    assert skland.has_credential() is False


def test_binding_parse_filters_endfield():
    payload = {
        "code": 0,
        "message": "OK",
        "data": {
            "list": [
                {
                    "appCode": "arknights",
                    "appName": "明日方舟",
                    "bindingList": [{"uid": "111", "nickName": "不是终末地"}],
                },
                {
                    "appCode": "endfield",
                    "appName": "明日方舟：终末地",
                    "bindingList": [
                        {
                            "uid": "10448293",
                            "nickName": "伊冯",
                            "channelName": "官服",
                        }
                    ],
                },
            ]
        },
    }
    profile = skland.parse_bindings(payload)
    assert profile.provider == PROVIDER_SKLAND
    assert profile.nickname == "伊冯"
    assert profile.uid == "10448293"
    assert len(profile.roles) == 1
    assert profile.roles[0].server == "官服"


def test_binding_parse_without_endfield_is_human_error():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {"appCode": "arknights", "appName": "明日方舟", "bindingList": [{"uid": "1", "nickName": "A"}]}
            ]
        },
    }
    with pytest.raises(AccountError) as info:
        skland.parse_bindings(payload)
    assert "终末地" in info.value.display()


def test_binding_auth_error_is_credential_expired(monkeypatch):
    monkeypatch.setattr(
        skland,
        "_request_json",
        lambda *_args, **_kwargs: {"code": 401, "message": "登录已过期"},
    )
    with pytest.raises(skland.CredentialExpiredError) as info:
        skland.fetch_binding_payload({"cred": "c", "token": "t"})
    assert "失效" in info.value.display() or "过期" in info.value.display()


def test_binding_http_401_is_credential_expired(monkeypatch):
    class _Response:
        status_code = 401
        headers: dict[str, str] = {}

        def json(self):
            return {}

    monkeypatch.setattr(skland.net, "request", lambda *_args, **_kwargs: _Response())
    with pytest.raises(skland.CredentialExpiredError):
        skland.fetch_binding_payload({"cred": "c", "token": "t"})


def test_fetch_profile_with_refresh_retries_once(monkeypatch):
    calls = {"profile": 0}

    def fake_profile(credential, *, device=None):
        calls["profile"] += 1
        if calls["profile"] == 1:
            raise skland.CredentialExpiredError("登录已失效")
        return PlayerProfile(provider=PROVIDER_SKLAND, nickname="伊冯", uid="10448293")

    monkeypatch.setattr(skland, "fetch_profile", fake_profile)
    monkeypatch.setattr(
        skland,
        "refresh_credential",
        lambda credential, *, device=None: {**credential, "token": "new-token"},
    )
    profile, refreshed = skland.fetch_profile_with_refresh(
        {"cred": "c", "token": "old-token", "userId": "1", "oauthToken": "o"}
    )
    assert calls["profile"] == 2
    assert profile.nickname == "伊冯"
    assert refreshed["token"] == "new-token"


# -- B 服解析 ---------------------------------------------------------------
def test_bilibili_poll_waiting_and_scanned():
    waiting = bilibili.parse_poll_payload({"code": 0, "data": {"code": 86101}})
    assert waiting.status == bilibili.QR_WAITING
    scanned = bilibili.parse_poll_payload({"code": 0, "data": {"code": 86090}})
    assert scanned.status == bilibili.QR_SCANNED


def test_bilibili_poll_expired():
    result = bilibili.parse_poll_payload({"code": 0, "data": {"code": 86038}})
    assert result.status == bilibili.QR_EXPIRED
    assert result.terminal is True


def test_bilibili_poll_success_extracts_cookies():
    url = (
        "https://passport.biligame.com/crossDomain?DedeUserID=12345&"
        "DedeUserID__ckMd5=abc&Expires=1&SESSDATA=sess%2Cvalue&bili_jct=csrf"
    )
    result = bilibili.parse_poll_payload({"code": 0, "data": {"code": 0, "url": url}})
    assert result.status == bilibili.QR_OK
    assert result.cookies["SESSDATA"] == "sess,value"
    assert result.cookies["bili_jct"] == "csrf"


def test_bilibili_success_without_cookie_is_human_error():
    with pytest.raises(AccountError) as info:
        bilibili.parse_poll_payload({"code": 0, "data": {"code": 0, "url": ""}})
    assert "Cookie" in info.value.display()


def test_bilibili_generate_network_failure(monkeypatch):
    def offline(*_args, **_kwargs):
        raise NetworkError("网络请求失败：connection refused")

    monkeypatch.setattr(bilibili.net, "get_json", offline)
    with pytest.raises(AccountError) as info:
        bilibili.generate_qr()
    assert "网络" in info.value.display()


def test_bilibili_cookie_store_roundtrip(isolated_store):
    cookies = {"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "42"}
    bilibili.save_cookies(cookies)
    assert bilibili.load_cookies()["SESSDATA"] == "s"
    bilibili.clear_cookies()
    assert bilibili.load_cookies() == {}


# -- 二维码 -----------------------------------------------------------------
def test_qr_matrix_is_square_boolean_grid():
    matrix = qr.make_matrix("hypergryph://scan_login?scanId=3c64d7b5ec529b035504abbfea769938")
    assert len(matrix) > 20
    assert qr.is_valid_matrix(matrix)
    assert all(isinstance(value, bool) for row in matrix for value in row)
    assert any(any(row) for row in matrix)


def test_qr_empty_content_is_human_error():
    with pytest.raises(AccountError):
        qr.make_matrix("")


# -- 服务：演示模式 / 登出 / 无凭据刷新 --------------------------------------
def test_service_demo_then_logout(isolated_store, ctx):
    service = AccountService(ctx)
    events: list[dict] = []
    ctx.bus.subscribe("account.updated", lambda snapshot=None, **_: events.append(snapshot))

    service.set_demo_mode(True)
    state = service.snapshot()
    assert state["demo"] is True and state["online"] is True
    assert state["nickname"] and state["roles"]
    assert events and events[-1]["demo"] is True

    service.logout()
    assert service.snapshot() == blank_state()
    assert isolated_store.get(skland.CREDENTIAL_KEY) is None
    assert service.demo_enabled() is False
    service.stop()


def test_service_has_credentials_reads_secure_store(isolated_store, ctx):
    service = AccountService(ctx)
    assert service.has_credentials() is False
    skland.save_credential({"cred": "c", "token": "t", "userId": "1", "oauthToken": "o"})
    assert service.has_credentials() is True
    service.stop()


def test_service_refresh_without_credentials_is_offline_not_error(isolated_store, ctx):
    service = AccountService(ctx)
    done: list[dict] = []
    service.refresh(on_done=done.append)
    state = service.snapshot()
    assert state["online"] is False
    assert state["error"] is None
    assert done and done[0]["online"] is False
    service.stop()


def test_service_stop_ignores_late_updates(isolated_store, ctx):
    service = AccountService(ctx)
    service.stop()
    service.set_demo_mode(True)
    assert service.snapshot()["demo"] is False


# -- 视图：退出登录必须先确认（取消时什么都不做）-----------------------------
def _make_account_view(ctx, monkeypatch):
    """构造真实 AccountView；构造期间订阅的事件在测试结束后取消，避免影响其他用例。"""
    import endbox.ui.views.account as view_mod

    subscriptions = []
    real_subscribe = global_bus.subscribe

    def tracking_subscribe(topic, handler):
        sub = real_subscribe(topic, handler)
        subscriptions.append(sub)
        return sub

    monkeypatch.setattr(global_bus, "subscribe", tracking_subscribe)
    view = view_mod.AccountView(ctx)
    for sub in subscriptions:
        sub.cancel()
    return view


def test_view_logout_cancel_does_nothing(isolated_store, ctx, qapp, monkeypatch):
    from endbox.core.text import t
    import endbox.ui.views.account as view_mod

    service = AccountService(ctx)
    ctx.services.account = service
    service.set_demo_mode(True)
    view = _make_account_view(ctx, monkeypatch)

    captured: dict = {}

    def fake_confirm(parent, title, message, **kwargs):
        captured.update(parent=parent, title=title, message=message, **kwargs)
        return False

    monkeypatch.setattr(view_mod, "confirm", fake_confirm)
    view._on_logout()

    assert captured, "退出登录必须先弹确认框"
    assert captured["danger"] is True, "退出登录的确认按钮必须是 danger"
    assert captured["title"] == t("account.confirm.logout_title")
    assert captured["action_text"] == t("account.confirm.logout_action")
    # 取消 → 状态与凭据都不变
    assert service.snapshot()["demo"] is True
    assert service.demo_enabled() is True
    assert isolated_store.get(skland.CREDENTIAL_KEY) is None
    assert not any(t("account.notify.logged_out") in text for text, _kind in ctx._toasts)
    view.deleteLater()


def test_view_logout_confirmed_clears_credentials(isolated_store, ctx, qapp, monkeypatch):
    from endbox.core.text import t
    import endbox.ui.views.account as view_mod

    skland.save_credential(
        {"cred": "c", "token": "t", "userId": "1", "oauthToken": "o"}
    )
    service = AccountService(ctx)
    ctx.services.account = service
    view = _make_account_view(ctx, monkeypatch)

    captured: dict = {}

    def fake_confirm(parent, title, message, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(view_mod, "confirm", fake_confirm)
    view._on_logout()

    assert captured.get("danger") is True
    assert skland.load_credential() is None
    assert bilibili.load_cookies() == {}
    state = service.snapshot()
    assert state["online"] is False and state["demo"] is False
    assert any(t("account.notify.logged_out") in text for text, _kind in ctx._toasts)
    view.deleteLater()


# ===========================================================================
# 2026-11：card/detail 真实数据结构（国服接口无真实账号，按公开文档结构 mock）
# ===========================================================================
CARD_DETAIL = {
    "code": 0,
    "message": "OK",
    "data": {
        "detail": {
            "base": {
                "roleId": "10448293",
                "serverId": "1",
                "serverName": "官服",
                "name": "伊冯",
                "level": 60,
                "worldLevel": 5,
                "exp": 12800,
                "gender": 1,
                "avatarUrl": "https://static.example/avatar.png",
                "charNum": 4,
                "weaponNum": 5,
                "docNum": 12,
                "createTime": 1700000000,
                "lastLoginTime": 1700003600,
                "saveTime": 1700003700,
                "mainMission": {"description": "抵达塔卫二"},
            },
            "dungeon": {"curStamina": 92, "maxStamina": 135, "maxTs": 1700010000},
            "bpSystem": {"curLevel": 27, "maxLevel": 60},
            "dailyMission": {"dailyActivation": 3, "maxDailyActivation": 5},
            "achieve": {"count": 46},
            "domain": [
                {
                    "name": "塔卫二",
                    "level": 12,
                    "settlements": [{"name": "据点A", "level": 5}, {"name": "据点B", "level": 3}],
                    "collections": {
                        "puzzleCount": 12,
                        "trchestCount": 8,
                        "pieceCount": 15,
                        "blackboxCount": 2,
                    },
                }
            ],
            "spaceShip": {"rooms": [{"type": 0, "level": 3}, {"type": 1, "level": 2}]},
            "chars": [
                {
                    "charData": {
                        "id": "chr_0001_yifeng",
                        "name": "伊冯",
                        "avatarSqUrl": "https://static.example/yifeng.png",
                        "rarity": {"key": "rarity_6", "value": 6},
                        "profession": {"value": "近卫", "key": "profession_guard"},
                        "property": {"value": "寒冷", "key": "element_cold"},
                        "skills": [{"id": "s1", "name": "技能"}],
                    },
                    "level": 60,
                    "evolvePhase": 4,
                    "potentialLevel": 2,
                    "userSkills": {"s1": {"level": 10, "maxLevel": 10}},
                    "weapon": {
                        "weaponData": {"name": "霜痕", "rarity": {"key": "rarity_6", "value": 6}},
                        "level": 60,
                        "refineLevel": 2,
                        "breakthroughLevel": 4,
                    },
                },
                {
                    "charData": {
                        "id": "chr_0002_peilika",
                        "name": "佩丽卡",
                        "rarity": {"key": "rarity_5", "value": 5},
                        "profession": {"value": "术师"},
                        "property": {"value": "电磁"},
                    },
                    "level": 50,
                    "evolvePhase": 3,
                    "potentialLevel": 1,
                },
            ],
        }
    },
}


def _card_payload():
    return json.loads(json.dumps(CARD_DETAIL))


def test_parse_card_detail_real_structure():
    card = skland.parse_card_payload(_card_payload())
    assert card.role_id == "10448293"
    assert card.server_id == "1"
    assert card.server_name == "官服"
    assert card.nickname == "伊冯"
    assert card.level == 60 and card.world_level == 5 and card.exp == 12800
    assert card.char_num == 4 and card.weapon_num == 5 and card.doc_num == 12
    assert card.main_mission == "抵达塔卫二"
    assert card.cur_stamina == 92 and card.max_stamina == 135
    assert card.stamina_full_ts == 1700010000
    assert card.bp_level == 27 and card.bp_max_level == 60
    assert card.daily_activation == 3 and card.daily_max_activation == 5
    assert card.achieve_count == 46
    assert card.domains == [
        {
            "name": "塔卫二",
            "level": 12,
            "settlements": 2,
            "settlement_max_level": 5,
            "puzzle": 12,
            "chest": 8,
            "piece": 15,
            "blackbox": 2,
        }
    ]
    assert card.spaceship_rooms == [{"type": 0, "level": 3}, {"type": 1, "level": 2}]
    assert len(card.operators) == 2
    first = card.operators[0]
    assert first.name == "伊冯"
    assert first.rarity == 6 and first.rarity_key == "rarity_6"
    assert first.profession == "近卫" and first.element == "寒冷"
    assert first.level == 60 and first.evolve_phase == 4 and first.potential_level == 2
    assert first.weapon_name == "霜痕" and first.weapon_rarity == 6
    assert first.weapon_level == 60
    assert first.avatar_url and first.avatar_path is None  # 不联网下载远程头像


def test_parse_card_detail_missing_fields_are_none_not_fabricated():
    card = skland.parse_card_payload(
        {"code": 0, "data": {"detail": {"base": {"name": "空数据"}}}}
    )
    assert card.nickname == "空数据"
    assert card.level is None and card.world_level is None
    assert card.cur_stamina is None and card.max_stamina is None
    assert card.operators == [] and card.domains == []


def test_parse_card_detail_missing_base_is_human_error():
    with pytest.raises(AccountError) as info:
        skland.parse_card_payload({"code": 0, "data": {"detail": {}}})
    assert "游戏数据" in info.value.display()


def test_parse_card_detail_ws_auth_error_is_credential_expired():
    with pytest.raises(skland.CredentialExpiredError):
        skland.parse_card_payload(
            {"code": 10002, "message": "用户未登录", "data": None}
        )


def test_parse_card_detail_api_error_is_human_error():
    with pytest.raises(AccountError) as info:
        skland.parse_card_payload({"code": 500, "message": "内部错误", "data": {}})
    assert "500" in info.value.display() and "游戏数据" in info.value.display()


def test_parse_card_detail_missing_data_is_human_error():
    with pytest.raises(AccountError) as info:
        skland.parse_card_payload({"code": 0})
    assert "游戏数据" in info.value.display()


def test_card_detail_requires_role_and_server_ids():
    with pytest.raises(AccountError) as info:
        skland.fetch_card_detail(
            {"cred": "c", "token": "t"}, Role("伊冯", role_id="1")
        )
    assert "roleId" in info.value.display()


def test_card_detail_signs_query_and_sends_game_role_header(monkeypatch):
    monkeypatch.setattr(skland, "_now_ts_fresh", lambda: 1700000123)
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, headers=kwargs.get("headers") or {})
        return _card_payload()

    monkeypatch.setattr(skland, "_request_json", fake_request)
    card = skland.fetch_card_detail(
        {"cred": "cred-value", "token": "sign-token"},
        Role("伊冯", role_id="10448293", server_id="1"),
        device="dev123456",
    )
    assert card.nickname == "伊冯"
    assert captured["method"] == "GET"
    assert captured["url"].endswith(
        "/api/v1/game/endfield/card/detail?roleId=10448293&serverId=1"
    )
    headers = captured["headers"]
    assert headers["sk-game-role"] == "3_10448293_1"
    assert headers["cred"] == "cred-value"
    assert "token" not in headers
    # 独立复算签名：path + query + timestamp + json(headers)
    timestamp = "1700000121"
    signature_headers = {
        "platform": "1",
        "timestamp": timestamp,
        "dId": "dev123456",
        "vName": "1.21.0",
    }
    text = (
        "/api/v1/game/endfield/card/detail"
        + "roleId=10448293&serverId=1"
        + timestamp
        + json.dumps(signature_headers, separators=(",", ":"))
    )
    expected = hashlib.md5(
        hmac.new(b"sign-token", text.encode("utf-8"), hashlib.sha256)
        .hexdigest()
        .encode()
    ).hexdigest()
    assert headers["sign"] == expected


def test_card_detail_http_401_is_credential_expired(monkeypatch):
    class _Response:
        status_code = 401
        headers: dict[str, str] = {}

        def json(self):
            return {}

    monkeypatch.setattr(skland.net, "request", lambda *_a, **_k: _Response())
    with pytest.raises(skland.CredentialExpiredError):
        skland.fetch_card_detail(
            {"cred": "c", "token": "t"},
            Role("伊冯", role_id="1", server_id="2"),
        )


def test_binding_parse_reads_channel_master_id_as_server_id():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "appCode": "endfield",
                    "appName": "明日方舟：终末地",
                    "bindingList": [
                        {
                            "uid": "10448293",
                            "nickName": "伊冯",
                            "channelMasterId": "1",
                            "channelName": "官服",
                            "level": 60,
                        },
                        {
                            "uid": "10448001",
                            "nickName": "佩丽卡",
                            "channelMasterId": "2",
                            "channelName": "官服",
                            "level": 50,
                        },
                    ],
                }
            ]
        },
    }
    profile = skland.parse_bindings(payload)
    assert len(profile.roles) == 2
    assert profile.roles[0].role_id == "10448293"
    assert profile.roles[0].server_id == "1"
    assert profile.roles[1].server_id == "2"
    assert profile.roles[0].api_ready is True


def test_binding_parse_default_role_structure_is_deduped():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "appCode": "endfield",
                    "bindingList": [
                        {
                            "defaultRole": {
                                "roleId": "10448293",
                                "serverId": "1",
                                "nickname": "伊冯",
                                "serverName": "官服",
                                "level": 60,
                            },
                            "roles": [
                                {
                                    "roleId": "10448293",
                                    "serverId": "1",
                                    "nickname": "伊冯",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    }
    profile = skland.parse_bindings(payload)
    assert len(profile.roles) == 1
    assert profile.roles[0].role_id == "10448293"
    assert profile.roles[0].server_id == "1"


def test_fetch_player_data_degrades_when_card_detail_fails(monkeypatch):
    role = Role("伊冯", 60, "官服", role_id="10448293", server_id="1")

    def fake_profile(credential, *, device=None):
        return PlayerProfile(
            provider=PROVIDER_SKLAND,
            nickname="伊冯",
            uid="10448293",
            roles=[role],
        )

    monkeypatch.setattr(skland, "fetch_profile", fake_profile)

    def card_fails(*_args, **_kwargs):
        raise AccountError("接口暂不可用")

    monkeypatch.setattr(skland, "fetch_card_detail", card_fails)
    profile = skland._fetch_player_data({"cred": "c", "token": "t"}, 0, "dev123456")
    assert profile.roles and profile.card is None
    assert "接口暂不可用" in (profile.data_error or "")


def test_fetch_player_data_retries_after_expired(monkeypatch):
    role = Role("伊冯", 60, "官服", role_id="1", server_id="2")
    calls = {"n": 0}

    def fake_fetch(credential, role_index, device):
        calls["n"] += 1
        if calls["n"] == 1:
            raise skland.CredentialExpiredError("登录已失效")
        return PlayerProfile(provider=PROVIDER_SKLAND, roles=[role])

    monkeypatch.setattr(skland, "_fetch_player_data", fake_fetch)
    monkeypatch.setattr(
        skland, "refresh_credential", lambda credential, *, device=None: {**credential, "token": "new"}
    )
    profile, refreshed = skland.fetch_player_data_with_refresh(
        {"cred": "c", "token": "old"}
    )
    assert calls["n"] == 2
    assert refreshed["token"] == "new"
    assert profile.roles


# -- 服务：真实数据刷新 / 多角色 / 签到 --------------------------------------
def _sync_run_async(monkeypatch):
    """把 run_async 变成同步直调（account 服务测试不依赖 Qt 线程池）。"""

    def fake_run(fn, *args, on_done=None, on_error=None, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(exc)
            return None
        if on_done:
            on_done(result)
        return None

    monkeypatch.setattr(service_mod, "run_async", fake_run)


def test_service_refresh_publishes_player_and_operators(isolated_store, ctx, monkeypatch):
    _sync_run_async(monkeypatch)
    credential = {"cred": "c", "token": "t", "userId": "1", "oauthToken": "o"}
    skland.save_credential(credential)
    card = skland.parse_card_payload(_card_payload())
    profile = PlayerProfile(
        provider=PROVIDER_SKLAND,
        nickname=card.nickname,
        uid=card.role_id,
        roles=[
            Role(
                "伊冯", 60, "官服", "近卫", "寒冷", role_id="10448293", server_id="1"
            )
        ],
        card=card,
    )
    monkeypatch.setattr(
        skland,
        "fetch_player_data_with_refresh",
        lambda cred, *, role_index=0, device=None: (profile, cred),
    )
    service = AccountService(ctx)
    events: list[dict] = []
    ctx.bus.subscribe("account.updated", lambda snapshot=None, **_: events.append(snapshot))
    done: list[dict] = []
    service.refresh(on_done=done.append)
    state = service.snapshot()
    # 旧契约字段不变
    assert state["online"] is True and state["nickname"] == "伊冯"
    assert state["roles"][0]["name"] == "伊冯"
    # 新数据
    assert state["player"] and state["player"]["uid_masked"] == "10••••93"
    assert state["player"]["stamina"]["cur"] == 92
    assert len(state["operators"]) == 2
    assert state["operators"][0]["rarity"] == 6
    assert state["data_error"] is None
    assert done and events and events[-1]["player"]["level"] == 60
    service.stop()


def test_service_reuses_cached_card_when_detail_temporarily_fails(isolated_store, ctx, monkeypatch):
    _sync_run_async(monkeypatch)
    credential = {"cred": "c", "token": "t", "userId": "1", "oauthToken": "o"}
    skland.save_credential(credential)
    card = skland.parse_card_payload(_card_payload())
    role = Role("伊冯", 60, "官服", role_id="10448293", server_id="1")
    ok_profile = PlayerProfile(
        provider=PROVIDER_SKLAND, nickname="伊冯", uid="10448293", roles=[role], card=card
    )
    calls = {"n": 0}

    def fake_fetch(cred, *, role_index=0, device=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return ok_profile, cred
        return (
            PlayerProfile(
                provider=PROVIDER_SKLAND,
                nickname="伊冯",
                uid="10448293",
                roles=[role],
                data_error="临时接口失败",
            ),
            cred,
        )

    monkeypatch.setattr(skland, "fetch_player_data_with_refresh", fake_fetch)
    service = AccountService(ctx)
    service.refresh()
    assert service.snapshot()["player"] is not None
    service.refresh()
    state = service.snapshot()
    # 缓存降级：仍显示上次完整数据，但明确标注可能不是最新
    assert state["player"] is not None
    assert state["operators"]
    assert "缓存" in (state["data_error"] or "")
    service.stop()


def test_service_select_role_persists_and_refetches(isolated_store, ctx, monkeypatch):
    _sync_run_async(monkeypatch)
    skland.save_credential({"cred": "c", "token": "t", "userId": "1", "oauthToken": "o"})
    seen: list[int] = []

    def fake_fetch(cred, *, role_index=0, device=None):
        seen.append(int(role_index))
        return (
            PlayerProfile(
                provider=PROVIDER_SKLAND,
                nickname="第二角色",
                uid="2",
                roles=[Role("甲", role_id="1", server_id="1"), Role("乙", role_id="2", server_id="1")],
                role_index=int(role_index),
            ),
            cred,
        )

    monkeypatch.setattr(skland, "fetch_player_data_with_refresh", fake_fetch)
    service = AccountService(ctx)
    service.refresh()
    assert service.snapshot()["role_index"] == 0
    service.select_role(1)
    assert seen == [0, 1]
    assert service.snapshot()["role_index"] == 1
    assert ctx.settings.get("account.role_index") == 1
    service.stop()


def test_service_sign_attendance_reports_awards(isolated_store, ctx, monkeypatch):
    _sync_run_async(monkeypatch)
    credential = {"cred": "c", "token": "t", "userId": "1", "oauthToken": "o"}
    skland.save_credential(credential)
    monkeypatch.setattr(
        skland,
        "sign_attendance_for",
        lambda cred, *, role_index=0, device=None: (
            {"awards": [{"type": "daily"}, {"type": "first"}]},
            cred,
        ),
    )
    service = AccountService(ctx)
    results: list[dict] = []
    errors: list[Exception] = []
    service.skland_sign_attendance(on_done=results.append, on_error=errors.append)
    assert not errors
    assert results and results[0]["awards"] == 2
    assert service.is_busy() is False
    service.stop()


def test_service_sign_attendance_without_credential_is_human_error(isolated_store, ctx, monkeypatch):
    service = AccountService(ctx)
    errors: list[Exception] = []
    service.skland_sign_attendance(on_done=lambda _r: None, on_error=errors.append)
    assert errors and "签" in str(errors[0])
    service.stop()


def test_demo_state_contains_full_player_data():
    state = demo_state(now=1700000000)
    assert state["demo"] is True
    assert state["player"] is not None
    assert state["player"]["stamina"]["cur"] == 92
    assert state["player"]["domains"] and state["player"]["bp"]["max"] == 60
    assert state["player"]["uid_masked"] == mask_uid("10448293")
    assert len(state["operators"]) == 4
    assert state["operators"][0]["rarity"] == 6


# -- 视图：旧 state 兼容 / 演示数据渲染 ---------------------------------------
def test_view_opens_when_service_missing(ctx, qapp, monkeypatch):
    ctx.services.account = None
    view = _make_account_view(ctx, monkeypatch)
    assert view._missing_flag.isHidden() is False
    assert view._online_host.isHidden() is True
    view.on_hide()
    view.deleteLater()


def test_view_bili_online_keeps_scan_layout_for_skland(isolated_store, ctx, qapp, monkeypatch):
    service = AccountService(ctx)
    ctx.services.account = service
    view = _make_account_view(ctx, monkeypatch)
    bili_state = blank_state(online=True, provider="bilibili", nickname="B站用户")
    view._apply_service_state(service, bili_state)
    # B 服身份登录后仍显示扫码布局，用户可以继续扫森空岛拿终末地数据
    assert view._online_host.isHidden() is True
    assert view._offline_host.isHidden() is False
    assert view._profile_empty.isHidden() is False
    view.on_hide()
    view.deleteLater()


def test_view_renders_partial_state_when_card_detail_failed(isolated_store, ctx, qapp, monkeypatch):
    service = AccountService(ctx)
    ctx.services.account = service
    view = _make_account_view(ctx, monkeypatch)
    partial = blank_state(
        online=True,
        provider=PROVIDER_SKLAND,
        nickname="绑定昵称",
        uid_masked="10••••93",
        last_sync=1700000000,
        data_error="读取终末地完整数据失败（code 500）：内部错误",
    )
    partial["roles"] = [
        {"name": "伊冯", "level": 60, "server": "官服", "class": None, "element": None}
    ]
    view._apply_service_state(service, partial)
    # 完整数据缺失时：降级显示绑定角色列表 + 人话说明，不崩、不编造
    assert view._player_note.isHidden() is False
    assert "500" in view._player_note.text()
    assert view._binding_fallback.isHidden() is False
    assert view._binding_rows.count() == 1
    assert view._operator_note.isHidden() is False
    view.on_hide()
    view.deleteLater()


def test_view_accepts_legacy_state_without_new_fields(isolated_store, ctx, qapp, monkeypatch):
    service = AccountService(ctx)
    ctx.services.account = service
    view = _make_account_view(ctx, monkeypatch)
    legacy = {
        key: blank_state()[key]
        for key in (
            "online",
            "provider",
            "nickname",
            "uid_masked",
            "avatar_path",
            "roles",
            "last_sync",
            "demo",
            "error",
        )
    }
    legacy.update(online=True, provider=PROVIDER_SKLAND, nickname="旧状态", roles=[])
    view._apply_service_state(service, legacy)  # 不抛异常即兼容
    assert view._state is not None
    assert view._role_selector.isHidden() is True
    view.on_hide()
    view.deleteLater()


def test_view_demo_renders_operator_list_and_filter(isolated_store, ctx, qapp, monkeypatch):
    service = AccountService(ctx)
    ctx.services.account = service
    service.set_demo_mode(True)
    view = _make_account_view(ctx, monkeypatch)
    assert view._operator_rows.count() == 4
    view._operator_filter.set_index(1, emit=True)  # 只看 6★
    assert view._operator_rows.count() == 2
    view._operator_filter.set_index(0, emit=True)
    view._operator_sort.set_index(1, emit=True)  # 按稀有度排
    assert view._operator_rows.count() == 4
    view.on_hide()
    view.deleteLater()


def test_view_role_selector_visible_for_multiple_roles(isolated_store, ctx, qapp, monkeypatch):
    service = AccountService(ctx)
    ctx.services.account = service
    service.set_demo_mode(True)
    view = _make_account_view(ctx, monkeypatch)
    state = service.snapshot()
    assert len(state["roles"]) > 1
    assert not view._role_selector.isHidden()
    view.on_hide()
    view.deleteLater()


def test_view_role_selector_hidden_for_single_role(isolated_store, ctx, qapp, monkeypatch):
    service = AccountService(ctx)
    ctx.services.account = service
    view = _make_account_view(ctx, monkeypatch)
    legacy = blank_state(online=True, provider=PROVIDER_SKLAND, nickname="单角色")
    legacy["roles"] = [{"name": "伊冯", "level": 60, "server": "官服", "class": None, "element": None}]
    view._apply_service_state(service, legacy)
    assert view._role_selector.isHidden() is True
    view.on_hide()
    view.deleteLater()


