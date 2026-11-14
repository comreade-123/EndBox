# -*- coding: utf-8 -*-
"""路径、应用配置与持久化设置。

所有可变数据都写到 ``%APPDATA%\\EndBox``：
  settings.json   普通设置（主题、窗口位置、HUD 参数……）
  secure.dat      DPAPI 加密后的凭据（见 core.secure_store）
  logs/           运行日志
  plugins/        用户安装的插件（与内置插件同构）
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

APP_NAME = "EndBox"
APP_DIR_NAME = "EndBox"

def _app_root() -> Path:
    """程序所在目录：源码运行时是仓库根目录，打包后是 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = _app_root()
BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))  # 单文件 exe 解包目录（源码运行时 = ROOT）

# 资源随 exe 一起打包在 _MEIPASS，数据则写在 exe 同目录的 EndBox/ 下
ASSETS = BUNDLE / "assets" if (BUNDLE / "assets").is_dir() else ROOT / "assets"
PLUGINS_DIR = BUNDLE / "plugins" if (BUNDLE / "plugins").is_dir() else ROOT / "plugins"
DOCS = ROOT / "docs"

# 素材目录（排轴工具的干员立绘 / 职业 / 属性图）——用户提供的原始素材
# 放在 exe 同目录即可（不随程序分发），也兼容打包进 assets/game 的情况
_GAME_ASSET_CANDIDATES = [
    ROOT / "素材",
    ROOT / "assets" / "game",
    BUNDLE / "素材",
    BUNDLE / "assets" / "game",
]
GAME_ASSETS: Path = next((p for p in _GAME_ASSET_CANDIDATES if p.is_dir()), _GAME_ASSET_CANDIDATES[0])

# 毛玻璃（抓桌面模糊）默认开启；截图/自动化测试可设 ENDBOX_NO_GLASS=1 得到稳定背景
GLASS_ENABLED = os.environ.get("ENDBOX_NO_GLASS", "") not in ("1", "true", "yes")


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", "utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _legacy_dir() -> Path | None:
    base = os.environ.get("APPDATA")
    if not base:
        return None
    path = Path(base) / APP_DIR_NAME
    return path if path.is_dir() else None


def _migrate_legacy(target: Path) -> None:
    """把旧版本 %APPDATA%\EndBox 的数据搬到软件目录（只搬一次，不删旧数据）。"""
    legacy = _legacy_dir()
    if legacy is None or legacy.resolve() == target.resolve():
        return
    if (target / "settings.json").exists():
        return
    import shutil

    for name in ("settings.json", "secure.dat", "timeline.json", "timeline_library.json"):
        src = legacy / name
        if src.is_file():
            try:
                shutil.copy2(src, target / name)
            except OSError:
                pass
    src_plugins = legacy / "plugins"
    if src_plugins.is_dir():
        try:
            shutil.copytree(src_plugins, target / "plugins", dirs_exist_ok=True)
        except OSError:
            pass


def _data_dir() -> Path:
    # 1) 显式指定（开发 / 测试）
    override = os.environ.get("ENDBOX_DATA_DIR")
    if override:
        return Path(override)
    # 2) 便携模式：
    #    打包后的 exe → 同目录 EndBox/（首次运行自动创建，用户要求）
    #    源码运行     → 仓库根目录 data/（避免弄脏源码目录）
    if getattr(sys, "frozen", False):
        portable = ROOT / APP_DIR_NAME
    else:
        portable = ROOT / "data"
    if _writable_dir(portable):
        _migrate_legacy(portable)
        return portable
    # 3) 程序目录不可写（如 Program Files）→ 退回用户目录
    base = os.environ.get("APPDATA") or os.environ.get("XDG_DATA_HOME")
    if base:
        fallback = Path(base) / APP_DIR_NAME
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return fallback
    return Path.home() / f".{APP_DIR_NAME.lower()}"


DATA_DIR = _data_dir()
LOG_DIR = DATA_DIR / "logs"
USER_PLUGINS_DIR = DATA_DIR / "plugins"
SETTINGS_FILE = DATA_DIR / "settings.json"
SECURE_FILE = DATA_DIR / "secure.dat"
CACHE_DIR = DATA_DIR / "cache"


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, USER_PLUGINS_DIR, CACHE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


DEFAULTS: dict[str, Any] = {
    "app": {
        "theme": "dark",           # dark | light
        "last_view": "home",
        "animations": True,
        "glass_follow": True,      # 毛玻璃背景跟随窗口实时移动
        "first_run": True,
        "version": "1.1.0",
    },
    "window": {"w": 1320, "h": 860, "x": None, "y": None},
    "account": {
        "provider": "",            # skland | bilibili
        "uid_masked": "",
        "auto_refresh": True,
        "demo_mode": False,
        "last_sync": 0,
    },
    "timeline": {
        "team": [],
        "zoom": 60.0,              # 像素 / 秒
        "snap": 0.1,
        "show_buffs": True,
        "duration": 30.0,
    },
    "hud": {
        "enabled": False,
        "opacity": 0.96,
        "scale": 1.0,
        "anchor": "top-center",
        "hotkey": "Ctrl+Alt+H",
        "show_clock": True,
        "show_battery": True,
        "show_account": True,
        "mode": "compact",
        "offset_x": 0,
        "offset_y": 0,
        "screen": "",
        "clock_12h": False,
        "clock_seconds": True,
    },
    "calc325": {"last_seed": ""},
    "plugins": {"enabled": [], "disabled": []},
}


class Settings:
    """极简设置存储：点路径读写 + 显式保存。线程安全。"""

    def __init__(self, path: Path = SETTINGS_FILE):
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        self.load()

    # -- 读写 ---------------------------------------------------------------
    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, dict):
                self._merge(self._data, raw)
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001 - 设置损坏时不应导致启动失败
            pass

    @classmethod
    def _merge(cls, base: dict, incoming: dict) -> None:
        for k, v in incoming.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                cls._merge(base[k], v)
            else:
                base[k] = v

    def save(self) -> None:
        with self._lock:
            try:
                ensure_dirs()
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")
                os.replace(tmp, self._path)
            except OSError:
                pass

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        with self._lock:
            for part in path.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, path: str, value: Any, save: bool = True) -> None:
        with self._lock:
            node = self._data
            parts = path.split(".")
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = value
        if save:
            self.save()

    def update(self, mapping: dict[str, Any], save: bool = True) -> None:
        for k, v in mapping.items():
            self.set(k, v, save=False)
        if save:
            self.save()

    @property
    def data(self) -> dict[str, Any]:
        return self._data


settings = Settings()


def resource_path(*parts: str) -> Path:
    """定位随程序分发的资源，兼容 PyInstaller 打包后的 _MEIPASS。"""
    base = Path(getattr(sys, "_MEIPASS", ROOT))
    return base.joinpath(*parts)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
