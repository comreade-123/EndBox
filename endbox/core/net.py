# -*- coding: utf-8 -*-
"""HTTP 客户端：统一的 UA、超时、重试与错误类型。

第三方社区接口随时可能变动，所以所有网络调用都必须有明确的错误分支，
让界面能显示"未连接/失败/重试"而不是卡死。
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from endbox.core.log import get_logger

log = get_logger("core.net")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 EndBox/0.1"
)
TIMEOUT = httpx.Timeout(12.0, connect=8.0)


class NetworkError(Exception):
    """所有网络失败的统一异常（带用户可读信息）。"""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
    return _client


def close() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        finally:
            _client = None


def request(
    method: str,
    url: str,
    *,
    retries: int = 2,
    backoff: float = 0.6,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    data: Any = None,
    cookies: dict[str, str] | None = None,
) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client().request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                data=data,
                cookies=cookies,
            )
            if resp.status_code >= 500 and attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            return resp
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            log.warning("network %s %s failed (%s/%s): %s", method, url, attempt + 1, retries + 1, exc)
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise NetworkError(f"网络请求失败：{last}") from last


def get_json(url: str, **kwargs: Any) -> Any:
    resp = request("GET", url, **kwargs)
    if resp.status_code >= 400:
        raise NetworkError(f"服务返回 HTTP {resp.status_code}", status=resp.status_code)
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise NetworkError("服务返回的内容不是 JSON（接口可能已变更）") from exc


def post_json(url: str, **kwargs: Any) -> Any:
    resp = request("POST", url, **kwargs)
    if resp.status_code >= 400:
        raise NetworkError(f"服务返回 HTTP {resp.status_code}", status=resp.status_code)
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise NetworkError("服务返回的内容不是 JSON（接口可能已变更）") from exc


def get_text(url: str, **kwargs: Any) -> str:
    resp = request("GET", url, **kwargs)
    if resp.status_code >= 400:
        raise NetworkError(f"服务返回 HTTP {resp.status_code}", status=resp.status_code)
    return resp.text
