# -*- coding: utf-8 -*-
"""一键同步《明日方舟：终末地》干员素材（头像/技能图标/可选立绘）到本地缓存目录。

数据来自 assets/data/operators.json（由 tools/fetch_game_data.py 生成），
图片路径与 size/md5 来自数据站的 asset-sync-index.json（缓存在目标目录 .cache/ 下）。

默认落点 ``assets/gamedata/``（.gitignore 已忽略：本地缓存，不进仓库/安装包）：

    python tools/sync_game_data.py                  # 头像 + 技能图标 + 职业/属性图标（缺什么补什么）
    python tools/sync_game_data.py --only-avatars   # 只补头像
    python tools/sync_game_data.py --only-skills    # 只补技能图标
    python tools/sync_game_data.py --portraits      # 额外下载立绘（4–6MB/张）并缩放到 512px 存 portrait.png
    python tools/sync_game_data.py --dry-run        # 只计算计划，不下载
    python tools/sync_game_data.py --force          # 覆盖已存在的文件
    python tools/sync_game_data.py --limit 5        # 只处理前 5 名（调试）
    python tools/sync_game_data.py --version 1.6.4  # 校验数据版本（和 operators.json 不一致就退出）
    python tools/sync_game_data.py --jobs 6 --retries 4 --timeout 90
    python tools/sync_game_data.py --no-network     # 不联网：只校验已有文件、报缺失
    python tools/sync_game_data.py --target 素材    # 有完全权限时直接写用户原素材目录（旧布局）

落盘布局（``<目标>/`` = assets/gamedata（默认）或 素材（旧布局））：

    <目标>/operators/<中文名>/avatar.png       干员头像（charicon/icon_chr_*.png，约 75KB；已存在则跳过）
    <目标>/operators/<中文名>/avatar_m.png     同名角色（管理员有男/女两行）的变体头像
    <目标>/operators/<中文名>/skills/attack.png    普攻图标
    <目标>/operators/<中文名>/skills/skill.png     战技图标
    <目标>/operators/<中文名>/skills/link.png      连携技图标
    <目标>/operators/<中文名>/skills/ult.png       终结技图标
    <目标>/operators/<中文名>/portrait.png     立绘缩略图（--portraits，512px 长边）
    <目标>/operators/<中文名>/class.txt        职业（仅在新建目录时写，不动已有文件）
    <目标>/operators/<中文名>/element.txt      属性（同上）
    <目标>/classes/<职业>.png                  职业图标（缺才补；游戏原名「术师」，与旧素材「术士」并存）
    <目标>/elements/<属性>.png                 属性图标（缺才补）
    <目标>/.cache/asset-sync-index.json        21MB 资产索引缓存（gitignored）

    --target 素材 时额外写旧协议兼容文件：
        <目标>/operators/<中文名>/icon.png、skill.png、link.png、ult.png

程序读取优先级：``素材/``（用户原素材，只读来源）优先，``assets/gamedata/`` 补缺；
两边共用同一套「运行时素材根」相对路径（operators.json 的 avatar/portrait 字段）。
游戏数据里重击（power_attack）没有独立图标，所以不写 skills/heavy.png，不伪造。

可重复运行：已存在且大小对得上的文件直接跳过；下载写 ``.part`` 再原子替换；
失败会重试并汇总，不中断其它文件。版权：游戏素材版权归上海鹰角网络科技有限公司，
只在本地工具里使用，不重新分发。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://data.akedata.wiki"
MANIFEST_URL = f"{SITE}/manifest.json"
INDEX_URL = f"{SITE}/asset-sync-index.json"
UA = "EndBox-AssetSync/1.0 (+local personal use only)"
DEFAULT_TARGET = ROOT / "assets" / "gamedata"
LEGACY_TARGET_NAMES = {"素材"}
OPERATORS_JSON = ROOT / "assets" / "data" / "operators.json"
GAME_META_JSON = ROOT / "assets" / "data" / "game_meta.json"

IMAGE_SPRITES = "assets/beyond/dynamicassets/gameplay/ui/sprites/"
# 属性枚举 → 属性图标名（CharTypeTable.icon 的取值，交叉核对过）
ELEMENT_ICON_BY_ID = {
    "Physical": "icon_charattrtype_physical",
    "Fire": "icon_charattrtype_fire",
    "Cryst": "icon_charattrtype_cold",
    "Pulse": "icon_charattrtype_pulse",
    "Natural": "icon_charattrtype_nature",
}
SKILL_KINDS = ("attack", "skill", "link", "ult")
PORTRAIT_MAX_SIDE = 512
INVALID_DIR_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def log(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("gbk", "replace").decode("gbk", "replace"), flush=True)


def safe_dirname(name: str) -> str:
    """目录名清洗：去掉 Windows 非法字符；中文名原样保留。"""
    cleaned = INVALID_DIR_CHARS.sub("_", str(name or "")).strip().strip(".")
    return cleaned or "unknown"


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text("utf-8"))


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(text, "utf-8", newline="\n")
    os.replace(temp, path)


def file_matches(path: Path, expected_size: int | None) -> bool:
    """已存在且大小对得上 → 视为已完成（断点续传）。"""
    path = Path(path)
    if not path.is_file():
        return False
    if expected_size is None:
        return path.stat().st_size > 0
    try:
        return path.stat().st_size == expected_size
    except OSError:
        return False


def file_url(dataset_root: str, rel_path: str) -> str:
    encoded = "/".join(urllib.parse.quote(part, safe="@+$,;=!*'()~-._") for part in rel_path.split("/"))
    return f"{SITE}/{dataset_root}/{encoded}"


def is_legacy_target(target: Path) -> bool:
    """目标目录叫「素材」时按旧布局多写兼容文件（用户有自己的 素材/ 时）。"""
    return target.name in LEGACY_TARGET_NAMES


# ---------------------------------------------------------------------------
# 素材索引
# ---------------------------------------------------------------------------
def index_datasets(index: dict) -> tuple[dict, str]:
    datasets = (index or {}).get("datasets") or {}
    images = datasets.get("images") or {}
    return images.get("files") or {}, images.get("root") or "public/images"


def image_lookup(index: dict, folder: str) -> dict[str, tuple[str, dict]]:
    """``folder`` 下的规范图片：名字（无 .png）→ (索引相对路径, 文件记录)。"""
    files, _ = index_datasets(index)
    prefix = f"{IMAGE_SPRITES}{folder}/"
    bucket: dict[str, tuple[str, dict]] = {}
    for path, record in files.items():
        if not path.startswith(prefix) or not path.lower().endswith(".png"):
            continue
        name = path[len(prefix):]
        if "/" in name:
            continue
        bucket[name[:-4]] = (path, record if isinstance(record, dict) else {})
    return bucket


def load_index(client, target: Path, *, refresh: bool, allowed_network: bool) -> dict:
    cache = target / ".cache" / "asset-sync-index.json"
    if cache.is_file() and not refresh:
        try:
            return read_json(cache)
        except Exception as exc:  # noqa: BLE001 - 缓存坏了就重下
            log(f"资产索引缓存损坏，重新下载：{exc}")
    if not allowed_network:
        raise FileNotFoundError(f"离线模式缺少资产索引缓存：{cache}")
    import httpx

    log("下载资产索引 asset-sync-index.json（约 21MB，之后走缓存）…")
    with httpx.Client(verify=False, timeout=120, follow_redirects=True,
                      headers={"User-Agent": UA}) as http:
        response = http.get(INDEX_URL)
    if response.status_code != 200:
        raise RuntimeError(f"资产索引下载失败：HTTP {response.status_code}")
    text = response.text
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, "utf-8")
    log(f"资产索引已缓存：{cache}（{len(response.content) / 1048576:.1f} MB）")
    return json.loads(text)


def manifest_latest(client) -> tuple[str | None, str | None]:
    try:
        response = client.get(MANIFEST_URL)
        if response.status_code != 200:
            return None, None
        data = response.json()
        return data.get("latest"), data.get("updatedAt")
    except Exception as exc:  # noqa: BLE001 - 版本检查失败不阻塞素材同步
        log(f"（版本检查跳过：{exc}）")
        return None, None


# ---------------------------------------------------------------------------
# 下载计划
# ---------------------------------------------------------------------------
def operator_directory(operator: dict) -> str:
    """干员素材目录名：优先用 operators.json 里 avatar 的相对路径，其次中文名。"""
    avatar = operator.get("avatar")
    if avatar:
        parts = [part for part in str(avatar).replace("\\", "/").split("/") if part]
        if len(parts) >= 2:
            return safe_dirname(parts[-2])
    return safe_dirname(operator.get("name") or operator.get("id") or "unknown")


def plan_tasks(
    operators: list[dict],
    index: dict,
    target: Path,
    *,
    include_avatars: bool = True,
    include_skills: bool = True,
    include_portraits: bool = False,
    include_icons: bool = True,
    force: bool = False,
    limit: int = 0,
) -> list[dict]:
    """纯函数：算出一批下载任务（不联网，可单测）。

    任务：``{kind, name, url_path, root, dest, expected_size, md5, note}``；
    ``dest`` 是目标素材根下的相对路径，交由调用方拼绝对路径。
    """
    _, dataset_root = index_datasets(index)
    charicons = image_lookup(index, "charicon")
    portraits = image_lookup(index, "characterportrait")
    skillicons = image_lookup(index, "skillicon")
    profession_icons = image_lookup(index, "charprofessionicon")
    element_icons = image_lookup(index, "elementicon")
    legacy = is_legacy_target(target)

    tasks: list[dict] = []
    used_dirs: dict[str, int] = {}
    rows = operators[:limit] if limit and limit > 0 else operators
    for operator in rows:
        name = operator.get("name")
        operator_id = operator.get("id")
        if not name or not operator_id:
            continue
        directory = operator_directory(operator)
        variant = operator.get("variant")
        occurrences = used_dirs.get(directory, 0)
        used_dirs[directory] = occurrences + 1

        if include_avatars:
            rel = f"operators/{directory}/"
            icon_hit = charicons.get(f"icon_{operator_id}")
            if icon_hit and occurrences == 0:
                source_rel, record = icon_hit
                expected = record.get("size")
                tasks.append({
                    "kind": "avatar", "name": name, "url_path": source_rel, "root": dataset_root,
                    "dest": rel + "avatar.png", "expected_size": expected,
                    "md5": record.get("md5"), "note": directory,
                })
                if legacy:
                    tasks.append({
                        "kind": "avatar-icon", "name": name, "url_path": source_rel,
                        "root": dataset_root, "dest": rel + "icon.png",
                        "expected_size": expected, "md5": record.get("md5"), "note": directory,
                    })
            elif icon_hit and variant:
                source_rel, record = icon_hit
                expected = record.get("size")
                tasks.append({
                    "kind": f"avatar-{variant}", "name": name, "url_path": source_rel,
                    "root": dataset_root, "dest": f"{rel}avatar_{variant}.png",
                    "expected_size": expected, "md5": record.get("md5"), "note": directory,
                })
                if legacy:
                    tasks.append({
                        "kind": f"avatar-icon-{variant}", "name": name, "url_path": source_rel,
                        "root": dataset_root, "dest": f"{rel}icon_{variant}.png",
                        "expected_size": expected, "md5": record.get("md5"), "note": directory,
                    })

        if include_skills:
            for skill in operator.get("skills") or []:
                icon = skill.get("icon")
                kind = skill.get("kind")
                if not icon or kind not in SKILL_KINDS:
                    continue
                hit = skillicons.get(icon)
                if not hit:
                    continue
                source_rel, record = hit
                expected = record.get("size")
                tasks.append({
                    "kind": f"skill-{kind}", "name": name, "url_path": source_rel,
                    "root": dataset_root, "dest": f"operators/{directory}/skills/{kind}.png",
                    "expected_size": expected, "md5": record.get("md5"), "note": directory,
                })
                # 旧素材协议：素材/operators/<名>/{skill,link,ult}.png（assets.py 读这三个）
                if legacy and kind in {"skill", "link", "ult"}:
                    tasks.append({
                        "kind": f"compat-{kind}", "name": name, "url_path": source_rel,
                        "root": dataset_root, "dest": f"operators/{directory}/{kind}.png",
                        "expected_size": expected, "md5": record.get("md5"), "note": directory,
                    })

        if include_portraits:
            hit = portraits.get(str(operator_id))
            if hit:
                source_rel, record = hit
                tasks.append({
                    "kind": "portrait", "name": name, "url_path": source_rel,
                    "root": dataset_root,
                    "dest": f"operators/{directory}/portrait.png",
                    "expected_size": None, "md5": record.get("md5"),
                    "note": directory, "resize": PORTRAIT_MAX_SIDE,
                })

        if include_icons:
            profession_icon = operator.get("profession_icon")
            profession = operator.get("profession")
            if profession_icon and profession:
                hit = profession_icons.get(profession_icon)
                if hit:
                    source_rel, record = hit
                    tasks.append({
                        "kind": "profession-icon", "name": profession, "url_path": source_rel,
                        "root": dataset_root,
                        "dest": f"classes/{safe_dirname(profession)}.png",
                        "expected_size": record.get("size"), "md5": record.get("md5"),
                        "note": "classes",
                    })
            element_icon = ELEMENT_ICON_BY_ID.get(str(operator.get("element_id")))
            element = operator.get("element")
            if element_icon and element:
                hit = element_icons.get(element_icon)
                if hit:
                    source_rel, record = hit
                    tasks.append({
                        "kind": "element-icon", "name": element, "url_path": source_rel,
                        "root": dataset_root,
                        "dest": f"elements/{safe_dirname(element)}.png",
                        "expected_size": record.get("size"), "md5": record.get("md5"),
                        "note": "elements",
                    })
    # 已存在且大小对得上的文件不再下载（--force 全量覆盖）；同一落盘路径只保留一个任务
    deduped: dict[str, dict] = {}
    for task in tasks:
        if not force and file_matches(target / task["dest"], task.get("expected_size")):
            continue
        deduped.setdefault(str(task["dest"]).lower(), task)
    return list(deduped.values())


def plan_text_files(operators: list[dict], target: Path, *, limit: int = 0) -> list[dict]:
    """为**新建**的干员目录准备 class.txt / element.txt（已有目录不覆盖）。"""
    plans: list[dict] = []
    rows = operators[:limit] if limit and limit > 0 else operators
    seen: set[str] = set()
    for operator in rows:
        name = operator.get("name")
        if not name:
            continue
        directory = operator_directory(operator)
        if directory in seen:
            continue
        seen.add(directory)
        for filename, value in (("class.txt", operator.get("profession")),
                                ("element.txt", operator.get("element"))):
            if value:
                plans.append({
                    "dest": f"operators/{directory}/{filename}",
                    "text": str(value) + "\n",
                    "name": name,
                })
    return plans


# ---------------------------------------------------------------------------
# 下载执行
# ---------------------------------------------------------------------------
def make_client():
    import httpx

    return httpx.Client(
        verify=False,  # 本机 MITM 代理需要；数据站是纯静态公开资源
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": UA},
    )


def download_one(client, task: dict, target: Path, *, retries: int, timeout: float) -> tuple[dict, int]:
    """下载单个任务，返回 (task, 落盘字节数)。失败抛异常给调用方汇总。"""
    url = file_url(task["root"], task["url_path"])
    dest = target / task["dest"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        temp = dest.with_name(dest.name + ".part")
        try:
            with client.stream("GET", url, timeout=timeout) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                size = 0
                digest = hashlib.md5()
                with temp.open("wb") as handle:
                    for chunk in response.iter_bytes(64 * 1024):
                        handle.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            expected = task.get("expected_size")
            if expected is not None and size != expected:
                raise RuntimeError(f"大小不符（{size} != {expected}）")
            expected_md5 = task.get("md5")
            if expected_md5 and digest.hexdigest() != expected_md5:
                raise RuntimeError("md5 不符")
            if task.get("resize"):
                size = resize_png(temp, int(task["resize"]))
            os.replace(temp, dest)
            return task, size
        except Exception as exc:  # noqa: BLE001 - 重试后交给汇总
            temp.unlink(missing_ok=True)
            last = exc
            if attempt < retries:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"{task.get('name')} {task.get('kind')}: {last}")


def resize_png(path: Path, max_side: int) -> int:
    """立绘缩略图：长边缩到 max_side（Pillow 已随项目依赖）。"""
    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGBA")
        width, height = image.size
        scale = min(1.0, max_side / max(width, height))
        if scale < 1.0:
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
        image.save(path, "PNG", optimize=True)
    return path.stat().st_size


def run_tasks(tasks: list[dict], target: Path, *, jobs: int, retries: int, timeout: float,
              dry_run: bool, no_network: bool, force: bool = False,
              progress_every: int = 25) -> tuple[int, int, list[str], list[str]]:
    """执行下载；返回 (字节数, 成功数, 失败列表, 缺失列表)。"""
    if dry_run or no_network:
        if force:
            missing = list(tasks)
        else:
            missing = [
                task for task in tasks
                if not file_matches(target / task["dest"], task.get("expected_size"))
            ]
        planned = sum(task.get("expected_size") or 0 for task in missing)
        if dry_run:
            log(f"[dry-run] 计划下载 {len(missing)} 个文件，约 {planned / 1048576:.1f} MB（任务总数 {len(tasks)}）")
        else:
            log(f"[no-network] 缺失/大小不符 {len(missing)} 个文件")
        for task in missing[:20]:
            log(f"  缺 {target / task['dest']}")
        return 0, 0, [], [str(target / task["dest"]) for task in missing]

    total_bytes = 0
    ok = 0
    failures: list[str] = []
    done = 0
    total = len(tasks)
    if not total:
        return 0, 0, [], []
    client = make_client()
    try:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = {pool.submit(download_one, client, task, target,
                                   retries=retries, timeout=timeout): task
                       for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    _, size = future.result()
                except Exception as exc:  # noqa: BLE001 - 单个失败不中断
                    failures.append(f"{task['dest']}: {exc}")
                else:
                    total_bytes += size
                    ok += 1
                done += 1
                if progress_every and (done % progress_every == 0 or done == total):
                    log(f"  进度 {done}/{total}（成功 {ok}，失败 {len(failures)}，"
                        f"{total_bytes / 1048576:.1f} MB）")
    finally:
        client.close()
    return total_bytes, ok, failures, []


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def load_operators(path: Path = OPERATORS_JSON) -> tuple[dict, list[dict]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("operators"), list):
        raise ValueError(f"{path} 结构不对：需要 {{meta, operators}}")
    return payload.get("meta") or {}, payload["operators"]


def check_version(meta: dict, expected: str | None, latest: str | None) -> str | None:
    """版本校验；返回错误/提示文本，或 None。"""
    current = str(meta.get("game_version") or "")
    if expected and current and expected != current:
        return (f"operators.json 是 {current} 版数据，期望 {expected}。"
                f"先运行 python tools/fetch_game_data.py 更新数据。")
    if latest and current:
        latest_version = str(latest).split("@")[0]
        if latest_version and latest_version != current:
            return (f"数据站已更新到 {latest_version}（本地 {current}）。"
                    f"可先运行 python tools/fetch_game_data.py 再同步素材。")
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="同步终末地干员素材（头像/技能图标/可选立绘）到本地缓存目录（默认 assets/gamedata/）"
    )
    parser.add_argument("--only-avatars", action="store_true", help="只同步干员头像")
    parser.add_argument("--only-skills", action="store_true", help="只同步技能图标")
    parser.add_argument("--portraits", action="store_true", help="额外下载立绘并缩到 512px（每张 4–6MB）")
    parser.add_argument("--no-icons", action="store_true", help="不同步职业/属性图标")
    parser.add_argument("--version", default=None, help="期望的游戏版本（如 1.6.4），不一致则退出")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 名干员（调试）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    parser.add_argument("--dry-run", action="store_true", help="只打印下载计划，不下载")
    parser.add_argument("--no-network", action="store_true", help="不联网：只校验已有素材是否齐全")
    parser.add_argument("--refresh-index", action="store_true", help="忽略缓存重新下载资产索引")
    parser.add_argument("--jobs", type=int, default=6, help="并发下载数（默认 6）")
    parser.add_argument("--retries", type=int, default=3, help="单个文件失败重试次数（默认 3）")
    parser.add_argument("--timeout", type=float, default=60.0, help="单请求超时秒数（默认 60）")
    parser.add_argument("--target", "--assets-root", dest="target", default=str(DEFAULT_TARGET),
                        help="素材落点（默认 assets/gamedata；传 素材 则写用户原素材目录）")
    parser.add_argument("--operators", default=str(OPERATORS_JSON), help="operators.json 路径")
    parser.add_argument("--skip-text", action="store_true", help="不为新目录写 class.txt/element.txt")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.only_avatars and args.only_skills:
        log("--only-avatars 与 --only-skills 不能同时用（要全量就别加参数）")
        return 2
    target = Path(args.target)
    operators_path = Path(args.operators)
    if not operators_path.is_file():
        log(f"找不到 {operators_path}，先运行 python tools/fetch_game_data.py 生成干员数据")
        return 1
    meta, operators = load_operators(operators_path)
    legacy = is_legacy_target(target)
    log(f"干员数据：{operators_path}（版本 {meta.get('game_version')}，"
        f"{len(operators)} 名干员 / {meta.get('skill_count')} 条技能）")
    log(f"落点：{target}（{'旧素材布局' if legacy else 'gamedata 布局'}）")

    allowed_network = not args.no_network
    client = make_client() if allowed_network else None
    try:
        latest = None
        if allowed_network:
            latest, latest_at = manifest_latest(client)
            if latest:
                log(f"数据站最新版本：{latest}（索引更新 {latest_at}）")
        problem = check_version(meta, args.version, latest)
        if problem:
            log(problem)
            return 1 if args.version else 0

        index = load_index(client, target, refresh=args.refresh_index, allowed_network=allowed_network)
        log(f"资产索引：{len(((index.get('datasets') or {}).get('images') or {}).get('files') or {})} 张图片")

        include_avatars = not args.only_skills
        include_skills = not args.only_avatars
        include_icons = not args.no_icons and not args.only_avatars
        tasks = plan_tasks(
            operators, index, target,
            include_avatars=include_avatars,
            include_skills=include_skills,
            include_portraits=args.portraits and not args.only_skills,
            include_icons=include_icons,
            force=args.force,
            limit=args.limit,
        )
        text_plans = [] if args.skip_text else plan_text_files(operators, target, limit=args.limit)
        kinds: dict[str, int] = {}
        for task in tasks:
            kinds[task["kind"]] = kinds.get(task["kind"], 0) + 1
        log(f"计划任务 {len(tasks)} 个：{kinds or '（无需下载）'}")
        if not args.dry_run and not args.no_network:
            written = 0
            for plan in text_plans:
                dest = target / plan["dest"]
                if not dest.is_file():
                    write_text_atomic(dest, plan["text"])
                    written += 1
            if written:
                log(f"新建目录文本 {written} 个（class.txt / element.txt）")
        total_bytes, ok, failures, missing = run_tasks(
            tasks, target, jobs=args.jobs, retries=args.retries, timeout=args.timeout,
            dry_run=args.dry_run, no_network=args.no_network, force=args.force,
        )
        log(f"完成：成功 {ok} 个文件，{total_bytes / 1048576:.1f} MB；失败 {len(failures)} 个")
        for failure in failures[:15]:
            log(f"  ! {failure}")
        if args.no_network and missing:
            return 1
        if failures:
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 失败给出人话
        log(f"失败：{type(exc).__name__}: {exc}")
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
