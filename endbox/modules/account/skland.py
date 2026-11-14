# -*- coding: utf-8 -*-
"""森空岛（官服）扫码登录与接口签名。

流程（社区整理的第三方接口，非官方开放 API）：

1. ``POST https://as.hypergryph.com/general/v1/gen_scan/login``
   → ``data.scanId`` / ``data.scanUrl``（``hypergryph://scan_login?scanId=...``）
2. ``GET  https://as.hypergryph.com/general/v1/scan_status?scanId=...``
   → ``status == 0`` 表示已确认并返回 ``data.scanCode``；否则按 ``msg`` 区分
     未扫码 / 已扫码待确认 / 已过期 / 已取消。
3. ``POST https://as.hypergryph.com/user/auth/v1/token_by_scan_code``
   → ``data.token``（鹰角 OAuth Token）。
4. ``POST https://as.hypergryph.com/user/oauth2/v2/grant``
   → ``data.code``（appCode = 森空岛 ``4ca99fa6b56cc2ba``）。
5. ``POST https://zonai.skland.com/web/v1/user/auth/generate_cred_by_code``
   → ``data.cred`` / ``data.token``（签名 Token）/ ``data.userId``。
6. ``GET  https://zonai.skland.com/api/v1/game/player/binding``（签名头）
   → 绑定角色列表。

签名算法（社区实现）：``sign = md5(hmac_sha256(sign_token, path + query + timestamp
+ json({platform,timestamp,dId,vName})).hexdigest()).hexdigest()``，timestamp 为服务器
时间 - 2 秒；``dId`` 为本地生成的稳定设备 ID（本工具不做官方设备验证，仅需稳定标识）。

所有凭据只经 ``endbox.core.secure_store``（DPAPI）落盘；失败一律转 ``AccountError``
（人话 + 重试建议），不吞异常。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib.parse import urlencode

from endbox.core import net
from endbox.core.log import get_logger
from endbox.core.net import NetworkError
from endbox.core.secure_store import secure_store
from endbox.core.text import t
from endbox.modules.account.models import (
    PROVIDER_SKLAND,
    AccountError,
    Operator,
    PlayerCard,
    PlayerProfile,
    Role,
    none_or_str,
    to_int,
)

log = get_logger("account.skland")

# -- 端点 -------------------------------------------------------------------
SCAN_CREATE_URL = "https://as.hypergryph.com/general/v1/gen_scan/login"
SCAN_STATUS_URL = "https://as.hypergryph.com/general/v1/scan_status"
SCAN_TOKEN_URL = "https://as.hypergryph.com/user/auth/v1/token_by_scan_code"
GRANT_URL = "https://as.hypergryph.com/user/oauth2/v2/grant"
CRED_URL = "https://zonai.skland.com/web/v1/user/auth/generate_cred_by_code"
REFRESH_URL = "https://zonai.skland.com/web/v1/auth/refresh"
BINDING_URL = "https://zonai.skland.com/api/v1/game/player/binding"
# 2026-11 新增（国服实测存在：GET 无凭据返回 401 code=10002 用户未登录）
# enums 接口同样存在，但本版直接用 profession.value / property.value（已是中文显示文本），
# 不额外拉取枚举，避免多一次未验证请求。
CARD_DETAIL_URL = "https://zonai.skland.com/api/v1/game/endfield/card/detail"
ENUMS_URL = "https://zonai.skland.com/web/v1/game/endfield/enums"
ATTENDANCE_URL = "https://zonai.skland.com/web/v1/game/endfield/attendance"

# 森空岛 App 的 appCode（不是终末地游戏的 appCode）
SKLAND_APP_CODE = "4ca99fa6b56cc2ba"
BINDING_PATH = "/api/v1/game/player/binding"
REFRESH_PATH = "/web/v1/auth/refresh"
CARD_DETAIL_PATH = "/api/v1/game/endfield/card/detail"
ENUMS_PATH = "/web/v1/game/endfield/enums"
ATTENDANCE_PATH = "/web/v1/game/endfield/attendance"

# 终末地角色上下文请求头："3_{roleId}_{serverId}"（国际服文档；国服也带上更稳妥）
GAME_ROLE_HEADER = "sk-game-role"
GAME_ROLE_PREFIX = "3_"

# 签名接口返回「未登录 / 凭据失效」的已知标记（国服 401 code=10002 用户未登录）
_AUTH_MARKERS = ("登录已过期", "登录失效", "凭据已失效", "未登录", "cred")

HYPERGRYPH_UA = (
    "Mozilla/5.0 (Linux; Android 12; SM-A5560 Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/101.0.4951.61 Safari/537.36; SKLand/1.52.1"
)
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
SKLAND_APP_UA = (
    "Skland/1.21.0 (com.hypergryph.skland; build:102100065; iOS 17.6.0; ) "
    "Alamofire/5.7.1"
)

# secure_store 键
CREDENTIAL_KEY = "skland.credential"
DEVICE_KEY = "skland.device_id"

# 扫码状态（与界面文案解耦）
SCAN_WAITING = "waiting"
SCAN_SCANNED = "scanned"
SCAN_CONFIRMED = "confirmed"
SCAN_EXPIRED = "expired"
SCAN_CANCELED = "canceled"
SCAN_FAILED = "failed"

# 二维码本地倒计时（服务端可能提前判过期，以服务端状态为准）
SCAN_TTL_SECONDS = 180

# 时间同步：签名对时间敏感，优先用服务器 Date 头纠正本机偏差
_SERVER_CLOCK_TTL = 300.0
_clock_offset = 0.0
_clock_sampled = 0.0


class CredentialExpiredError(AccountError):
    """当前 cred/token 已失效，可以尝试刷新或重新扫码。"""


@dataclass
class ScanQr:
    scan_id: str
    scan_url: str


@dataclass
class ScanStatus:
    status: str
    message: str = ""
    scan_code: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in (SCAN_CONFIRMED, SCAN_EXPIRED, SCAN_CANCELED, SCAN_FAILED)


@dataclass
class ScanLoginResult:
    profile: PlayerProfile
    credential: dict[str, str]


# -- 设备 ID / 时间 ---------------------------------------------------------
def device_id() -> str:
    """稳定的匿名设备 ID（dId 请求头用），首次生成后加密保存。"""
    value = secure_store.get(DEVICE_KEY)
    if value and len(value) >= 8:
        return value
    value = uuid.uuid4().hex
    try:
        secure_store.set(DEVICE_KEY, value)
    except Exception:  # noqa: BLE001 - 存储不可用时仍允许本次登录
        log.warning("device id persist failed")
    return value


def _absorb_server_time(response) -> None:
    """从响应的 Date 头估计服务器时间偏差，供签名 timestamp 使用。"""
    global _clock_offset, _clock_sampled
    try:
        raw = response.headers.get("Date", "")
    except Exception:  # noqa: BLE001
        return
    if not raw:
        return
    try:
        server = parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError, OverflowError):
        return
    _clock_offset = server - time.time()
    _clock_sampled = time.monotonic()


def _now_ts_fresh() -> int:
    global _clock_offset
    if time.monotonic() - _clock_sampled > _SERVER_CLOCK_TTL:
        _clock_offset = 0.0
    return int(time.time() + _clock_offset)


# -- HTTP 基础 --------------------------------------------------------------
def _request_json(
    method: str,
    url: str,
    *,
    stage: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    update_clock: bool = False,
) -> dict[str, Any]:
    try:
        response = net.request(
            method,
            url,
            headers=headers,
            params=params,
            json_body=json_body,
            retries=1,
        )
    except NetworkError as exc:
        raise AccountError(
            t("account.error.skland.net_connect", stage=stage, error=exc),
            hint=t("account.error.skland.net_connect_hint"),
        ) from exc
    if update_clock:
        _absorb_server_time(response)
    if response.status_code >= 400:
        raise AccountError(
            t(
                "account.error.skland.http",
                status=response.status_code,
                stage=stage,
            ),
            hint=t("account.error.skland.http_hint"),
            status=response.status_code,
        )
    try:
        payload = response.json()
    except (ValueError, UnicodeError) as exc:
        raise AccountError(
            t("account.error.skland.not_json", stage=stage),
            hint=t("account.error.skland.not_json_hint"),
        ) from exc
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.unrecognized", stage=stage),
            hint=t("account.error.skland.unrecognized_hint"),
        )
    return payload


def _status_code(payload: dict[str, Any]) -> Any:
    if "status" in payload:
        return payload.get("status")
    return payload.get("code")


def _message(payload: dict[str, Any]) -> str:
    return (
        none_or_str(payload.get("msg"))
        or none_or_str(payload.get("message"))
        or ""
    )


def _hypergryph_headers(device: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": HYPERGRYPH_UA,
        "dId": device,
        "x-requested-with": "com.hypergryph.skland",
    }


def _web_credential_headers(device: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "user-agent": WEB_UA,
        "referer": "https://www.skland.com/",
        "origin": "https://www.skland.com",
        "dId": device,
        "platform": "3",
        "timestamp": str(_now_ts_fresh()),
        "vName": "1.0.0",
    }


def _credential_headers(cred: str, sign_token: str) -> dict[str, str]:
    return {
        "cred": cred,
        "User-Agent": SKLAND_APP_UA,
        "Accept-Encoding": "gzip",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "token": sign_token,
    }


def build_signed_headers(
    sign_token: str,
    *,
    path: str,
    query: str,
    device: str,
    headers: dict[str, str],
    platform: str = "1",
    version: str = "1.21.0",
    body: str = "",
) -> dict[str, str]:
    """按社区 V2 算法生成森空岛签名头（不发送 sign token 本身）。

    GET 带 query 时 query string 参与签名；POST 时用 ``body``（JSON 字符串）参与签名，
    签名文本 = path + (body or query) + timestamp + json(headers)。
    """
    timestamp = str(_now_ts_fresh() - 2)
    signature_headers = {
        "platform": platform,
        "timestamp": timestamp,
        "dId": device,
        "vName": version,
    }
    signature_text = (
        path
        + (body if body else query)
        + timestamp
        + json.dumps(signature_headers, separators=(",", ":"))
    )
    digest = hashlib.md5(
        hmac.new(
            str(sign_token).encode("utf-8"),
            signature_text.encode("utf-8"),
            hashlib.sha256,
        )
        .hexdigest()
        .encode("utf-8")
    ).hexdigest()
    result = dict(headers)
    result.update(signature_headers)
    result["sign"] = digest
    result.pop("token", None)
    return result


def _signed_headers(
    credential: dict[str, str],
    *,
    path: str,
    query: str = "",
    device: str,
    extra_headers: dict[str, str] | None = None,
    body: str = "",
) -> dict[str, str]:
    headers = _credential_headers(credential["cred"], credential["token"])
    if extra_headers:
        headers.update(extra_headers)
    return build_signed_headers(
        credential["token"],
        path=path,
        query=query,
        device=device,
        headers=headers,
        body=body,
    )


def _query_string(params: dict[str, Any] | None) -> str:
    """稳定顺序的 query string（签名与实际请求必须逐字节一致）。"""
    pairs = [
        (str(key), str(value))
        for key, value in (params or {}).items()
        if value is not None and value != ""
    ]
    return urlencode(pairs)


# -- 扫码创建 / 轮询 / 换 token ---------------------------------------------
def create_scan_login() -> ScanQr:
    payload = _request_json(
        "POST",
        SCAN_CREATE_URL,
        stage=t("account.error.stage.create_qr"),
        headers=_hypergryph_headers(device_id()),
        json_body={},
    )
    return parse_scan_created(payload)


def parse_scan_created(payload: Any) -> ScanQr:
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.gen_format"),
            hint=t("account.error.skland.gen_format_hint"),
        )
    code = _status_code(payload)
    if code is None or str(code) != "0":
        message = _message(payload) or t("account.error.upstream_rejected")
        raise AccountError(
            t("account.error.skland.gen_failed", code=code, message=message),
            hint=t("account.error.skland.gen_failed_hint"),
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise AccountError(
            t("account.error.skland.gen_no_data"),
            hint=t("account.error.skland.gen_no_data_hint"),
        )
    scan_id = none_or_str(data.get("scanId"))
    scan_url = none_or_str(data.get("scanUrl"))
    if not scan_id or not scan_url:
        raise AccountError(
            t("account.error.skland.gen_no_url"),
            hint=t("account.error.skland.gen_no_url_hint"),
        )
    return ScanQr(scan_id=scan_id, scan_url=scan_url)


def check_scan_status(scan_id: str) -> ScanStatus:
    key = str(scan_id or "").strip()
    if not key:
        raise AccountError(t("account.error.skland.qr_invalid"))
    payload = _request_json(
        "GET",
        SCAN_STATUS_URL,
        stage=t("account.error.stage.scan_status"),
        params={"scanId": key},
    )
    return parse_scan_status(payload)


def parse_scan_status(payload: Any) -> ScanStatus:
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.status_format"),
            hint=t("account.error.skland.status_format_hint"),
        )
    code = _status_code(payload)
    message = _message(payload)
    try:
        numeric = int(code) if code is not None else None
    except (TypeError, ValueError):
        numeric = None

    if numeric == 0:
        data = payload.get("data")
        scan_code = none_or_str(data.get("scanCode")) if isinstance(data, dict) else None
        if not scan_code:
            raise AccountError(
                t("account.error.skland.confirmed_no_code"),
                hint=t("account.error.skland.confirmed_no_code_hint"),
            )
        return ScanStatus(
            status=SCAN_CONFIRMED,
            message=t("account.error.skland.scan_confirmed"),
            scan_code=scan_code,
        )

    if "未扫码" in message or "未扫描" in message or numeric == 100:
        return ScanStatus(
            status=SCAN_WAITING,
            message=message or t("account.error.skland.scan_waiting"),
        )
    if "待确认" in message or "已扫码" in message:
        return ScanStatus(
            status=SCAN_SCANNED,
            message=message or t("account.error.skland.scan_scanned"),
        )
    if "失效" in message or "过期" in message or "超时" in message:
        return ScanStatus(
            status=SCAN_EXPIRED,
            message=message or t("account.error.skland.scan_expired"),
        )
    if "取消" in message or "拒绝" in message:
        return ScanStatus(
            status=SCAN_CANCELED,
            message=message or t("account.error.skland.scan_canceled"),
        )
    raise AccountError(
        t(
            "account.error.skland.status_unknown",
            message=message or f"status {code}",
        ),
        hint=t("account.error.skland.status_unknown_hint"),
    )


def _scan_token(scan_code: str, device: str) -> str:
    payload = _request_json(
        "POST",
        SCAN_TOKEN_URL,
        stage=t("account.error.stage.scan_token"),
        headers=_hypergryph_headers(device),
        json_body={"scanCode": scan_code},
    )
    return parse_scan_token(payload)


def parse_scan_token(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.token_format"),
            hint=t("account.error.skland.token_format_hint"),
        )
    code = _status_code(payload)
    if code is None or str(code) != "0":
        message = _message(payload) or t("account.error.upstream_rejected")
        if "失效" in message or "过期" in message:
            raise AccountError(
                t("account.error.skland.token_expired"),
                hint=t("account.error.skland.token_expired_hint"),
            )
        raise AccountError(
            t("account.error.skland.token_failed", code=code, message=message),
            hint=t("account.error.skland.token_failed_hint"),
        )
    data = payload.get("data")
    token = none_or_str(data.get("token")) if isinstance(data, dict) else None
    if not token:
        raise AccountError(
            t("account.error.skland.token_missing"),
            hint=t("account.error.skland.token_missing_hint"),
        )
    return token


def _grant_code(oauth_token: str, device: str) -> str:
    payload = _request_json(
        "POST",
        GRANT_URL,
        stage=t("account.error.stage.grant"),
        headers=_hypergryph_headers(device),
        json_body={
            "appCode": SKLAND_APP_CODE,
            "token": oauth_token,
            "type": 0,
        },
    )
    return parse_grant_code(payload)


def parse_grant_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.grant_format"),
            hint=t("account.error.skland.grant_format_hint"),
        )
    code = _status_code(payload)
    if code is None or str(code) != "0":
        message = _message(payload) or t("account.error.upstream_rejected")
        raise AccountError(
            t("account.error.skland.grant_failed", code=code, message=message),
            hint=t("account.error.skland.grant_failed_hint"),
        )
    data = payload.get("data")
    grant = none_or_str(data.get("code")) if isinstance(data, dict) else None
    if not grant:
        raise AccountError(
            t("account.error.skland.grant_missing"),
            hint=t("account.error.skland.grant_missing_hint"),
        )
    return grant


def _fetch_credential(grant_code: str, oauth_token: str, device: str) -> dict[str, str]:
    payload = _request_json(
        "POST",
        CRED_URL,
        stage=t("account.error.stage.cred"),
        headers=_web_credential_headers(device),
        json_body={"code": grant_code, "kind": 1},
        update_clock=True,
    )
    return parse_credential(payload, oauth_token=oauth_token)


def parse_credential(payload: Any, *, oauth_token: str = "") -> dict[str, str]:
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.cred_format"),
            hint=t("account.error.skland.cred_format_hint"),
        )
    code = _status_code(payload)
    if code is None or str(code) != "0":
        message = _message(payload) or t("account.error.upstream_rejected")
        raise AccountError(
            t("account.error.skland.cred_failed", code=code, message=message),
            hint=t("account.error.skland.cred_failed_hint"),
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise AccountError(
            t("account.error.skland.cred_no_data"),
            hint=t("account.error.skland.cred_no_data_hint"),
        )
    cred = none_or_str(data.get("cred"))
    sign_token = none_or_str(data.get("token"))
    if not cred or not sign_token:
        raise AccountError(
            t("account.error.skland.cred_incomplete"),
            hint=t("account.error.skland.cred_incomplete_hint"),
        )
    return {
        "cred": cred,
        "token": sign_token,
        "userId": none_or_str(data.get("userId")) or "",
        "oauthToken": str(oauth_token or ""),
    }


def complete_scan_login(scan_code: str) -> ScanLoginResult:
    """扫码确认后：scanCode → OAuth Token → 授权码 → cred → 绑定列表。"""
    code = str(scan_code or "").strip()
    if not code:
        raise AccountError(t("account.error.skland.empty_scan_code"))
    device = device_id()
    oauth_token = _scan_token(code, device)
    grant = _grant_code(oauth_token, device)
    credential = _fetch_credential(grant, oauth_token, device)
    profile = fetch_profile(credential, device=device)
    return ScanLoginResult(profile=profile, credential=credential)


# -- 凭据读写 ---------------------------------------------------------------
def save_credential(credential: dict[str, Any]) -> None:
    data = {
        "cred": str(credential.get("cred") or ""),
        "token": str(credential.get("token") or ""),
        "userId": str(credential.get("userId") or ""),
        "oauthToken": str(credential.get("oauthToken") or ""),
    }
    secure_store.set(CREDENTIAL_KEY, json.dumps(data, ensure_ascii=False))


def load_credential() -> dict[str, str] | None:
    raw = secure_store.get(CREDENTIAL_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        log.warning("skland credential payload unreadable")
        return None
    if not isinstance(data, dict):
        return None
    cred = none_or_str(data.get("cred"))
    token = none_or_str(data.get("token"))
    if not cred or not token:
        return None
    return {
        "cred": cred,
        "token": token,
        "userId": none_or_str(data.get("userId")) or "",
        "oauthToken": none_or_str(data.get("oauthToken")) or "",
    }


def clear_credential() -> None:
    secure_store.delete(CREDENTIAL_KEY)


def has_credential() -> bool:
    return load_credential() is not None


# -- 绑定列表 / 玩家资料 -----------------------------------------------------
def fetch_binding_payload(credential: dict[str, str], *, device: str | None = None) -> dict[str, Any]:
    dev = device or device_id()
    headers = build_signed_headers(
        credential["token"],
        path=BINDING_PATH,
        query="",
        device=dev,
        headers=_credential_headers(credential["cred"], credential["token"]),
    )
    try:
        payload = _request_json(
            "GET",
            BINDING_URL,
            stage=t("account.error.stage.binding"),
            headers=headers,
        )
    except AccountError as exc:
        if getattr(exc, "status", None) == 401:
            raise CredentialExpiredError(
                t("account.error.skland.login_expired_401"),
                hint=t("account.error.skland.login_expired_hint"),
            ) from exc
        raise
    code = _status_code(payload)
    if code is not None and str(code) not in ("0", "200"):
        message = _message(payload) or t("account.error.upstream_rejected")
        if code == 401 or any(
            marker in message
            for marker in ("登录已过期", "登录失效", "凭据已失效", "未登录", "cred")
        ):
            raise CredentialExpiredError(
                t("account.error.skland.login_expired", message=message),
                hint=t("account.error.skland.login_expired_hint"),
            )
        raise AccountError(
            t("account.error.skland.binding_failed", code=code, message=message),
            hint=t("account.error.skland.binding_failed_hint"),
        )
    return payload


def fetch_profile(credential: dict[str, str], *, device: str | None = None) -> PlayerProfile:
    payload = fetch_binding_payload(credential, device=device)
    return parse_bindings(payload)


def refresh_credential(
    credential: dict[str, str],
    *,
    device: str | None = None,
) -> dict[str, str]:
    """用当前 cred + 签名 token 刷新签名 token。"""
    dev = device or device_id()
    headers = build_signed_headers(
        credential["token"],
        path=REFRESH_PATH,
        query="",
        device=dev,
        headers=_credential_headers(credential["cred"], credential["token"]),
    )
    try:
        payload = _request_json(
            "GET",
            REFRESH_URL,
            stage=t("account.error.stage.refresh"),
            headers=headers,
            update_clock=True,
        )
    except AccountError as exc:
        if getattr(exc, "status", None) == 401:
            raise CredentialExpiredError(
                t("account.error.skland.refresh_401"),
                hint=t("account.error.skland.refresh_failed_hint"),
            ) from exc
        raise
    code = _status_code(payload)
    if code is None or str(code) not in ("0", "200"):
        message = _message(payload) or t("account.error.upstream_rejected")
        raise CredentialExpiredError(
            t("account.error.skland.refresh_failed", message=message),
            hint=t("account.error.skland.refresh_failed_hint"),
        )
    data = payload.get("data")
    token = none_or_str(data.get("token")) if isinstance(data, dict) else None
    if not token:
        raise CredentialExpiredError(
            t("account.error.skland.refresh_no_token"),
            hint=t("account.error.skland.refresh_no_token_hint"),
        )
    refreshed = dict(credential)
    refreshed["token"] = token
    return refreshed


def fetch_profile_with_refresh(
    credential: dict[str, str],
    *,
    device: str | None = None,
) -> tuple[PlayerProfile, dict[str, str]]:
    """先直接读绑定列表；cred 过期时静默刷新一次再重试。"""
    dev = device or device_id()
    try:
        return fetch_profile(credential, device=dev), credential
    except CredentialExpiredError:
        refreshed = refresh_credential(credential, device=dev)
        return fetch_profile(refreshed, device=dev), refreshed


# -- card/detail：完整游戏数据 ------------------------------------------------
def pick_role(roles: list[Role], index: Any = 0) -> Role | None:
    """从绑定角色里按序号取一个（越界钳到合法范围）；没有角色返回 None。"""
    if not roles:
        return None
    idx = max(0, min(len(roles) - 1, to_int(index) or 0))
    return roles[idx]


def _game_role(role: Role) -> str:
    return f"{GAME_ROLE_PREFIX}{role.role_id}_{role.server_id}"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _nested_text(value: Any) -> str | None:
    """解析 ``{"value": "近卫", "key": ...}`` 这类枚举包装。"""
    if isinstance(value, dict):
        return (
            none_or_str(value.get("value"))
            or none_or_str(value.get("text"))
            or none_or_str(value.get("name"))
            or none_or_str(value.get("key"))
        )
    return none_or_str(value)


def _rarity(value: Any) -> tuple[str | None, int | None]:
    """解析 ``{"key": "rarity_6", "value": 6}`` / ``"rarity_6"`` / ``6``。"""
    key: str | None = None
    number: int | None = None
    if isinstance(value, dict):
        key = none_or_str(value.get("key")) or none_or_str(value.get("value"))
        number = to_int(value.get("value"))
    else:
        key = none_or_str(value)
    if number is None:
        digits = "".join(ch for ch in (key or "") if ch.isdigit())
        number = int(digits) if digits else None
    return key, number


def fetch_card_detail(
    credential: dict[str, str],
    role: Role,
    *,
    device: str | None = None,
) -> PlayerCard:
    """拉取 card/detail 完整玩家数据（带 sk-game-role 头与带 query 的 V2 签名）。"""
    role_id = none_or_str(role.role_id)
    server_id = none_or_str(role.server_id)
    if not role_id or not server_id:
        raise AccountError(
            t("account.error.skland.role_ids_missing"),
            hint=t("account.error.skland.role_ids_missing_hint"),
        )
    dev = device or device_id()
    query = _query_string({"roleId": role_id, "serverId": server_id})
    headers = _signed_headers(
        credential,
        path=CARD_DETAIL_PATH,
        query=query,
        device=dev,
        extra_headers={GAME_ROLE_HEADER: _game_role(role)},
    )
    url = f"{CARD_DETAIL_URL}?{query}"
    try:
        payload = _request_json(
            "GET",
            url,
            stage=t("account.error.stage.card_detail"),
            headers=headers,
        )
    except AccountError as exc:
        if getattr(exc, "status", None) == 401:
            raise CredentialExpiredError(
                t("account.error.skland.login_expired_401"),
                hint=t("account.error.skland.login_expired_hint"),
            ) from exc
        raise
    return parse_card_payload(payload)


def parse_card_payload(payload: Any) -> PlayerCard:
    """card/detail 响应 → PlayerCard；错误码转人话（不吞异常）。"""
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.card_format"),
            hint=t("account.error.skland.card_format_hint"),
        )
    code = _status_code(payload)
    if code is not None and str(code) not in ("0", "200"):
        message = _message(payload) or t("account.error.upstream_rejected")
        if str(code) in ("401", "10002") or any(marker in message for marker in _AUTH_MARKERS):
            raise CredentialExpiredError(
                t("account.error.skland.login_expired", message=message),
                hint=t("account.error.skland.login_expired_hint"),
            )
        raise AccountError(
            t("account.error.skland.card_failed", code=code, message=message),
            hint=t("account.error.skland.card_failed_hint"),
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise AccountError(
            t("account.error.skland.card_no_data"),
            hint=t("account.error.skland.card_no_data_hint"),
        )
    detail = data.get("detail")
    if not isinstance(detail, dict):
        detail = data
    return parse_card_detail(detail)


def parse_card_detail(detail: Any) -> PlayerCard:
    """解析 detail 节点（纯函数，结构见 _devdata/research/skport-readme.md）。"""
    if not isinstance(detail, dict):
        raise AccountError(
            t("account.error.skland.card_format"),
            hint=t("account.error.skland.card_format_hint"),
        )
    base = detail.get("base")
    if not isinstance(base, dict) or not base:
        raise AccountError(
            t("account.error.skland.card_no_base"),
            hint=t("account.error.skland.card_no_base_hint"),
        )
    main_mission = base.get("mainMission")
    if isinstance(main_mission, dict):
        mission_text = none_or_str(main_mission.get("description")) or none_or_str(
            main_mission.get("name")
        )
    else:
        mission_text = none_or_str(main_mission)

    dungeon = _dict(detail.get("dungeon"))
    bp_system = _dict(detail.get("bpSystem"))
    daily = _dict(detail.get("dailyMission"))
    achieve = _dict(detail.get("achieve"))
    spaceship = _dict(detail.get("spaceShip"))

    return PlayerCard(
        role_id=none_or_str(base.get("roleId")),
        server_id=none_or_str(base.get("serverId")),
        server_name=none_or_str(base.get("serverName")),
        nickname=none_or_str(base.get("name")) or none_or_str(base.get("nickname")),
        level=to_int(base.get("level")),
        world_level=to_int(base.get("worldLevel")),
        exp=to_int(base.get("exp")),
        avatar_url=none_or_str(base.get("avatarUrl")),
        char_num=to_int(base.get("charNum")),
        weapon_num=to_int(base.get("weaponNum")),
        doc_num=to_int(base.get("docNum")),
        create_time=to_int(base.get("createTime")),
        last_login_time=to_int(base.get("lastLoginTime")),
        save_time=to_int(base.get("saveTime")),
        main_mission=mission_text,
        cur_stamina=to_int(dungeon.get("curStamina")),
        max_stamina=to_int(dungeon.get("maxStamina")),
        stamina_full_ts=to_int(dungeon.get("maxTs")),
        bp_level=to_int(bp_system.get("curLevel")),
        bp_max_level=to_int(bp_system.get("maxLevel")),
        daily_activation=to_int(daily.get("dailyActivation")),
        daily_max_activation=to_int(daily.get("maxDailyActivation")),
        achieve_count=to_int(achieve.get("count")),
        domains=_parse_domains(detail.get("domain")),
        spaceship_rooms=_parse_rooms(spaceship.get("rooms")),
        operators=_parse_operators(detail.get("chars")),
    )


def _parse_domains(value: Any) -> list[dict[str, Any]]:
    domains: list[dict[str, Any]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        name = none_or_str(item.get("name")) or none_or_str(item.get("domainName"))
        if not name:
            continue
        settlements = _list(item.get("settlements"))
        levels = [
            to_int(_dict(settlement).get("level")) for settlement in settlements
        ]
        levels = [level for level in levels if level is not None]
        collections = item.get("collections")
        counts = {"puzzle": None, "chest": None, "piece": None, "blackbox": None}
        if isinstance(collections, dict):
            counts = _collection_counts(collections)
        elif isinstance(collections, list):
            merged: dict[str, Any] = {}
            for collection in collections:
                for key, raw in _dict(collection).items():
                    merged[key] = (to_int(merged.get(key)) or 0) + (to_int(raw) or 0)
            if merged:
                counts = _collection_counts(merged)
        domains.append(
            {
                "name": name,
                "level": to_int(item.get("level")),
                "settlements": len(settlements),
                "settlement_max_level": max(levels) if levels else None,
                **counts,
            }
        )
    return domains


def _collection_counts(data: dict[str, Any]) -> dict[str, int | None]:
    return {
        "puzzle": to_int(data.get("puzzleCount")),
        "chest": to_int(data.get("trchestCount")),
        "piece": to_int(data.get("pieceCount")),
        "blackbox": to_int(data.get("blackboxCount")),
    }


def _parse_rooms(value: Any) -> list[dict[str, Any]]:
    rooms: list[dict[str, Any]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        rooms.append(
            {"type": to_int(item.get("type")), "level": to_int(item.get("level"))}
        )
    return rooms


def _parse_operators(value: Any) -> list[Operator]:
    operators: list[Operator] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        char_data = _dict(item.get("charData"))
        source = char_data or item
        name = none_or_str(source.get("name")) or none_or_str(item.get("name"))
        if not name:
            continue
        rarity_key, rarity = _rarity(source.get("rarity"))
        weapon = _dict(item.get("weapon"))
        weapon_data = _dict(weapon.get("weaponData"))
        _, weapon_rarity = _rarity(weapon_data.get("rarity"))
        operators.append(
            Operator(
                name=name,
                char_id=none_or_str(source.get("id")) or none_or_str(source.get("charId")),
                rarity=rarity,
                rarity_key=rarity_key,
                profession=_nested_text(source.get("profession")),
                element=_nested_text(source.get("property")),
                level=to_int(item.get("level")),
                evolve_phase=to_int(item.get("evolvePhase")),
                potential_level=to_int(item.get("potentialLevel")),
                weapon_name=none_or_str(weapon_data.get("name")),
                weapon_rarity=weapon_rarity,
                weapon_level=to_int(weapon.get("level")),
                avatar_url=none_or_str(source.get("avatarSqUrl"))
                or none_or_str(source.get("avatarRtUrl"))
                or none_or_str(source.get("avatarUrl")),
            )
        )
    return operators


def _fetch_player_data(
    credential: dict[str, str],
    role_index: Any,
    device: str,
) -> PlayerProfile:
    """绑定列表 + 选中角色的 card/detail；完整数据失败时降级但不丢绑定信息。"""
    profile = fetch_profile(credential, device=device)
    if not profile.roles:
        profile.data_error = t("account.data.no_roles")
        return profile
    profile.role_index = max(0, min(len(profile.roles) - 1, to_int(role_index) or 0))
    role = pick_role(profile.roles, role_index)
    if role is None:  # pragma: no cover - 上面已判断不可能为空
        profile.data_error = t("account.data.no_roles")
        return profile
    if not role.api_ready:
        profile.data_error = t("account.data.role_incomplete")
        return profile
    try:
        card = fetch_card_detail(credential, role, device=device)
    except CredentialExpiredError:
        raise
    except AccountError as exc:
        log.warning("endfield card/detail unavailable: %s", exc)
        profile.data_error = exc.display()
        return profile
    profile.card = card
    if card.nickname:
        profile.nickname = card.nickname
    if card.role_id:
        profile.uid = card.role_id
    profile.data_error = None
    return profile


def fetch_player_data_with_refresh(
    credential: dict[str, str],
    *,
    role_index: Any = 0,
    device: str | None = None,
) -> tuple[PlayerProfile, dict[str, str]]:
    """先直接拉；cred 过期时静默刷新一次再重试（绑定列表 + card/detail）。"""
    dev = device or device_id()
    try:
        return _fetch_player_data(credential, role_index, dev), credential
    except CredentialExpiredError:
        refreshed = refresh_credential(credential, device=dev)
        return _fetch_player_data(refreshed, role_index, dev), refreshed


# -- 终末地签到（社区记录、未端到端验证；失败不阻塞主流程）------------------------
def _attendance_payload(
    method: str,
    credential: dict[str, str],
    role: Role,
    *,
    device: str,
) -> dict[str, Any]:
    if not role.api_ready:
        raise AccountError(
            t("account.error.skland.role_ids_missing"),
            hint=t("account.error.skland.role_ids_missing_hint"),
        )
    body = "{}" if method.upper() == "POST" else ""
    headers = _signed_headers(
        credential,
        path=ATTENDANCE_PATH,
        query="",
        device=device,
        extra_headers={GAME_ROLE_HEADER: _game_role(role)},
        body=body,
    )
    return _request_json(
        method.upper(),
        ATTENDANCE_URL,
        stage=t("account.error.stage.attendance"),
        headers=headers,
        json_body={} if method.upper() == "POST" else None,
    )


def parse_attendance(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.attendance_format"),
            hint=t("account.error.skland.attendance_format_hint"),
        )
    code = _status_code(payload)
    if code is not None and str(code) not in ("0", "200"):
        message = _message(payload) or t("account.error.upstream_rejected")
        if str(code) in ("401", "10002") or any(marker in message for marker in _AUTH_MARKERS):
            raise CredentialExpiredError(
                t("account.error.skland.login_expired", message=message),
                hint=t("account.error.skland.login_expired_hint"),
            )
        raise AccountError(
            t("account.error.skland.attendance_failed", code=code, message=message),
            hint=t("account.error.skland.attendance_failed_hint"),
        )
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def fetch_attendance(
    credential: dict[str, str],
    role: Role,
    *,
    device: str | None = None,
) -> dict[str, Any]:
    return parse_attendance(
        _attendance_payload("GET", credential, role, device=device or device_id())
    )


def sign_attendance(
    credential: dict[str, str],
    role: Role,
    *,
    device: str | None = None,
) -> dict[str, Any]:
    return parse_attendance(
        _attendance_payload("POST", credential, role, device=device or device_id())
    )


def sign_attendance_for(
    credential: dict[str, str],
    *,
    role_index: Any = 0,
    device: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """按当前角色签到：需要 roleId/serverId，先读绑定列表（过期则刷新一次）。"""
    dev = device or device_id()
    current = credential
    try:
        profile = fetch_profile(current, device=dev)
    except CredentialExpiredError:
        current = refresh_credential(current, device=dev)
        profile = fetch_profile(current, device=dev)
    role = pick_role(profile.roles, role_index)
    if role is None:
        raise AccountError(
            t("account.data.no_roles"),
            hint=t("account.error.skland.no_roles_hint"),
        )
    return sign_attendance(current, role, device=dev), current


# -- 绑定列表解析（纯函数，供测试覆盖）--------------------------------------
@dataclass
class _Binding:
    app: str
    app_name: str
    uid: str | None
    name: str | None
    level: int | None
    server: str | None
    klass: str | None
    element: str | None
    role_id: str | None = None
    server_id: str | None = None


def parse_bindings(payload: Any) -> PlayerProfile:
    if not isinstance(payload, dict):
        raise AccountError(
            t("account.error.skland.payload_unrecognized"),
            hint=t("account.error.skland.payload_unrecognized_hint"),
        )

    code = _status_code(payload)
    if code is not None and str(code) not in ("0", "200"):
        message = _message(payload) or t("account.error.unknown_error")
        raise AccountError(
            t("account.error.skland.api_error", code=code, message=message),
            hint=t("account.error.skland.api_error_hint"),
        )

    data = payload.get("data")
    if data is None:
        data = payload

    profile_nickname: str | None = None
    if isinstance(data, dict):
        profile_nickname = none_or_str(data.get("nickname")) or none_or_str(data.get("nickName"))

    items = _items_from(data)
    if not items:
        raise AccountError(
            t("account.error.skland.no_bindings"),
            hint=t("account.error.skland.no_bindings_hint"),
        )

    candidates = [binding for binding in _flatten(items) if binding.name or binding.uid]
    # 不同解析路径（defaultRole / roles[] / bindingList[]）可能重复同一条角色，按 id 去重
    bindings: list[_Binding] = []
    seen: set[tuple[str, str]] = set()
    for binding in candidates:
        key = (binding.role_id or binding.uid or "", binding.server_id or binding.server or "")
        if key in seen:
            continue
        seen.add(key)
        bindings.append(binding)
    if not bindings:
        raise AccountError(
            t("account.error.skland.no_roles"),
            hint=t("account.error.skland.no_roles_hint"),
        )

    endfield = [binding for binding in bindings if _is_endfield(binding)]
    if not endfield:
        apps = sorted(
            {
                binding.app_name or binding.app
                for binding in bindings
                if binding.app_name or binding.app
            }
        )
        raise AccountError(
            t("account.error.skland.no_endfield"),
            hint=t(
                "account.error.skland.no_endfield_hint",
                apps="、".join(apps) or t("account.error.skland.other_games"),
            ),
        )

    roles = [
        Role(
            name=binding.name or t("common.placeholder"),
            level=binding.level,
            server=binding.server,
            klass=binding.klass,
            element=binding.element,
            role_id=binding.role_id or binding.uid,
            server_id=binding.server_id,
        )
        for binding in endfield
    ]
    first = endfield[0]
    nickname = profile_nickname or first.name or t("account.error.skland.default_nickname")
    return PlayerProfile(
        provider=PROVIDER_SKLAND,
        nickname=nickname,
        uid=first.uid,
        roles=roles,
        avatar_path=None,
    )


def _is_endfield(binding: _Binding) -> bool:
    app = (binding.app or "").strip().lower()
    name = binding.app_name or ""
    if app == "endfield":
        return True
    if app:
        return False
    return "终末地" in name or "endfield" in name.lower() or not name


def _items_from(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "bindingList", "binding_list", "bindings", "items", "games"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _flatten(items: Iterable[Any], app: str = "", app_name: str = "") -> Iterable[_Binding]:
    for item in items:
        if not isinstance(item, dict):
            continue
        item_app = none_or_str(
            item.get("appCode")
            or item.get("gameCode")
            or item.get("app_code")
            or item.get("game_code")
        ) or app
        item_app_name = none_or_str(item.get("appName") or item.get("gameName")) or app_name
        # 国际服文档结构：bindingList[] 里是 {defaultRole:{roleId,serverId,...}, roles:[]}
        default_role = item.get("defaultRole") or item.get("default_role")
        if isinstance(default_role, dict):
            yield from _flatten([default_role], item_app, item_app_name)
        nested = (
            item.get("bindingList")
            or item.get("binding_list")
            or item.get("bindings")
            or item.get("roles")
        )
        if isinstance(nested, list) and nested:
            yield from _flatten(nested, item_app, item_app_name)
        if not any(
            key in item
            for key in (
                "uid",
                "roleId",
                "role_id",
                "gameUid",
                "nickName",
                "nickname",
                "roleName",
                "name",
            )
        ):
            continue
        yield _binding_from(item, item_app, item_app_name)


def _binding_from(item: dict[str, Any], app: str, app_name: str) -> _Binding:
    uid = none_or_str(
        item.get("uid")
        or item.get("roleId")
        or item.get("role_id")
        or item.get("gameUid")
        or item.get("game_uid")
    )
    name = none_or_str(
        item.get("nickName")
        or item.get("nickname")
        or item.get("roleName")
        or item.get("role_name")
        or item.get("name")
    )
    level = to_int(item.get("level") or item.get("roleLevel") or item.get("role_level"))
    server = none_or_str(
        item.get("channelName")
        or item.get("channel_name")
        or item.get("serverName")
        or item.get("server_name")
        or item.get("server")
    )
    # card/detail 的 serverId：国际服字段 serverId；国服绑定列表历史上用 channelMasterId
    server_id = none_or_str(
        item.get("serverId")
        or item.get("server_id")
        or item.get("channelMasterId")
        or item.get("channel_master_id")
    )
    role_id = none_or_str(
        item.get("roleId")
        or item.get("role_id")
        or item.get("gameRoleId")
        or item.get("game_role_id")
    ) or uid
    klass = none_or_str(
        item.get("profession")
        or item.get("class")
        or item.get("className")
        or item.get("class_name")
        or item.get("job")
    )
    element = none_or_str(
        item.get("element")
        or item.get("elementName")
        or item.get("element_name")
        or item.get("attribute")
    )
    return _Binding(
        app=app or "",
        app_name=app_name or "",
        uid=uid,
        name=name,
        level=level,
        server=server,
        klass=klass,
        element=element,
        role_id=role_id,
        server_id=server_id,
    )
