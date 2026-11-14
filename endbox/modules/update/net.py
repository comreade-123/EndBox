# -*- coding: utf-8 -*-
"""更新检查的 HTTP 封装：超时、限流识别、SSL 兼容与可注入的 client。

安全约定：
  · 正式环境按正常 SSL 校验；
  · 只有显式设置环境变量 ``ENDBOX_INSECURE_TLS=1``（开发机的 HTTPS 中间人
    代理场景）才跳过证书校验，并写一条警告日志 —— 不做静默降级；
  · 所有失败统一抛 :class:`FetchError`，``kind`` 供 UI 显示人话提示。
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from endbox.core.log import get_logger
from endbox.core.text import t

log = get_logger("update.net")

DEFAULT_TIMEOUT = 8.0
DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json, application/json;q=0.9, */*;q=0.5",
    "X-GitHub-Api-Version": "2022-11-28",
    "Cache-Control": "no-cache",
}


class FetchError(Exception):
    """一次更新检查请求失败（消息可直接展示给用户）。"""

    def __init__(self, message: str, *, kind: str = "unknown", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def insecure_tls_enabled() -> bool:
    return os.environ.get("ENDBOX_INSECURE_TLS", "").strip().lower() in ("1", "true", "yes", "on")


def user_agent() -> str:
    from endbox.version import current_version

    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EndBox/"
        f"{current_version()} (+https://github.com/comreade-123/EndBox)"
    )


def make_client(
    *,
    verify: bool | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """构造带统一 UA / 超时的 httpx 客户端（测试可传 MockTransport）。"""
    if verify is None:
        verify = not insecure_tls_enabled()
    if not verify:
        log.warning("TLS 证书校验已关闭（ENDBOX_INSECURE_TLS），仅限开发环境")
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)),
        follow_redirects=True,
        headers={"User-Agent": user_agent(), **DEFAULT_HEADERS},
        verify=verify,
        transport=transport,
    )


def _error_for_response(response: httpx.Response) -> FetchError:
    status = int(response.status_code)
    remaining = response.headers.get("x-ratelimit-remaining", "")
    if status == 429 or (status in (403,) and remaining.strip() == "0"):
        return FetchError(t("update.error.ratelimit"), kind="ratelimit", status=status)
    if status == 404:
        return FetchError(t("update.error.not_found"), kind="not_found", status=status)
    return FetchError(t("update.error.http", status=status), kind="http", status=status)


def _handle_exception(exc: BaseException) -> FetchError:
    if isinstance(exc, FetchError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return FetchError(t("update.error.timeout"), kind="timeout")
    if isinstance(exc, httpx.TransportError):
        message = str(exc).lower()
        if "certificate" in message or "ssl" in message:
            return FetchError(t("update.error.ssl"), kind="ssl")
        return FetchError(t("update.error.offline"), kind="offline")
    return FetchError(t("update.error.unknown", error=f"{type(exc).__name__}: {exc}"), kind="unknown")


def request_text(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.BaseTransport | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """GET 一个文本资源，失败抛 :class:`FetchError`。"""
    owns_client = client is None
    active = client or make_client(timeout=timeout, transport=transport)
    try:
        response = active.get(url, params=params)
        if response.status_code >= 400:
            raise _error_for_response(response)
        return response.text
    except FetchError:
        raise
    except BaseException as exc:  # noqa: BLE001 - 统一转成人话错误
        raise _handle_exception(exc) from exc
    finally:
        if owns_client:
            try:
                active.close()
            except Exception:  # noqa: BLE001
                pass


def get_json(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.BaseTransport | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """GET + 解析 JSON；错误同样抛 :class:`FetchError`。"""
    text = request_text(url, client=client, timeout=timeout, transport=transport, params=params)
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise FetchError(t("update.error.bad_json"), kind="bad_json") from exc


def github_releases(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.BaseTransport | None = None,
) -> Any:
    """拉 GitHub Releases 列表（每页 30 条足够判断最新版本）。"""
    return get_json(url, client=client, timeout=timeout, transport=transport, params={"per_page": 30})


__all__ = [
    "DEFAULT_TIMEOUT",
    "FetchError",
    "get_json",
    "github_releases",
    "insecure_tls_enabled",
    "make_client",
    "request_text",
    "user_agent",
]
