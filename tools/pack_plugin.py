# -*- coding: utf-8 -*-
"""把插件目录打包成市场 zip，计算 sha256，并更新 market/index.json。

用法::

    python tools/pack_plugin.py --all                 # 打包全部官方插件并写索引
    python tools/pack_plugin.py --plugin plugins/timers
    python tools/pack_plugin.py --all --check         # 只校验索引与包的 sha256
    python tools/pack_plugin.py --all --base-url https://raw.githubusercontent.com/.../main/market

包结构固定为「单个顶层目录」：``<id>/plugin.json``、``<id>/plugin.py`` ……，
市场模块只接受这种结构（见 endbox/plugins/market.py::safe_extract）。

打包是确定性的：文件顺序、时间戳都固定，同样的源码重复打包得到同样的 sha256，
方便发布流程做「索引与包是否一致」的校验。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MARKET_DIR = ROOT / "market"
PACKAGES_DIR = MARKET_DIR / "packages"
INDEX_FILE = MARKET_DIR / "index.json"

#: 打包时排除的文件 / 目录（开发缓存、尾递归数据、隐藏文件）
EXCLUDE_NAMES = {"__pycache__", ".git", ".pytest_cache", ".idea", ".vscode"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}
#: 固定 zip 时间戳，保证重复打包 sha256 稳定
FIXED_DATE = (2026, 1, 1, 0, 0, 0)

REPO = "comreade-123/EndBox"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"

#: 官方插件的展示信息（改这里；plugin.json 里的字段优先）
DEFAULT_META: dict[str, dict] = {
    "timers": {
        "category": "工具",
        "tags": ["计时", "材料", "刷新"],
        "changelog": "材料 / 采集点刷新倒计时，支持预设、持久化与到点提示。",
    },
    "timeline_stats": {
        "category": "排轴",
        "tags": ["排轴", "统计"],
        "changelog": "读取排轴快照，统计事件数、各干员与各类型事件、Buff 覆盖率。",
    },
    "permission_demo": {
        "category": "示例",
        "tags": ["权限", "示例"],
        "changelog": "演示宿主权限白名单：越权调用被拒绝并在界面显示原因。",
    },
    "operator_roster": {
        "category": "账号",
        "tags": ["账号", "干员", "练度"],
        "changelog": "按账号快照统计干员数量、平均等级、最高等级与服务器。",
    },
    "link_notes": {
        "category": "排轴",
        "tags": ["排轴", "连携", "备忘录"],
        "changelog": "记录干员 / 组合的连携触发条件，保存在插件私有数据里。",
        "homepage": f"https://github.com/{REPO}/tree/main/market/plugins/link_notes",
    },
}

DEFAULT_CATEGORY = "其他"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 256)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(plugin_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(plugin_dir.rglob("*")):
        relative = path.relative_to(plugin_dir)
        if any(part in EXCLUDE_NAMES for part in relative.parts):
            continue
        if path.is_dir():
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if path.name.startswith("."):
            continue
        files.append(path)
    return files


def load_plugin(plugin_dir: Path) -> dict:
    """读 plugin.json（不导入代码），返回 {id, name, version, ...}。"""
    manifest_path = Path(plugin_dir) / "plugin.json"
    try:
        raw = json.loads(manifest_path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[pack] 读不了 {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
        raise SystemExit(f"[pack] {manifest_path} 缺少 id")
    return raw


def build_zip(plugin_dir: Path, out_path: Path) -> tuple[str, int]:
    """把插件目录打成 ``<id>/...`` 的 zip；返回 (sha256, 字节数)。"""
    plugin_dir = Path(plugin_dir)
    out_path = Path(out_path)
    plugin_id = str(load_plugin(plugin_dir)["id"])
    files = _iter_files(plugin_dir)
    if not files:
        raise SystemExit(f"[pack] 插件目录是空的：{plugin_dir}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            relative = path.relative_to(plugin_dir).as_posix()
            info = zipfile.ZipInfo(f"{plugin_id}/{relative}", date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    tmp.replace(out_path)
    return sha256_file(out_path), out_path.stat().st_size


def _entry_for(plugin_dir: Path, *, package: Path, market_dir: Path, base_url: str | None) -> dict:
    raw = load_plugin(plugin_dir)
    plugin_id = str(raw["id"]).strip()
    version = str(raw.get("version") or "0.0.0").strip()
    meta = DEFAULT_META.get(plugin_id, {})
    if package.is_relative_to(market_dir):
        url = package.relative_to(market_dir).as_posix()
        if base_url:
            url = f"{base_url.rstrip('/')}/{url}"
    else:
        url = package.as_uri()
    return {
        "id": plugin_id,
        "name": str(raw.get("name") or plugin_id).strip(),
        "version": version,
        "author": str(raw.get("author") or "EndBox").strip(),
        "description": str(raw.get("description") or "").strip(),
        "icon": str(raw.get("icon") or "plugin").strip(),
        "category": str(meta.get("category") or DEFAULT_CATEGORY),
        "tags": list(meta.get("tags") or []),
        "homepage": str(meta.get("homepage") or f"https://github.com/{REPO}/tree/main/plugins/{plugin_id}"),
        "download_url": url,
        "sha256": sha256_file(package),
        "size": package.stat().st_size,
        "min_app_version": str(raw.get("min_app_version") or "").strip(),
        "permissions": [
            str(item).strip() for item in (raw.get("permissions") or []) if str(item).strip()
        ],
        "changelog": str(meta.get("changelog") or "").strip(),
    }


def load_index(index_file: Path) -> dict:
    index_file = Path(index_file)
    try:
        raw = json.loads(index_file.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[pack] 索引读不了 {index_file}: {exc}") from exc
    return raw if isinstance(raw, dict) else {}


def update_index(
    index_file: Path,
    entries: list[dict],
    *,
    market_dir: Path,
    name: str = "EndBox 官方插件市场",
    updated: str | None = None,
) -> dict:
    """把打包结果合并进 index.json：同 id 更新，其他条目保留。"""
    index_file = Path(index_file)
    market_dir = Path(market_dir)
    existing = load_index(index_file)
    current: dict[str, dict] = {}
    for item in existing.get("plugins") or []:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            current[str(item["id"]).strip()] = item
    for entry in entries:
        current[entry["id"]] = entry
    plugins = [current[plugin_id] for plugin_id in sorted(current)]
    index = {
        "schema": 1,
        "name": str(existing.get("name") or name),
        "updated": updated or date.today().isoformat(),
        "repo": f"https://github.com/{REPO}",
        "plugins": plugins,
    }
    index_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_file.with_name(index_file.name + ".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(index_file)
    return index


def discover_plugin_dirs(source_dirs: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for source in source_dirs:
        source = Path(source)
        if not source.is_dir():
            continue
        for child in sorted(source.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            if (child / "plugin.json").is_file():
                found.append(child)
    return found


def pack(
    plugin_dirs: Iterable[Path],
    *,
    market_dir: Path = MARKET_DIR,
    base_url: str | None = None,
) -> list[dict]:
    market_dir = Path(market_dir)
    packages_dir = market_dir / "packages"
    index_file = market_dir / "index.json"
    entries: list[dict] = []
    for plugin_dir in plugin_dirs:
        raw = load_plugin(plugin_dir)
        plugin_id = str(raw["id"]).strip()
        version = str(raw.get("version") or "0.0.0").strip()
        package = packages_dir / f"{plugin_id}-{version}.zip"
        sha256, size = build_zip(plugin_dir, package)
        entry = _entry_for(plugin_dir, package=package, market_dir=market_dir, base_url=base_url)
        entries.append(entry)
        print(f"[pack] {plugin_id} v{version} -> {package.relative_to(market_dir)} ({size} bytes, sha256 {sha256[:12]}…)")
    update_index(index_file, entries, market_dir=market_dir)
    print(f"[pack] 索引已更新：{index_file.relative_to(ROOT) if index_file.is_relative_to(ROOT) else index_file}（{len(entries)} 个条目）")
    return entries


def check(*, market_dir: Path = MARKET_DIR) -> int:
    """校验索引里每个条目的 sha256 与包文件一致，返回错误数。"""
    market_dir = Path(market_dir)
    index = load_index(market_dir / "index.json")
    errors = 0
    for entry in index.get("plugins") or []:
        if not isinstance(entry, dict):
            continue
        download = str(entry.get("download_url") or "")
        if not download or "://" in download:
            continue
        package = market_dir / download
        if not package.is_file():
            print(f"[check] 缺包：{download}")
            errors += 1
            continue
        actual = sha256_file(package)
        expected = str(entry.get("sha256") or "")
        if actual != expected:
            print(f"[check] sha256 不一致：{download}")
            errors += 1
        elif package.stat().st_size != int(entry.get("size") or -1):
            print(f"[check] 大小不一致：{download}")
            errors += 1
    print(f"[check] {'全部一致' if errors == 0 else f'{errors} 个问题'}（{market_dir.name}/index.json）")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包 EndBox 插件并更新市场索引")
    parser.add_argument("--all", action="store_true", help="打包 plugins/ 与 market/plugins/ 下全部插件")
    parser.add_argument("--plugin", action="append", default=[], help="只打包指定插件目录（可重复）")
    parser.add_argument("--market-dir", default=str(MARKET_DIR), help="市场目录（默认仓库 market/）")
    parser.add_argument(
        "--base-url",
        default=None,
        help="下载地址前缀，例如 https://raw.githubusercontent.com/<repo>/main/market；默认用仓库内相对路径",
    )
    parser.add_argument("--check", action="store_true", help="只校验索引与包是否一致")
    args = parser.parse_args(argv)
    market_dir = Path(args.market_dir).resolve()

    if args.check:
        return check(market_dir=market_dir)

    plugin_dirs: list[Path] = [Path(item) for item in args.plugin]
    if args.all or not plugin_dirs:
        plugin_dirs.extend(discover_plugin_dirs([ROOT / "plugins", market_dir / "plugins"]))
    if not plugin_dirs:
        print("[pack] 没有找到可以打包的插件")
        return 2
    pack(plugin_dirs, market_dir=market_dir, base_url=args.base_url)
    return check(market_dir=market_dir)


if __name__ == "__main__":
    raise SystemExit(main())
