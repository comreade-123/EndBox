# -*- coding: utf-8 -*-
"""同步官方小图标素材到 ``assets/gamedata/``（本地缓存，不入库、不打包）。

只抓体积小、数量少的图标组（职业 / 属性 / 武器类型 / 房间 / 货币 / 阵营 / 技能），
合计约 60 张、不到 1 MB；立绘、物品大图等大体积资源不抓。

    python tools/sync_icon_assets.py                 # 全量抓取
    python tools/sync_icon_assets.py --dry-run       # 只打印计划
    python tools/sync_icon_assets.py --only elements # 只抓某一组

数据来源：``https://data.akedata.wiki`` 的 ``asset-sync-index.json``（约 21MB，会缓存到
``_devdata/cache/``）。图片地址 = 站点 + ``datasets.images.root`` + 相对路径。
版权归鹰角网络所有，只在本地工具里使用，不重新分发。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://data.akedata.wiki"
MANIFEST_URL = f"{SITE}/manifest.json"
INDEX_URL = f"{SITE}/asset-sync-index.json"
TARGET = ROOT / "assets" / "gamedata"
CACHE = ROOT / "_devdata" / "cache" / "asset-sync-index.json"

# 组名 → 配置：路径标记 / 文件名前缀（None = 全部）/ 单文件大小上限 / 目标子目录
GROUPS: dict[str, dict] = {
    "professions": {"marker": "charprofessionicon/", "prefixes": ("icon_profession_",),
                    "max_size": 40_000, "subdir": "professions"},
    "elements": {"marker": "elementicon/", "prefixes": ("icon_charattrtype_",),
                 "max_size": 40_000, "subdir": "elements"},
    "weapons": {"marker": "weaponexhibit/", "prefixes": ("icon_weapon_exhibit_",),
                "max_size": 40_000, "subdir": "weapons"},
    "rooms": {"marker": "spaceshiproom/", "prefixes": ("icon_",),
              "max_size": 200_000, "subdir": "rooms"},
    "currency": {"marker": "walleticon/", "prefixes": ("item_", "icon_settlement"),
                 "max_size": 60_000, "subdir": "currency"},
    "factions": {"marker": "bloc/", "prefixes": ("faction_logo",),
                 "max_size": 60_000, "subdir": "factions"},
    "skills": {"marker": "skillicon/", "prefixes": ("btn_",),
               "max_size": 80_000, "subdir": "skills"},
}


def client() -> httpx.Client:
    return httpx.Client(verify=False, timeout=120, follow_redirects=True,
                        headers={"User-Agent": "EndBox-icon-sync"})


def fetch_index(http: httpx.Client, *, refresh: bool = False) -> dict:
    if CACHE.is_file() and not refresh:
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    print("下载图片索引（约 21MB，只下一次）…")
    response = http.get(INDEX_URL)
    response.raise_for_status()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(response.content)
    return response.json()


def plan(index: dict, only: str) -> list[tuple[str, str]]:
    images = (index.get("datasets") or {}).get("images") or {}
    root = str(images.get("root") or "public/images").strip("/")
    files = images.get("files") or {}
    picked: list[tuple[str, str]] = []
    for name, config in GROUPS.items():
        if only and only not in ("all", name):
            continue
        for path, meta in files.items():
            path = str(path)
            if config["marker"] not in path or not path.endswith(".png"):
                continue
            filename = path.rsplit("/", 1)[-1]
            if not filename.startswith(tuple(config["prefixes"])):
                continue
            try:
                size = int((meta or {}).get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if size and size > config["max_size"]:
                continue
            picked.append((f"{SITE}/{root}/{path}", str(TARGET / config["subdir"] / filename)))
    return picked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步终末地小图标素材到 assets/gamedata/")
    parser.add_argument("--only", default="all", help="只抓一组：" + "/".join(GROUPS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="重新下载 21MB 图片索引")
    args = parser.parse_args(argv)

    with client() as http:
        manifest = http.get(MANIFEST_URL).json()
        print("数据版本:", manifest.get("latest"))
        index = fetch_index(http, refresh=args.refresh)
        jobs = plan(index, args.only)
        print(f"计划下载 {len(jobs)} 个文件")
        if args.dry_run:
            for url, dest in jobs[:20]:
                print("  ", Path(dest).relative_to(ROOT))
            return 0
        done = failed = 0
        started = time.perf_counter()
        for url, dest in jobs:
            target = Path(dest)
            if target.is_file() and target.stat().st_size > 0:
                done += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                response = http.get(url)
                response.raise_for_status()
                target.write_bytes(response.content)
                done += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("  失败:", target.name, exc)
        print(f"完成 {done}，失败 {failed}，耗时 {time.perf_counter() - started:.1f}s")
        print("落点:", TARGET)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
