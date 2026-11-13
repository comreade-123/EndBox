# -*- coding: utf-8 -*-
"""从 fz.wiki 抓一次「干员补充资料」离线快照。

    python tools/build_operator_data.py                 # 全部干员（默认输出 assets/data/operators_extra.json）
    python tools/build_operator_data.py --limit 5       # 只抓前 5 个，调试用
    python tools/build_operator_data.py --only 伊冯 余烬
    python tools/build_operator_data.py --dry-run       # 只打印将写入什么，不落盘

数据来源：https://www.fz.wiki 的公开 API
``/api/v1/articles/by-title?ns=0&title=干员/<名字>&withRevision=1``，
只取文章里 ``endfieldCardCharacterHero`` 卡片的公开字段
（稀有度 / 职业 / 阵营 / 武器 / 词缀 / 简介 / 种族 / 生日 / CV 等）。
抓不到的干员不会伪造：整体跳过并写进 meta.missing。

授权说明：
- 站内文本为 CC BY-NC-SA 4.0（fz.wiki 版权声明）；
- 游戏图片/文本版权归上海鹰角网络科技有限公司，本快照仅本地工具使用、不再分发。
- 快照生成后运行时**不再联网**；该文件不进安装包也可正常运行（界面显示 —）。

只依赖标准库；失败（网络/接口变更）时退出码非 0 并打印原因。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API = "https://api.fz.wiki/api/v1/articles/by-title"
SOURCE = "https://www.fz.wiki"
LICENSE_TEXT = (
    "站内文本 CC BY-NC-SA 4.0（fz.wiki 版权声明）；"
    "游戏素材版权归上海鹰角网络科技有限公司所有，本快照仅本地工具使用、不重新分发。"
)
UA = "EndBox-OperatorData/1.0 (offline snapshot tool)"
TIMEOUT = 25
MAX_BIO = 1600


def operator_names() -> list[str]:
    """素材里已有的干员名（roster 顺序 + 目录里多出来的）。"""
    from endbox import config

    base = Path(config.GAME_ASSETS) / "operators"
    names: list[str] = []
    seen: set[str] = set()
    roster_path = base / "roster.json"
    if roster_path.is_file():
        try:
            raw = json.loads(roster_path.read_text("utf-8-sig"))
        except Exception:  # noqa: BLE001
            raw = {}
        if isinstance(raw, dict):
            for key in sorted(raw, key=lambda k: int(k) if str(k).isdigit() else 999):
                name = str(raw[key] or "").strip()
                if name and name not in seen:
                    names.append(name)
                    seen.add(name)
    if base.is_dir():
        for path in sorted(base.iterdir()):
            if path.is_dir() and not path.name.startswith(".") and path.name not in seen:
                names.append(path.name)
                seen.add(path.name)
    return names


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8", "replace"))


def _parse_hero(content_json: dict) -> dict | None:
    """从文章 contentJson 里找 endfieldCardCharacterHero 节点。"""
    if not isinstance(content_json, dict):
        return None
    stack = [content_json]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("type") == "endfieldCardCharacterHero":
                return node.get("attrs") if isinstance(node.get("attrs"), dict) else None
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def fetch_operator(name: str) -> dict | None:
    """抓一个干员；返回整理后的字段，失败返回 None。"""
    candidates = [name]
    for sep in ("（", "("):
        if sep in name:
            candidates.append(name.split(sep)[0].strip())
    if name == "管理员":
        candidates = ["管理员·男", "管理员·女", "管理员"]
    for candidate in candidates:
        title = f"干员/{candidate}"
        url = API + "?" + urllib.parse.urlencode({"ns": "0", "title": title, "withRevision": "1"})
        try:
            data = _get_json(url)
        except Exception:  # noqa: BLE001 - 404 / 网络错误都换下一个候选
            continue
        revision = data.get("revision") if isinstance(data, dict) else None
        content = revision.get("contentJson") if isinstance(revision, dict) else None
        hero = _parse_hero(content)
        if not hero:
            continue
        meta = {}
        for row in hero.get("meta") or []:
            if isinstance(row, dict) and row.get("label"):
                meta[str(row["label"])] = str(row.get("value") or "")
        bio = str(hero.get("bio") or "").strip()
        if len(bio) > MAX_BIO:
            bio = bio[:MAX_BIO].rstrip() + "…"
        try:
            rarity = int(hero.get("rarity")) if hero.get("rarity") is not None else None
        except (TypeError, ValueError):
            rarity = None
        return {
            "name": str(hero.get("name") or candidate),
            "name_en": str(hero.get("nameEn") or "") or None,
            "rarity": rarity,
            "profession": str(hero.get("profession") or "") or None,
            "sub_profession": str(hero.get("subProfession") or "") or None,
            "faction": str(hero.get("faction") or "") or None,
            "weapon_type": str(hero.get("weaponType") or "") or None,
            "tags": [str(t) for t in hero.get("tags") or [] if t],
            "bio": bio or None,
            "meta": meta,
            "source": SOURCE,
        }
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取 fz.wiki 干员资料，生成 assets/data/operators_extra.json")
    parser.add_argument("--output", default=str(ROOT / "assets" / "data" / "operators_extra.json"))
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 个（0 = 全部）")
    parser.add_argument("--only", nargs="*", default=None, help="只抓指定干员名")
    parser.add_argument("--delay", type=float, default=0.35, help="每次请求之间等待秒数")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    names = operator_names()
    if args.only:
        wanted = [str(n) for n in args.only]
        names = [n for n in names if n in wanted] or wanted
    if args.limit and args.limit > 0:
        names = names[: args.limit]
    if not names:
        print("素材里没有干员（检查 素材/operators/roster.json）。")
        return 1

    operators: dict[str, dict] = {}
    missing: list[str] = []
    for index, name in enumerate(names, start=1):
        data = fetch_operator(name)
        if data is None:
            missing.append(name)
            print(f"[{index}/{len(names)}] {name}: 未找到（跳过，界面显示 —）")
        else:
            operators[name] = data
            print(
                f"[{index}/{len(names)}] {name}: {data.get('rarity') or '—'}★ "
                f"{data.get('profession') or '—'} / {data.get('faction') or '—'}"
            )
        if args.delay and index < len(names):
            time.sleep(max(0.0, args.delay))

    payload = {
        "meta": {
            "source": SOURCE,
            "api": API,
            "license": LICENSE_TEXT,
            "fetched_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "operator_count": len(operators),
            "missing": missing,
        },
        "operators": operators,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    output = Path(args.output)
    if args.dry_run:
        print(f"\n[dry-run] 将写入 {output}（{len(text)} 字节，{len(operators)} 名干员，{len(missing)} 名缺失）")
        return 0
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(".tmp")
        temp.write_text(text, "utf-8")
        os.replace(temp, output)
    except OSError as exc:
        print(f"写入失败：{exc}")
        return 1
    print(f"\n已写入 {output}：{len(operators)} 名干员，{len(missing)} 名缺失")
    if missing:
        print("缺失：" + "、".join(missing))
    return 0 if operators else 1


if __name__ == "__main__":
    raise SystemExit(main())
