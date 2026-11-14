# -*- coding: utf-8 -*-
"""B 服（哔哩哔哩）扫码登录。

流程：generate 拿二维码链接 + qrcode_key → 视图渲染二维码 → 每 2s poll。
Cookie 只经 ``endbox.core.secure_store`` 加密保存，不落明文。
B 站身份只代表"已登录 B 站"；终末地角色数据仍需森空岛 Token（见 docs/account.md）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from endbox import config
from endbox.core import net
from endbox.core.net import NetworkError
from endbox.core.text import t
from endbox.core.secure_store import secure_store
from endbox.modules.account.models import (
    BILI_COOKIE_PREFIX,
    PROVIDER_BILIBILI,
    AccountError,
    PlayerProfile,
    none_or_str,
)

GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

WEB_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

# 登录成功后跳转 URL 里带的 Cookie 字段（只需要这几个）
COOKIE_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")

QR_OK = "ok"
QR_WAITING = "waiting"
QR_SCANNED = "scanned"
QR_EXPIRED = "expired"
QR_FAILED = "failed"

STATUS_TEXT = {
    QR_OK: t("account.error.bilibili.status_ok"),
    QR_WAITING: t("account.error.bilibili.status_waiting"),
    QR_SCANNED: t("account.error.bilibili.status_scanned"),
    QR_EXPIRED: t("account.error.bilibili.status_expired"),
    QR_FAILED: t("account.error.bilibili.status_failed"),
}

# 扫码轮询状态码（B 站 passport 接口约定）
STATUS_CODES = {
    0: QR_OK,
    86101: QR_WAITING,   # 未扫码
    86090: QR_SCANNED,   # 已扫码未确认
    86038: QR_EXPIRED,   # 二维码过期
}

QR_TTL_SECONDS = 180


@dataclass
class BiliQr:
    url: str
    qrcode_key: str


@dataclass
class BiliPoll:
    status: str
    message: str
    code: int
    cookies: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == QR_OK

    @property
    def terminal(self) -> bool:
        return self.status in (QR_OK, QR_EXPIRED, QR_FAILED)


def generate_qr() -> BiliQr:
    """申请一张新二维码。失败转成人话 AccountError。"""
    try:
        payload = net.get_json(GENERATE_URL, headers=WEB_HEADERS)
    except NetworkError as exc:
        raise AccountError(
            t("account.error.bilibili.connect", error=exc),
            hint=t("account.error.bilibili.connect_hint"),
        ) from exc
    if not isinstance(payload, dict):
        raise AccountError(t("account.error.bilibili.bad_payload"))
    code = payload.get("code")
    if code != 0:
        message = none_or_str(payload.get("message")) or t("account.error.unknown_error")
        raise AccountError(
            t("account.error.bilibili.generate_failed", code=code, message=message),
            hint=t("account.error.bilibili.generate_failed_hint"),
        )
    data = payload.get("data") or {}
    url = none_or_str(data.get("url"))
    key = none_or_str(data.get("qrcode_key"))
    if not url or not key:
        raise AccountError(
            t("account.error.bilibili.no_qr_url"),
            hint=t("account.error.bilibili.no_qr_url_hint"),
        )
    return BiliQr(url=url, qrcode_key=key)


def poll_qr(qrcode_key: str) -> BiliPoll:
    """轮询一次扫码状态。网络失败抛 AccountError（视图继续轮询/重试）。"""
    key = str(qrcode_key or "").strip()
    if not key:
        raise AccountError(t("account.error.bilibili.empty_key"))
    try:
        payload = net.get_json(POLL_URL, params={"qrcode_key": key}, headers=WEB_HEADERS)
    except NetworkError as exc:
        raise AccountError(
            t("account.error.bilibili.poll_failed", error=exc),
            hint=t("account.error.bilibili.poll_failed_hint"),
        ) from exc
    return parse_poll_payload(payload)


def parse_poll_payload(payload) -> BiliPoll:
    """纯解析函数（测试用）：外层 code 固定 0，真正的扫码状态在 data.code。"""
    if not isinstance(payload, dict):
        raise AccountError(t("account.error.bilibili.bad_poll"))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    code_value = data.get("code")
    if code_value is None:
        code_value = payload.get("code")
    try:
        code = int(code_value)
    except (TypeError, ValueError):
        raise AccountError(t("account.error.bilibili.bad_poll")) from None
    status = STATUS_CODES.get(code, QR_FAILED)
    message = (
        none_or_str(data.get("message"))
        or none_or_str(payload.get("message"))
        or STATUS_TEXT.get(status, t("account.error.unknown_status"))
    )
    result = BiliPoll(status=status, message=message, code=code)
    if status == QR_OK:
        cookies = parse_cookie_url(none_or_str(data.get("url")) or "")
        if not cookies.get("SESSDATA"):
            raise AccountError(
                t("account.error.bilibili.no_cookie"),
                hint=t("account.error.bilibili.no_cookie_hint"),
            )
        result.cookies = cookies
    return result


def parse_cookie_url(url: str) -> dict[str, str]:
    """从登录成功后的跳转 URL 里取 Cookie。"""
    text = str(url or "").strip()
    if not text:
        return {}
    try:
        query = parse_qs(urlsplit(text).query)
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for key in COOKIE_KEYS:
        values = query.get(key)
        if values and values[0]:
            out[key] = values[0]
    return out


def load_cookies() -> dict[str, str]:
    cookies: dict[str, str] = {}
    for key in COOKIE_KEYS:
        value = secure_store.get(BILI_COOKIE_PREFIX + key.lower())
        if value:
            cookies[key] = value
    return cookies


def save_cookies(cookies: dict[str, str]) -> None:
    for key in COOKIE_KEYS:
        value = cookies.get(key)
        if value:
            secure_store.set(BILI_COOKIE_PREFIX + key.lower(), value)


def clear_cookies() -> None:
    for key in COOKIE_KEYS:
        secure_store.delete(BILI_COOKIE_PREFIX + key.lower())


def fetch_profile(cookies: dict[str, str] | None = None) -> PlayerProfile:
    """用已保存的 Cookie 拉 B 站昵称 / 头像。只读，不改登录态。"""
    jar = cookies if cookies is not None else load_cookies()
    if not jar.get("SESSDATA"):
        raise AccountError(
            t("account.error.bilibili.no_credential"),
            hint=t("account.error.bilibili.no_credential_hint"),
        )
    try:
        payload = net.get_json(NAV_URL, cookies=jar, headers=WEB_HEADERS)
    except NetworkError as exc:
        raise AccountError(
            t("account.error.bilibili.connect", error=exc),
            hint=t("account.error.bilibili.connect_fetch_hint"),
        ) from exc
    if not isinstance(payload, dict):
        raise AccountError(t("account.error.bilibili.bad_payload"))
    code = payload.get("code")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if code == -101:
        raise AccountError(
            t("account.error.bilibili.login_expired"),
            hint=t("account.error.bilibili.login_expired_hint"),
        )
    if code != 0 or not data.get("isLogin"):
        message = none_or_str(payload.get("message")) or t("account.error.unknown_error")
        raise AccountError(
            t("account.error.bilibili.api_error", code=code, message=message),
            hint=t("account.error.bilibili.api_error_hint"),
        )
    nickname = none_or_str(data.get("uname"))
    uid = none_or_str(data.get("mid"))
    avatar_path = _download_avatar(none_or_str(data.get("face")), uid)
    return PlayerProfile(
        provider=PROVIDER_BILIBILI,
        nickname=nickname,
        uid=uid,
        roles=[],
        avatar_path=avatar_path,
    )


def _download_avatar(url: str | None, uid: str | None) -> str | None:
    """头像下载失败不算错误：界面退回首字占位，不阻塞同步。"""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        config.ensure_dirs()
        target_dir = config.CACHE_DIR / "account"
        target_dir.mkdir(parents=True, exist_ok=True)
        tag = hashlib.sha1((uid or url).encode("utf-8")).hexdigest()[:12]
        path = target_dir / f"bili_{tag}.jpg"
        if path.is_file() and path.stat().st_size > 0:
            return str(path)
        response = net.request("GET", url, retries=1)
        if response.status_code == 200 and response.content:
            path.write_bytes(response.content)
            return str(path)
    except Exception:  # noqa: BLE001 - 头像不是关键路径
        return None
    return None
