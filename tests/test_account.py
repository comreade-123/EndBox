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
    assert "1001" in info.value.display() and "重试" in info.value.display()


def test_scan_create_network_failure_maps_to_account_error(monkeypatch):
    def offline(*_args, **_kwargs):
        raise NetworkError("网络请求失败：timed out")

    monkeypatch.setattr(skland.net, "request", offline)
    with pytest.raises(AccountError) as info:
        skland.create_scan_login()
    text = info.value.display()
    assert "网络" in text and "重试" in text


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
    assert "扫码状态异常" in info.value.display()


# -- 森空岛：token / grant / cred -------------------------------------------
def test_parse_scan_token_success_and_expired():
    assert skland.parse_scan_token({"status": 0, "data": {"token": "oauth-token"}}) == "oauth-token"
    with pytest.raises(AccountError) as info:
        skland.parse_scan_token({"status": 1, "msg": "扫码凭证已失效"})
    assert "失效" in info.value.display()


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
    assert "cred" in info.value.display()


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

