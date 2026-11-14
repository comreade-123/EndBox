# -*- coding: utf-8 -*-
"""从 data.akedata.wiki 抓取《明日方舟：终末地》干员数据，生成入库文件。

    python tools/fetch_game_data.py                 # 全量（缓存命中则不重复下载）
    python tools/fetch_game_data.py --dry-run       # 只统计，不落盘
    python tools/fetch_game_data.py --limit 3       # 只处理前 3 名（调试，产物不完整）
    python tools/fetch_game_data.py --refresh       # 忽略缓存重新下载
    python tools/fetch_game_data.py --no-network    # 只用本地缓存（不联网）

产物（全部入库，UTF-8）：
    assets/data/operators.json   全量干员 + 技能（精简，<400KB）
    assets/data/game_meta.json   游戏版本 / 数据站 revision / 来源声明 / 版权说明

数据来源（每一步都可在脚本里复查）：
    manifest.json                        → latest = 当前版本 id + tableCfgPath
    TableCfg/CharacterTable.json         → charId / name(文本引用) / engName / rarity /
                                           profession(int) / charTypeId(属性) / weaponType(int) / department
    TableCfg/CharProfessionTable.json    → 职业枚举 profession → 中文名（文本引用）
    TableCfg/CharTypeTable.json          → 属性枚举 charTypeId → 中文名 / 颜色 / 图标名
    TableCfg/CharGrowthTable.json        → 角色成长表的 skillGroupMap：技能组名/描述/图标/skillGroupType/skillIdList
    TableCfg/I18nTextTable_CN.json       → 文本 id → 中文文本（名称/技能名/描述都走它）
    Json/SkillData/<skillId>.json        → durationFrame（动画时长，帧）/ castType
    asset-sync-index.json                → 头像/立绘/技能图标在图片索引里的真实路径与 size/md5

已确认的数据规则（写进产物前逐条核对过）：
  * 技能组类型 CharGrowthTable.skillGroupMap[].skillGroupType：
    0 = 普攻，1 = 战技，2 = 终结技，3 = 连携技
    （依据社区前端 SKILL_GROUP_ORDER={0:0,1:1,2:3,3:2} 与技能类型前缀表
     [普攻, 战技, 连携技, 终结技] 的排序换算，见 _devdata/research 的 AKEDatabase 前端快照）。
  * 技能动画时长 = SkillData.durationFrame / 30（社区前端格式化函数按 30fps 换算帧→秒）。
  * SkillData.skillName 目前为空串，技能名/描述实际来自 CharGrowthTable.skillGroupMap 的
    name/desc 文本引用（经 I18nTextTable_CN 解析），不是编造。
  * 重击（heavyn）没有独立技能名：从普攻组 skillIdList 里找 id 含 power_attack/powerattack 的
    那个技能文件取时长，产物里 name/type/desc 都是 null，只给 id + duration。
  * 属性枚举 CharTypeTable.charTypeId → 中文名由文本表解析：Physical 物理 / Fire 灼热 /
    Cryst 寒冷 / Pulse 电磁 / Natural 自然。
  * 武器类型数据站没有枚举表：weaponType(int) → 武器名（1 单手剑 / 2 施术单元 / 3 双手剑 /
    5 长柄武器 / 6 手铳）是把 fz.wiki 快照与 CharacterTable 全量交叉验证得到的映射；
    出现未收录的 weaponType 时该字段写 null，不猜测。
  * faction / tags / bio 来自 fz.wiki 离线快照 assets/data/operators_extra.json（CC BY-NC-SA），
    先按中文名、再按英文名匹配；匹配不到就是 null（例如新角色尚未进快照时）。

授权说明：
  * 表结构与 Json/图片数据来自社区数据站 https://data.akedata.wiki（AKEDatabase，AGPL-3.0 前端；
    这里只读取其公开数据接口，不复制其代码）；
  * 游戏文本/图片版权归上海鹰角网络科技有限公司；本产物仅 EndBox 本地工具使用，不重新分发素材；
  * fz.wiki 站内文本 CC BY-NC-SA 4.0；快照生成后运行时不再联网。

缓存：_devdata/cache/gamedata/<版本 id>/（gitignored）；--no-network 时完全离线可跑。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
ASSETS_DATA = ROOT / "assets" / "data"
OPERATORS_JSON = ASSETS_DATA / "operators.json"
META_JSON = ASSETS_DATA / "game_meta.json"
EXTRAS_JSON = ASSETS_DATA / "operators_extra.json"
CACHE_ROOT = ROOT / "_devdata" / "cache" / "gamedata"

SITE = "https://data.akedata.wiki"
UA = "EndBox-DataSync/1.0 (+offline snapshot tool)"
TIMEOUT = 60
RETRIES = 3
DOWNLOAD_WORKERS = 6
MAX_OUTPUT_BYTES = 400 * 1024  # 交付要求：operators.json < 400KB

# 技能组枚举（CharGrowthTable.skillGroupType）→ 产物里的 kind / 中文类型
KIND_BY_GROUP_TYPE = {0: "attack", 1: "skill", 2: "ult", 3: "link"}
TYPE_LABEL_BY_KIND = {"attack": "普攻", "skill": "战技", "ult": "终结技", "link": "连携技"}
# 游戏里技能栏的展示顺序（社区前端 SKILL_GROUP_ORDER：普攻 → 战技 → 连携 → 终结）
DISPLAY_ORDER = {"attack": 0, "skill": 1, "link": 2, "ult": 3, "heavy": 4}

# 武器类型：数据站没有枚举表，这个映射由 fz.wiki 快照 × CharacterTable 全量交叉验证得到
WEAPON_TYPE_NAMES = {1: "单手剑", 2: "施术单元", 3: "双手剑", 5: "长柄武器", 6: "手铳"}

FRAME_RATE = 30  # SkillData.durationFrame 按 30fps 换算为秒（社区前端换算规则一致）

LICENSE = (
    "游戏数据与素材版权归上海鹰角网络科技有限公司所有；数据由社区数据站 data.akedata.wiki"
    "（AKEDatabase）公开接口提供，前端采用 AGPL-3.0；干员阵营/词缀/简介来自 fz.wiki 快照"
    "（CC BY-NC-SA 4.0）。本文件与相关素材仅 EndBox 本地工具使用，不再分发。"
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def now_text() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    """进度输出；编码异常时不影响流程（Windows 控制台可能是 GBK）。"""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("gbk", "replace").decode("gbk", "replace"), flush=True)


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text("utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, "utf-8", newline="\n")  # 固定 LF，避免 Windows CRLF 撑大文件
    os.replace(temp, path)


def resolve_text(i18n: dict, ref: Any) -> str | None:
    """解析 ``{id, text}`` 文本引用（text 为空时查 I18nTextTable_CN）。"""
    if ref is None:
        return None
    if isinstance(ref, str):
        return ref or None
    if not isinstance(ref, dict):
        return None
    text = ref.get("text")
    if isinstance(text, str) and text:
        return text
    key = ref.get("id")
    if key is None:
        return None
    value = i18n.get(str(key))
    if value is None:
        value = i18n.get(key)
    return str(value) if value else None


_RICH_TAG = re.compile(r"<[^>]{1,80}>")


def clean_rich_text(text: str | None) -> str | None:
    """去掉游戏富文本标记（``<@ba.pulse>`` / ``<#ba.conduct>`` 等），保留正文。"""
    if not text:
        return None
    cleaned = _RICH_TAG.sub("", text).replace("<", "").replace(">", "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned).strip()
    return cleaned or None


def frame_to_seconds(frames: Any) -> float | None:
    try:
        value = int(frames)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return round(value / FRAME_RATE, 3)


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def make_client():
    import httpx  # 延迟导入：--no-network / 单元测试不依赖网络栈

    return httpx.Client(
        verify=False,  # 本机 MITM 代理需要；数据站是纯静态公开 JSON
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json,*/*"},
    )


def fetch_bytes(client, url: str, *, tries: int = RETRIES) -> bytes:
    last: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            response = client.get(url)
            if response.status_code == 200:
                return response.content
            if response.status_code == 404:
                raise FileNotFoundError(f"404 {url}")
            raise RuntimeError(f"HTTP {response.status_code} {url}")
        except FileNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试
            last = exc
            if attempt < tries:
                time.sleep(0.6 * attempt)
    raise RuntimeError(f"下载失败 {url}: {last}")


def cached_download(
    client,
    url: str,
    path: Path,
    *,
    expected_size: int | None = None,
    expected_md5: str | None = None,
    refresh: bool = False,
) -> tuple[Path, int, bool]:
    """下载到缓存；size/md5 对得上就复用（断点/可重复运行）。返回 (path, bytes, 是否新下载)。"""
    path = Path(path)
    if not refresh and path.is_file():
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        if data and (
            (expected_size is None or len(data) == expected_size)
            and (expected_md5 is None or md5_bytes(data) == expected_md5)
        ):
            return path, len(data), False
    data = fetch_bytes(client, url)
    if expected_size is not None and len(data) != expected_size:
        raise RuntimeError(f"大小不符 {url}: {len(data)} != {expected_size}")
    if expected_md5 and md5_bytes(data) != expected_md5:
        raise RuntimeError(f"md5 不符 {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(data)
    os.replace(temp, path)
    return path, len(data), True


# ---------------------------------------------------------------------------
# 表 / 技能数据准备
# ---------------------------------------------------------------------------
REQUIRED_TABLES = (
    "CharacterTable.json",
    "CharProfessionTable.json",
    "CharTypeTable.json",
    "CharGrowthTable.json",
    "I18nTextTable_CN.json",
)


def prepare_tables(client, version: str, table_cfg_path: str, cache: Path, *, refresh: bool, allowed_network: bool) -> dict:
    """下载并读入所需 TableCfg 表；返回 {表名: 解析后的 dict}。"""
    tables: dict[str, Any] = {}
    total_bytes = 0
    for name in REQUIRED_TABLES:
        path = cache / "TableCfg" / f"{name}"
        if allowed_network:
            url = f"{SITE}/{table_cfg_path}/{name}"
            _, size, downloaded = cached_download(client, url, path, refresh=refresh)
            total_bytes += size
            log(f"  表 {name}: {size / 1048576:.1f} MB{'（新下载）' if downloaded else '（缓存）'}")
        elif not path.is_file():
            raise FileNotFoundError(f"离线模式缺少缓存：{path}")
        tables[name] = read_json(path)
    tables["__bytes__"] = total_bytes
    return tables


def load_asset_index(client, cache: Path, *, refresh: bool, allowed_network: bool) -> dict:
    """图片/Json 统一索引（21MB，只在需要校验素材路径时读）。"""
    path = cache / "asset-sync-index.json"
    if allowed_network:
        cached_download(client, f"{SITE}/asset-sync-index.json", path, refresh=refresh)
    elif not path.is_file():
        raise FileNotFoundError(f"离线模式缺少缓存：{path}")
    return read_json(path)


def _canonical_images(index: dict) -> dict[str, dict]:
    files = ((index or {}).get("datasets") or {}).get("images", {}).get("files") or {}
    return files


IMAGE_SPRITES = "assets/beyond/dynamicassets/gameplay/ui/sprites/"


def image_paths(index_files: dict, folder: str) -> dict[str, str]:
    """规范化图片相对路径：只取主资源目录（排除 i18n 多语言副本）。

    键为**去掉 .png 文件名后缀的名字**（charicon: ``icon_chr_0004_pelica``，
    portrait: ``chr_0004_pelica``，skillicon: ``icon_skill_pelica_01``），值为索引里的完整相对路径。
    """
    prefix = f"{IMAGE_SPRITES}{folder}/"
    bucket: dict[str, str] = {}
    for path in index_files:
        if not path.startswith(prefix) or not path.lower().endswith(".png"):
            continue
        name = path[len(prefix):]
        if "/" in name:  # 只收本目录下的文件
            continue
        bucket[name[:-4]] = path
    return bucket


def skill_ids_for_group(group: dict) -> list[str]:
    ids = group.get("skillIdList")
    if not isinstance(ids, list):
        return []
    return [str(item) for item in ids if isinstance(item, str) and item]


def find_heavy_id(skill_ids: Iterable[str]) -> str | None:
    for skill_id in skill_ids:
        low = skill_id.lower()
        if "power_attack" in low or "powerattack" in low:
            return skill_id
    return None


def plan_skill_downloads(growth_table: dict) -> list[tuple[str, str, str]]:
    """(角色 id, kind, skillId)——每个技能组取主 id，另加重击；去重后返回。"""
    wanted: dict[str, tuple[str, str, str]] = {}
    for char_id, row in growth_table.items():
        for group in (row.get("skillGroupMap") or {}).values():
            ids = skill_ids_for_group(group)
            if not ids:
                continue
            kind = KIND_BY_GROUP_TYPE.get(group.get("skillGroupType"))
            if kind is None:
                continue
            wanted.setdefault(ids[0], (char_id, kind, ids[0]))
            heavy = find_heavy_id(ids)
            if heavy:
                wanted.setdefault(heavy, (char_id, "heavy", heavy))
    return list(wanted.values())


def download_skill_data(
    client,
    version: str,
    cache: Path,
    json_files: dict,
    wanted: list[tuple[str, str, str]],
    *,
    refresh: bool,
    allowed_network: bool,
    progress_every: int = 20,
) -> tuple[dict[str, dict], int]:
    """下载 SkillData/<skillId>.json（size/md5 来自索引），返回 (skillId→数据, 下载字节)。"""
    skill_dir = cache / "SkillData"
    skill_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict] = {}
    downloaded_bytes = 0
    pending: list[tuple[str, Path, int | None, str | None]] = []
    for char_id, kind, skill_id in wanted:
        rel = f"SkillData/{skill_id}.json"
        meta = json_files.get(rel) or {}
        path = skill_dir / f"{skill_id}.json"
        pending.append((skill_id, path, meta.get("size"), meta.get("md5")))
        if not allowed_network and not path.is_file():
            raise FileNotFoundError(f"离线模式缺少缓存：{path}")
        if path.is_file() and not refresh:
            try:
                result[skill_id] = read_json(path)
            except Exception:  # noqa: BLE001 - 缓存坏了就重下
                pending[-1] = (skill_id, path, meta.get("size"), meta.get("md5"))
    to_download = [(i, p, s, m) for (i, p, s, m) in pending if i not in result]
    log(
        f"  SkillData：需要 {len(pending)} 个（去重后），缓存命中 {len(result)} 个，"
        f"待下载 {len(to_download)} 个"
    )
    if to_download and allowed_network:
        done = 0
        total = len(to_download)
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
            futures = {}
            for skill_id, path, size, digest in to_download:
                url = f"{SITE}/public/Json/SkillData/{skill_id}.json"
                futures[pool.submit(
                    cached_download, client, url, path,
                    expected_size=size, expected_md5=digest, refresh=False,
                )] = skill_id
            for future in as_completed(futures):
                skill_id = futures[future]
                try:
                    path, size, _ = future.result()
                except Exception as exc:  # noqa: BLE001 - 单个技能失败记录后继续
                    log(f"  ! 技能数据下载失败 {skill_id}: {exc}")
                    continue
                downloaded_bytes += size
                try:
                    result[skill_id] = read_json(path)
                except Exception as exc:  # noqa: BLE001
                    log(f"  ! 技能数据解析失败 {skill_id}: {exc}")
                done += 1
                if progress_every and (done % progress_every == 0 or done == total):
                    log(f"  SkillData 进度 {done}/{total}")
    elif to_download:
        raise FileNotFoundError(f"离线模式缺少技能数据：{to_download[0][1]}")
    return result, downloaded_bytes


# ---------------------------------------------------------------------------
# 组装干员数据
# ---------------------------------------------------------------------------
def load_extras_index(path: Path = EXTRAS_JSON) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """读 fz.wiki 快照：返回 (中文名→资料, 英文名→[中文名…])。"""
    if not Path(path).is_file():
        return {}, {}
    try:
        raw = json.loads(Path(path).read_text("utf-8"))
    except Exception:  # noqa: BLE001 - 快照坏了只是补充字段缺失
        return {}, {}
    operators = raw.get("operators") if isinstance(raw.get("operators"), dict) else {}
    by_en: dict[str, list[str]] = {}
    for name, data in operators.items():
        if not isinstance(data, dict):
            continue
        en = str(data.get("name_en") or "").strip()
        if en:
            by_en.setdefault(en.lower(), []).append(str(name))
    return operators, by_en


def match_extra(operator: dict, extras: dict[str, dict], by_en: dict[str, list[str]]) -> tuple[dict, str | None]:
    """按中文名 → 英文名匹配 fz.wiki 记录；返回 (记录, 使用的快照键)。"""
    name = operator.get("name") or ""
    if name in extras:
        return extras[name], name
    en = (operator.get("name_en") or "").strip().lower()
    candidates = by_en.get(en) if en else None
    if candidates:
        key = sorted(candidates)[0]
        return extras.get(key, {}), key
    return {}, None


def parse_skill_group(
    group: dict,
    *,
    skill_data: dict[str, dict],
    i18n: dict,
    icons: dict[str, str],
) -> dict | None:
    """一个技能组 → 产物条目（战技/连携/终结/普攻）。"""
    ids = skill_ids_for_group(group)
    kind = KIND_BY_GROUP_TYPE.get(group.get("skillGroupType"))
    if not ids or kind is None:
        return None
    primary = ids[0]
    data = skill_data.get(primary) or {}
    icon = group.get("icon") or None
    icon = icon if icon and icon in icons else None
    duration = frame_to_seconds(data.get("durationFrame"))
    entry = {
        "id": primary,
        "kind": kind,
        "type": TYPE_LABEL_BY_KIND.get(kind),
        "name": resolve_text(i18n, group.get("name")),
        "icon": icon,
        "duration": duration,
        "duration_frame": data.get("durationFrame") if duration is not None else None,
        "desc": clean_rich_text(resolve_text(i18n, group.get("desc"))),
        "skill_ids": ids,
    }
    return entry


def parse_heavy_entry(group: dict, *, skill_data: dict[str, dict]) -> dict | None:
    """重击条目：普攻组里的 power_attack，没有名字/图标，只取时长。"""
    if group.get("skillGroupType") != 0:
        return None
    ids = skill_ids_for_group(group)
    heavy = find_heavy_id(ids)
    if not heavy:
        return None
    data = skill_data.get(heavy) or {}
    duration = frame_to_seconds(data.get("durationFrame"))
    if duration is None:
        return None
    return {
        "id": heavy,
        "kind": "heavy",
        "type": None,
        "name": None,
        "icon": None,
        "duration": duration,
        "duration_frame": data.get("durationFrame"),
        "desc": None,
        "skill_ids": [heavy],
    }


def build_operator(
    char_id: str,
    char_row: dict,
    growth_row: dict,
    *,
    profile_names: dict[str, str],
    profession_icons: dict[str, str],
    type_rows: dict[str, dict],
    i18n: dict,
    skill_data: dict[str, dict],
    char_icons: dict[str, str],
    portraits: dict[str, str],
    skill_icons: dict[str, str],
    extras: dict[str, dict],
    by_en: dict[str, list[str]],
) -> dict:
    """CharacterTable 一行 + 成长表 + 技能数据 → 干员条目。缺失字段一律 None。"""
    icon_key = f"icon_{char_id}"
    portrait_key = char_id
    char_icon = char_icons.get(icon_key)
    portrait = portraits.get(portrait_key)

    element_id = char_row.get("charTypeId")
    element_row = type_rows.get(element_id) or {}
    profession_id = char_row.get("profession")
    weapon_id = char_row.get("weaponType")
    name = resolve_text(i18n, char_row.get("name"))

    # 同名角色（管理员有 3 行：chr_0002_endminm / chr_0003_endminf / chr_9000_endmin）
    # 用 id 里的 m/f 后缀区分，9000 行没有头像立绘（has_assets=False），variant 留空。
    variant = None
    if "_endmin" in char_id:
        suffix = char_id.rsplit("_", 1)[-1].replace("endmin", "")
        variant = suffix or None

    groups = list((growth_row.get("skillGroupMap") or {}).values())
    skills: list[dict] = []
    for group in groups:
        entry = parse_skill_group(group, skill_data=skill_data, i18n=i18n, icons=skill_icons)
        if entry:
            skills.append(entry)
    for group in groups:
        heavy = parse_heavy_entry(group, skill_data=skill_data)
        if heavy:
            skills.append(heavy)
            break
    skills.sort(key=lambda item: DISPLAY_ORDER.get(str(item.get("kind")), 9))

    directory = name if name else char_id
    base = {
        "id": char_id,
        "name": name,
        "name_en": (char_row.get("engName") or None),
        "rarity": char_row.get("rarity"),
        "profession": profile_names.get(str(profession_id)) or None,
        "profession_id": profession_id,
        "profession_icon": profession_icons.get(str(profession_id)),
        "element": resolve_text(i18n, element_row.get("name")),
        "element_id": element_id,
        "element_color": (element_row.get("color") or None),
        "weapon": WEAPON_TYPE_NAMES.get(weapon_id),
        "weapon_id": weapon_id,
        "faction": None,
        "tags": [],
        "bio": None,
        "department": (char_row.get("department") or None),
        "variant": variant,
        "has_assets": bool(char_icon and portrait),
        # 下列路径是「运行时素材根」下的相对路径（素材/ 或 assets/gamedata/ 都按这个结构找），
        # 不写死本机绝对路径；程序读取优先级：素材/（用户原素材）优先，assets/gamedata/ 补缺。
        "avatar": f"operators/{directory}/avatar.png" if (name and char_icon) else None,
        "portrait": f"operators/{directory}/portrait.png" if (name and portrait) else None,
        "skills": skills,
    }
    extra, extra_key = match_extra(base, extras, by_en)
    base["faction"] = str(extra.get("faction") or "") or None
    tags = extra.get("tags") if isinstance(extra.get("tags"), list) else []
    base["tags"] = [str(tag) for tag in tags if tag]
    base["bio"] = str(extra.get("bio") or "") or None
    base["extra_key"] = extra_key
    return base


def build_profession_names(profession_table: dict, i18n: dict) -> dict[str, str]:
    """职业枚举 int → 中文名。"""
    names: dict[str, str] = {}
    for key, row in (profession_table or {}).items():
        if not isinstance(row, dict):
            continue
        name = resolve_text(i18n, row.get("name"))
        if name:
            names[str(key)] = name
    return names


def build_payload(
    *,
    manifest: dict,
    version_record: dict,
    char_table: dict,
    profession_table: dict,
    type_table: dict,
    growth_table: dict,
    i18n: dict,
    skill_data: dict[str, dict],
    index: dict | None,
    extras_path: Path = EXTRAS_JSON,
    limit: int = 0,
) -> dict:
    """纯函数：所有表 → operators.json payload（不联网，可单测）。"""
    index_files = _canonical_images(index or {})
    char_icons = image_paths(index_files, "charicon")
    portraits = image_paths(index_files, "characterportrait")
    skill_icons = image_paths(index_files, "skillicon")
    extras, by_en = load_extras_index(extras_path)
    profession_names = build_profession_names(profession_table, i18n)
    profession_icons = {
        str(key): (row.get("iconId") or None)
        for key, row in (profession_table or {}).items()
        if isinstance(row, dict)
    }

    ids = sorted(char_table)
    if limit and limit > 0:
        ids = ids[:limit]
    operators: list[dict] = []
    for char_id in ids:
        char_row = char_table.get(char_id)
        growth_row = growth_table.get(char_id) or {}
        if not isinstance(char_row, dict):
            continue
        operator = build_operator(
            char_id,
            char_row,
            growth_row,
            profile_names=profession_names,
            profession_icons=profession_icons,
            type_rows=type_table or {},
            i18n=i18n,
            skill_data=skill_data,
            char_icons=char_icons,
            portraits=portraits,
            skill_icons=skill_icons,
            extras=extras,
            by_en=by_en,
        )
        operators.append(operator)

    operators.sort(key=lambda op: (str(op.get("name") or ""), str(op.get("id"))))
    skill_count = sum(len(op.get("skills") or []) for op in operators)
    unique_names = len({op.get("name") for op in operators if op.get("name")})
    payload = {
        "meta": {
            "schema": "endbox.operators/1",
            "game_version": version_record.get("gameVersion"),
            "revision": version_record.get("id"),
            "hotfix_version": version_record.get("hotfixVersion"),
            "table_cfg_path": version_record.get("tableCfgPath"),
            "table_published_at": version_record.get("publishedAt"),
            "generated_at": now_text(),
            "generator": "tools/fetch_game_data.py",
            "operator_count": len(operators),
            "unique_name_count": unique_names,
            "skill_count": skill_count,
            "sources": {
                "game_data": {"site": SITE, "manifest_latest": manifest.get("latest"),
                              "shared_revision": manifest.get("sharedRevision")},
                "extras": {"file": "assets/data/operators_extra.json",
                           "site": "https://www.fz.wiki", "license": "CC BY-NC-SA 4.0"},
            },
            "license": LICENSE,
            "field_notes": {
                "duration": "SkillData.durationFrame / 30（秒）",
                "kind": "attack=普攻 skill=战技 link=连携技 ult=终结技 heavy=重击（只有时长，无名称）",
                "faction/tags/bio": "fz.wiki 快照，匹配不到为 null（extra_key = 实际匹配到的快照键）",
                "weapon": "weaponType 映射（数据站无枚举表，与 fz.wiki 交叉验证）",
                "variant/has_assets": "重复名角色（管理员×3）用 variant 区分；has_assets=有头像与立绘",
                "avatar/portrait": "运行时素材根下的相对路径（素材/ 或 assets/gamedata/），程序优先读 素材/、缺了再读 assets/gamedata/",
            },
        },
        "operators": operators,
    }
    return payload


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_payload(payload: dict) -> list[str]:
    """返回错误列表（空 = 通过）。数量、字段完整性、重复、字段类型都查。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload 不是对象"]
    meta = payload.get("meta")
    operators = payload.get("operators")
    if not isinstance(meta, dict) or not isinstance(operators, list):
        return ["缺少 meta/operators"]
    for key in ("game_version", "revision", "generated_at", "operator_count", "license"):
        if not meta.get(key):
            errors.append(f"meta 缺少 {key}")
    if meta.get("operator_count") != len(operators):
        errors.append(f"meta.operator_count={meta.get('operator_count')} 与 operators={len(operators)} 不一致")
    if not operators:
        return errors + ["operators 为空"]
    seen_ids: set[str] = set()
    for index_no, op in enumerate(operators):
        if not isinstance(op, dict):
            errors.append(f"operators[{index_no}] 不是对象")
            continue
        op_id = op.get("id")
        if not isinstance(op_id, str) or not op_id:
            errors.append(f"operators[{index_no}] 缺少 id")
        elif op_id in seen_ids:
            errors.append(f"id 重复：{op_id}")
        else:
            seen_ids.add(op_id)
        name = op.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{op_id or index_no} 缺少 name")
        rarity = op.get("rarity")
        if rarity is not None and not isinstance(rarity, int):
            errors.append(f"{op_id} rarity 不是整数")
        for key in ("profession", "element", "weapon", "faction", "avatar", "portrait"):
            if key in op and op.get(key) is not None and not isinstance(op.get(key), str):
                errors.append(f"{op_id} {key} 类型非法")
        if not isinstance(op.get("tags"), list):
            errors.append(f"{op_id} tags 不是列表")
        skills = op.get("skills")
        if not isinstance(skills, list) or not skills:
            errors.append(f"{op_id} 没有 skills")
            continue
        kinds: set[str] = set()
        for skill in skills:
            if not isinstance(skill, dict):
                errors.append(f"{op_id} skills 里有非对象")
                continue
            skill_kind = skill.get("kind")
            if skill_kind not in {"attack", "skill", "link", "ult", "heavy"}:
                errors.append(f"{op_id} 未知 kind={skill_kind!r}")
            kinds.add(str(skill_kind))
            if not isinstance(skill.get("id"), str) or not skill.get("id"):
                errors.append(f"{op_id} 技能缺少 id")
            duration = skill.get("duration")
            if duration is not None and (not isinstance(duration, (int, float)) or duration < 0):
                errors.append(f"{op_id} 技能 duration 非法：{duration!r}")
            icon = skill.get("icon")
            if icon is not None and not isinstance(icon, str):
                errors.append(f"{op_id} 技能 icon 非法")
        for required in ("skill", "link", "ult"):
            if required not in kinds:
                errors.append(f"{op_id} 缺少 {required} 技能组")
    return errors


def payload_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))


def validate_existing(operators_path: Path = OPERATORS_JSON, meta_path: Path = META_JSON) -> int:
    """离线校验已生成的产物（--validate-only）：数量、字段完整性、体积、meta 一致性。"""
    if not Path(operators_path).is_file():
        log(f"找不到 {operators_path}，先运行 python tools/fetch_game_data.py")
        return 1
    payload = read_json(operators_path)
    errors = validate_payload(payload)
    size = payload_size(payload)
    meta = payload.get("meta") or {}
    log(f"干员 {len(payload.get('operators') or [])} 名，技能 {meta.get('skill_count')} 条，"
        f"文件 {size / 1024:.1f} KB（上限 {MAX_OUTPUT_BYTES / 1024:.0f} KB）")
    if size > MAX_OUTPUT_BYTES:
        errors.append(f"operators.json 超过 {MAX_OUTPUT_BYTES / 1024:.0f} KB 上限")
    if Path(meta_path).is_file():
        game_meta = read_json(meta_path)
        for key in ("game_version", "revision"):
            if game_meta.get(key) != meta.get(key):
                errors.append(f"game_meta.{key}={game_meta.get(key)!r} 与 operators.meta.{key}={meta.get(key)!r} 不一致")
        if not game_meta.get("license"):
            errors.append("game_meta 缺少 license")
    else:
        errors.append(f"缺少 {meta_path}")
    if errors:
        log("校验失败：")
        for error in errors:
            log(f"  - {error}")
        return 1
    log("校验通过")
    return 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 data.akedata.wiki 抓取终末地干员数据，生成 assets/data/operators.json 与 game_meta.json"
    )
    parser.add_argument("--dry-run", action="store_true", help="只下载/解析并打印统计，不写文件")
    parser.add_argument("--validate-only", action="store_true",
                        help="不联网：只校验现有 operators.json / game_meta.json（数量与字段完整性）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 名干员（调试，产物不完整）")
    parser.add_argument("--refresh", action="store_true", help="忽略本地缓存重新下载")
    parser.add_argument("--no-network", action="store_true", help="完全离线，只用 _devdata/cache 缓存")
    parser.add_argument("--extras", default=str(EXTRAS_JSON), help="fz.wiki 快照路径")
    parser.add_argument("--output", default=str(OPERATORS_JSON), help="operators.json 输出路径")
    parser.add_argument("--meta-output", default=str(META_JSON), help="game_meta.json 输出路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.validate_only:
        return validate_existing(Path(args.output), Path(args.meta_output))
    allowed_network = not args.no_network
    client = make_client() if allowed_network else None
    try:
        log(f"数据站：{SITE}")
        if allowed_network:
            manifest = json.loads(fetch_bytes(client, f"{SITE}/manifest.json").decode("utf-8"))
        else:
            manifest = read_json(CACHE_ROOT / "manifest.json")
        latest = manifest.get("latest")
        record = next((v for v in manifest.get("versions") or [] if v.get("id") == latest), None)
        if not record:
            log(f"manifest.latest={latest!r} 在 versions 里找不到，终止")
            return 1
        cache = CACHE_ROOT / str(latest).replace("@", "_")
        cache.mkdir(parents=True, exist_ok=True)
        if allowed_network:
            (CACHE_ROOT).mkdir(parents=True, exist_ok=True)
            (CACHE_ROOT / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8"
            )
        log(f"最新版本：{latest}（{record.get('publishedAt')}）")
        log(f"表目录：{record.get('tableCfgPath')}")

        tables = prepare_tables(
            client,
            str(latest),
            str(record.get("tableCfgPath")),
            cache,
            refresh=args.refresh,
            allowed_network=allowed_network,
        )
        index = load_asset_index(client, cache, refresh=args.refresh, allowed_network=allowed_network)
        json_files = (((index or {}).get("datasets") or {}).get("json") or {}).get("files") or {}
        log(f"资产索引：图片 {len(_canonical_images(index))} 个 / Json {len(json_files)} 个")

        growth = tables["CharGrowthTable.json"]
        wanted = plan_skill_downloads(growth)
        planned_bytes = sum(
            (json_files.get(f"SkillData/{sid}.json") or {}).get("size") or 0
            for _, _, sid in wanted
        )
        log(f"技能数据计划：{len(wanted)} 个文件，约 {planned_bytes / 1048576:.1f} MB")
        skill_data, downloaded = download_skill_data(
            client, str(latest), cache, json_files, wanted,
            refresh=args.refresh, allowed_network=allowed_network,
        )
        log(f"技能数据就绪：{len(skill_data)} 个（本次下载 {downloaded / 1048576:.1f} MB）")

        payload = build_payload(
            manifest=manifest,
            version_record=record,
            char_table=tables["CharacterTable.json"],
            profession_table=tables["CharProfessionTable.json"],
            type_table=tables["CharTypeTable.json"],
            growth_table=growth,
            i18n=tables["I18nTextTable_CN.json"],
            skill_data=skill_data,
            index=index,
            extras_path=Path(args.extras),
            limit=args.limit,
        )
        errors = validate_payload(payload)
        if errors:
            log("数据校验失败：")
            for error in errors:
                log(f"  - {error}")
            return 1
        size = payload_size(payload)
        meta = payload["meta"]
        log(
            f"干员 {meta['operator_count']} 名（唯一名字 {meta['unique_name_count']}）"
            f"，技能 {meta['skill_count']} 条，operators.json 约 {size / 1024:.1f} KB"
        )
        if size > MAX_OUTPUT_BYTES:
            log(f"警告：operators.json 超过 {MAX_OUTPUT_BYTES / 1024:.0f}KB 上限")

        game_meta = {
            "schema": "endbox.game_meta/1",
            "game_version": meta["game_version"],
            "revision": meta["revision"],
            "hotfix_version": meta["hotfix_version"],
            "table_cfg_path": meta["table_cfg_path"],
            "table_published_at": meta["table_published_at"],
            "asset_index_revision": (index or {}).get("revision"),
            "asset_index_updated_at": (index or {}).get("updatedAt"),
            "generated_at": meta["generated_at"],
            "generator": "tools/fetch_game_data.py",
            "operator_count": meta["operator_count"],
            "skill_count": meta["skill_count"],
            "sources": meta["sources"],
            "license": LICENSE,
            "usage": "仅 EndBox 本地工具使用：数据/素材版权归鹰角网络，不重新分发；"
                     "阵营/词缀/简介来自 fz.wiki（CC BY-NC-SA 4.0）。",
            "material_layout": {
                "read_priority": ["素材/", "assets/gamedata/"],
                "operator_dir": "operators/<中文名>/",
                "avatar": "operators/<中文名>/avatar.png",
                "portrait": "operators/<中文名>/portrait.png",
                "skills": "operators/<中文名>/skills/{attack,skill,link,ult}.png",
                "class_icon": "classes/<职业>.png",
                "element_icon": "elements/<属性>.png",
                "note": "operators.json 的 avatar/portrait 是相对「素材根」的路径；"
                        "素材/ 为用户原素材（只读来源），assets/gamedata/ 为同步脚本补缺目录（gitignored）。",
            },
            "fetch_command": "python tools/fetch_game_data.py",
            "sync_command": "python tools/sync_game_data.py",
        }
        if args.dry_run:
            log(f"[dry-run] 将写入 {args.output}（{size} 字节）")
            log(f"[dry-run] 将写入 {args.meta_output}")
            return 0
        output = Path(args.output)
        write_json_atomic(output, payload)
        write_json_atomic(Path(args.meta_output), game_meta)
        log(f"已写入 {output}（{output.stat().st_size} 字节）")
        log(f"已写入 {args.meta_output}")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 失败给出人话
        log(f"失败：{type(exc).__name__}: {exc}")
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
