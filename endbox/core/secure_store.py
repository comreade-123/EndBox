# -*- coding: utf-8 -*-
"""凭据安全存储：Windows DPAPI（CryptProtectData）加密后落盘。

DPAPI 的密钥由 Windows 账号绑定，同一台机器同一用户才能解密；
文件被拷走也无法在别的账户下解开。内容永远不以明文写入磁盘。
"""
from __future__ import annotations

import base64
import ctypes
import json
import threading
from ctypes import wintypes

from endbox import config
from endbox.core.log import get_logger

log = get_logger("core.secure")

_IS_WINDOWS = hasattr(ctypes, "windll")

if _IS_WINDOWS:
    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL

    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf

    def _protect(data: bytes) -> bytes | None:
        blob, buf = _blob(data)
        out = _DataBlob()
        entropy, ebuf = _blob(b"EndBox.credentials.v1")
        ok = _crypt32.CryptProtectData(
            ctypes.byref(blob), "EndBox", ctypes.byref(entropy), None, None, 0, ctypes.byref(out)
        )
        if not ok:
            log.warning("CryptProtectData failed: %s", ctypes.get_last_error())
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, wintypes.HLOCAL))

    def _unprotect(data: bytes) -> bytes | None:
        blob, buf = _blob(data)
        out = _DataBlob()
        entropy, ebuf = _blob(b"EndBox.credentials.v1")
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(blob), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out)
        )
        if not ok:
            log.warning("CryptUnprotectData failed: %s", ctypes.get_last_error())
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, wintypes.HLOCAL))

    def _crash_free(func, data: bytes) -> bytes | None:
        try:
            return func(data)
        except Exception:  # noqa: BLE001
            return None
else:  # pragma: no cover - 非 Windows 平台只是不加密，用于开发调试
    def _crash_free(func, data: bytes) -> bytes | None:
        return base64.b64encode(data)

    def _protect(data: bytes) -> bytes | None:
        return base64.b64encode(data)

    def _unprotect(data: bytes) -> bytes | None:
        try:
            return base64.b64decode(data)
        except Exception:  # noqa: BLE001
            return None


def available() -> bool:
    """DPAPI 是否可用（Windows 上恒为 True）。"""
    if not _IS_WINDOWS:
        return False
    probe = _crash_free(_protect, b"probe")
    return probe is not None


class SecureStore:
    """加密的字符串键值存储。"""

    def __init__(self, path=config.SECURE_FILE):
        self._path = path
        self._lock = threading.RLock()
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        self._cache = {}
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return self._cache
        except OSError:
            return self._cache
        plain = _crash_free(_unprotect, raw)
        if plain is None:
            return self._cache
        try:
            obj = json.loads(plain.decode("utf-8"))
            if isinstance(obj, dict):
                self._cache = {str(k): str(v) for k, v in obj.items()}
        except Exception:  # noqa: BLE001
            log.warning("secure store payload unreadable")
        return self._cache

    def _flush(self) -> None:
        config.ensure_dirs()
        payload = json.dumps(self._cache or {}, ensure_ascii=False).encode("utf-8")
        blob = _crash_free(_protect, payload)
        if blob is None:
            log.error("secure store: encryption unavailable, refusing to write plaintext")
            return
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        self._path.write_bytes(blob)
        try:
            tmp.unlink()
        except OSError:
            pass

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            return self._load().get(key, default)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._load()
            assert self._cache is not None
            self._cache[key] = value
            self._flush()

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._load()
            if key in data:
                data.pop(key, None)
                self._flush()

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
            try:
                self._path.unlink()
            except OSError:
                pass

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._load().keys())


secure_store = SecureStore()
