# -*- coding: utf-8 -*-
"""终末地游戏数据管道测试（**不联网**）。

覆盖：
- assets/data/operators.json：编码、数量、字段完整性、路径相对性、技能种类/时长、重复名处理、体积上限；
- assets/data/game_meta.json：版本/revision/来源/版权/素材布局；
- assets/data/operators_extra.json：编码完好、结构（fix 要求里要确认的部分）；
- tools/fetch_game_data.py：纯函数（文本解析、技能组解析、payload 组装、校验器）用小型 fixture 离线跑；
- tools/sync_game_data.py：下载计划纯函数（落盘路径、gamedata/旧布局差异、去重、断点跳过），不下载。

所有测试只读仓库里已入库的文件或临时目录，不访问 network（httpx 只在 main() 里延迟导入）。
"""
from __future__ import annotations

import json

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
DATA = ROOT / "assets" / "data"
OPERATORS = DATA / "operators.json"
GAME_META = DATA / "game_meta.json"
EXTRAS = DATA / "operators_extra.json"

KINDS = {"attack", "skill", "link", "ult", "heavy"}
TYPE_BY_KIND = {"attack": "普攻", "skill": "战技", "link": "连携技", "ult": "终结技"}


# ---------------------------------------------------------------------------
# 载入工具
# ---------------------------------------------------------------------------
def load_operators() -> dict:
    return json.loads(OPERATORS.read_text("utf-8"))


def load_meta() -> dict:
    return json.loads(GAME_META.read_text("utf-8"))


def operator_rows() -> list[dict]:
    return load_operators()["operators"]


# ---------------------------------------------------------------------------
# operators.json
# ---------------------------------------------------------------------------
def test_operators_json_is_utf8_and_parses():
    raw = OPERATORS.read_bytes()
    assert raw, "operators.json 为空"
    text = raw.decode("utf-8")  # 编码坏了这里会炸
    payload = json.loads(text)
    assert set(payload) == {"meta", "operators"}
    assert isinstance(payload["operators"], list)


def test_operators_json_size_under_limit():
    assert OPERATORS.stat().st_size < 400 * 1024


def test_operator_count_and_unique_names():
    rows = operator_rows()
    assert len(rows) == 36, "1.6.4 共 36 行角色数据（含管理员 2 个形态与 1 个无素材行）"
    names = [row["name"] for row in rows]
    assert len(set(names)) == 34
    # 只有「管理员」重复，且有男/女 variant 区分 + 一个无素材行
    duplicated = {name for name in names if names.count(name) > 1}
    assert duplicated == {"管理员"}
    admin = [row for row in rows if row["name"] == "管理员"]
    assert {row["variant"] for row in admin} == {"m", "f", None}
    assert any(row["has_assets"] is False for row in admin)


def test_operator_fields_complete():
    for row in operator_rows():
        op_id = row["id"]
        assert op_id.startswith("chr_"), op_id
        assert isinstance(row["name"], str) and row["name"].strip(), op_id
        assert isinstance(row["name_en"], str) and row["name_en"].strip(), op_id
        assert isinstance(row["rarity"], int) and 1 <= row["rarity"] <= 6, op_id
        assert row["profession"] and row["profession_id"] in {0, 2, 4, 5, 7, 8}, op_id
        assert row["profession_icon"].startswith("icon_profession_"), op_id
        assert row["element"] in {"物理", "灼热", "寒冷", "电磁", "自然"}, op_id
        assert row["element_id"] in {"Physical", "Fire", "Cryst", "Pulse", "Natural"}, op_id
        assert row["weapon"] in {"单手剑", "双手剑", "长柄武器", "施术单元", "手铳"}, op_id
        assert isinstance(row["tags"], list), op_id
        assert isinstance(row["has_assets"], bool), op_id
        assert row["department"] == "ENDFIELD INDUSTRIES", op_id


def test_operator_avatar_paths_are_relative():
    """Lead 约定：不能写死本机绝对路径；avatars 是「运行时素材根」相对路径。"""
    rows = operator_rows()
    with_avatar = [row for row in rows if row["avatar"]]
    # 35 行有头像（34 个唯一干员目录 + 管理员男/女两行共用「管理员」目录），
    # chr_9000_endmin 这一行在资产索引里没有头像/立绘，所以 avatar/portrait 是 null。
    assert len(with_avatar) == 35
    assert next(row for row in rows if row["id"] == "chr_9000_endmin")["avatar"] is None
    for row in with_avatar:
        avatar = row["avatar"].replace("\\", "/")
        assert not avatar.startswith("/")
        assert ":" not in avatar, f"{row['id']} 写死了盘符：{avatar}"
        assert avatar == f"operators/{row['name']}/avatar.png"
        assert row["portrait"] == f"operators/{row['name']}/portrait.png"
    assert "assets" not in rows[0], "数据源图片路径字段已移除，避免和历史字段混淆"


def test_every_operator_has_four_skill_groups_and_heavy():
    for row in operator_rows():
        kinds = [skill["kind"] for skill in row["skills"]]
        assert len(kinds) == 5, (row["id"], kinds)
        assert set(kinds) == KINDS, (row["id"], kinds)
        assert kinds[:4] == ["attack", "skill", "link", "ult"], "技能顺序按游戏技能栏"


def test_skill_fields_and_durations():
    for row in operator_rows():
        for skill in row["skills"]:
            assert skill["id"].startswith("chr_"), (row["id"], skill)
            assert isinstance(skill["skill_ids"], list) and skill["skill_ids"], (row["id"], skill)
            assert skill["id"] in skill["skill_ids"]
            if skill["kind"] == "heavy":
                # 重击在游戏数据里没有名字/图标，只有时长：不能编造
                assert skill["name"] is None
                assert skill["type"] is None
                assert skill["icon"] is None
            else:
                assert skill["type"] == TYPE_BY_KIND[skill["kind"]], (row["id"], skill)
                assert isinstance(skill["name"], str) and skill["name"], (row["id"], skill)
                assert skill["icon"] and skill["icon"].startswith("icon_"), (row["id"], skill)
            assert isinstance(skill["duration"], (int, float)) and skill["duration"] > 0
            assert isinstance(skill["duration_frame"], int) and skill["duration_frame"] > 0
            # duration = durationFrame / 30（社区前端换算规则）
            assert skill["duration"] == pytest.approx(skill["duration_frame"] / 30, abs=6e-4)


def test_attack_and_heavy_durations_are_real():
    for row in operator_rows():
        attack = next(skill for skill in row["skills"] if skill["kind"] == "attack")
        heavy = next(skill for skill in row["skills"] if skill["kind"] == "heavy")
        assert heavy["duration_frame"] > 0
        assert attack["duration_frame"] > 0
        assert heavy["duration"] != attack["duration"] or heavy["id"] != attack["id"]


def test_meta_matches_file():
    payload = load_operators()
    meta = payload["meta"]
    assert meta["schema"] == "endbox.operators/1"
    assert meta["game_version"] == "1.6.4"
    assert meta["revision"] == "1.6.4@11775769-12"
    assert meta["operator_count"] == len(payload["operators"])
    assert meta["skill_count"] == sum(len(row["skills"]) for row in payload["operators"])
    assert meta["unique_name_count"] == 34
    assert meta["sources"]["game_data"]["site"] == "https://data.akedata.wiki"
    assert "鹰角" in meta["license"] and "不再分发" in meta["license"]


# ---------------------------------------------------------------------------
# game_meta.json
# ---------------------------------------------------------------------------
def test_game_meta_consistency_and_license():
    meta = load_meta()
    payload_meta = load_operators()["meta"]
    assert meta["schema"] == "endbox.game_meta/1"
    for key in ("game_version", "revision", "hotfix_version", "table_cfg_path"):
        assert meta[key] == payload_meta[key]
    assert meta["operator_count"] == payload_meta["operator_count"]
    assert meta["skill_count"] == payload_meta["skill_count"]
    assert "鹰角" in meta["license"]
    assert "CC BY-NC-SA" in meta["sources"]["extras"]["license"]


def test_game_meta_material_layout_contract():
    layout = load_meta()["material_layout"]
    assert layout["read_priority"] == ["素材/", "assets/gamedata/"]
    assert layout["avatar"] == "operators/<中文名>/avatar.png"
    assert layout["skills"] == "operators/<中文名>/skills/{attack,skill,link,ult}.png"
    assert layout["class_icon"] == "classes/<职业>.png"
    assert layout["element_icon"] == "elements/<属性>.png"


# ---------------------------------------------------------------------------
# operators_extra.json（fz.wiki 快照）——确认编码与结构没有损坏
# ---------------------------------------------------------------------------
def test_operators_extra_encoding_and_structure():
    raw = EXTRAS.read_bytes()
    text = raw.decode("utf-8")  # 编码损坏会在这里失败
    assert not raw.startswith(b"\xef\xbb\xbf"), "应该是无 BOM 的 UTF-8"
    payload = json.loads(text)
    assert set(payload) >= {"meta", "operators"}
    operators = payload["operators"]
    assert len(operators) == 35
    meta = payload["meta"]
    assert meta["operator_count"] == len(operators)
    assert meta["source"] == "https://www.fz.wiki"
    assert "CC BY-NC-SA" in meta["license"]
    for name, data in operators.items():
        assert isinstance(name, str) and name
        assert isinstance(data.get("profession"), (str, type(None)))
        assert isinstance(data.get("tags"), list)
        assert isinstance(data.get("bio"), (str, type(None)))


def test_extras_cover_most_operators():
    extras = json.loads(EXTRAS.read_text("utf-8"))["operators"]
    rows = operator_rows()
    matched = [row for row in rows if row.get("extra_key") in extras]
    assert len(matched) >= 34, "除无素材行外都应该在 fz.wiki 快照里有对应记录"
    for row in matched:
        if row["name"] != "管理员" or row["variant"]:
            assert row["faction"], row["id"]
            assert row["bio"], row["id"]


# ---------------------------------------------------------------------------
# tools/fetch_game_data.py（离线纯函数）
# ---------------------------------------------------------------------------
def _fetch_module():
    import importlib

    return importlib.import_module("tools.fetch_game_data")


def test_fetch_module_imports_without_network():
    module = _fetch_module()
    assert module.SITE == "https://data.akedata.wiki"
    assert module.validate_payload(load_operators()) == []


def test_resolve_text_and_clean_rich_text():
    module = _fetch_module()
    i18n = {"123": "佩丽卡", "-9": "重击"}
    assert module.resolve_text(i18n, {"id": 123, "text": ""}) == "佩丽卡"
    assert module.resolve_text(i18n, {"id": -9, "text": "已内嵌"}) == "已内嵌"
    assert module.resolve_text(i18n, {"id": 404, "text": ""}) is None
    assert module.resolve_text(i18n, None) is None
    assert module.clean_rich_text("造成<@ba.pulse>电磁伤害</>") == "造成电磁伤害"
    assert module.clean_rich_text("") is None


def test_frame_to_seconds():
    module = _fetch_module()
    assert module.frame_to_seconds(30) == 1.0
    assert module.frame_to_seconds(155) == pytest.approx(5.167, abs=1e-3)
    assert module.frame_to_seconds(None) is None
    assert module.frame_to_seconds("bad") is None
    assert module.frame_to_seconds(-1) is None


def _fixture_tables():
    """最小可用的数据表 fixture（结构与数据站一致，值自造）。"""
    i18n = {
        "100": "测试干员",
        "101": "术师",
        "102": "电磁",
        "200": "测试战技",
        "201": "测试连携",
        "202": "测试终结",
        "203": "测试普攻",
        "300": "战技描述<@ba.pulse>标记</>",
        "301": "连携描述",
        "302": "终结描述",
        "303": "普攻描述",
    }
    char_table = {
        "chr_0001_test": {
            "charId": "chr_0001_test",
            "name": {"id": 100, "text": ""},
            "engName": "Tester",
            "rarity": 6,
            "profession": 5,
            "charTypeId": "Pulse",
            "weaponType": 2,
            "department": "ENDFIELD INDUSTRIES",
        }
    }
    profession_table = {"5": {"profession": 5, "name": {"id": 101, "text": ""},
                              "iconId": "icon_profession_5", "sortId": 2}}
    type_table = {
        "Pulse": {"charTypeId": "Pulse", "color": "FFC000", "damageType": 3,
                  "icon": "icon_charattrtype_pulse", "name": {"id": 102, "text": ""}}
    }
    growth_table = {
        "chr_0001_test": {
            "skillGroupMap": {
                "chr_0001_test_NormalAttack": {
                    "skillGroupType": 0, "icon": "icon_attack_sword",
                    "name": {"id": 203, "text": ""}, "desc": {"id": 303, "text": ""},
                    "skillIdList": ["chr_0001_test_attack1", "chr_0001_test_power_attack"],
                },
                "chr_0001_test_NormalSkill": {
                    "skillGroupType": 1, "icon": "icon_skill_test_01",
                    "name": {"id": 200, "text": ""}, "desc": {"id": 300, "text": ""},
                    "skillIdList": ["chr_0001_test_normal_skill"],
                },
                "chr_0001_test_ComboSkill": {
                    "skillGroupType": 3, "icon": "icon_combo_skill_test_01",
                    "name": {"id": 201, "text": ""}, "desc": {"id": 301, "text": ""},
                    "skillIdList": ["chr_0001_test_combo_skill"],
                },
                "chr_0001_test_UltimateSkill": {
                    "skillGroupType": 2, "icon": "icon_ultimate_skill_test_01",
                    "name": {"id": 202, "text": ""}, "desc": {"id": 302, "text": ""},
                    "skillIdList": ["chr_0001_test_ultimate_skill"],
                },
            }
        }
    }
    skill_data = {
        "chr_0001_test_attack1": {"durationFrame": 150, "castType": "Active"},
        "chr_0001_test_power_attack": {"durationFrame": 90, "castType": "Active"},
        "chr_0001_test_normal_skill": {"durationFrame": 120, "castType": "Active"},
        "chr_0001_test_combo_skill": {"durationFrame": 60, "castType": "Active"},
        "chr_0001_test_ultimate_skill": {"durationFrame": 240, "castType": "Active"},
    }
    prefix = "assets/beyond/dynamicassets/gameplay/ui/sprites/"
    index = {
        "datasets": {
            "images": {
                "root": "public/images",
                "files": {
                    prefix + "charicon/icon_chr_0001_test.png": {"size": 10, "md5": "a"},
                    prefix + "characterportrait/chr_0001_test.png": {"size": 20, "md5": "b"},
                    prefix + "skillicon/icon_skill_test_01.png": {"size": 1, "md5": "c"},
                    prefix + "skillicon/icon_combo_skill_test_01.png": {"size": 1, "md5": "d"},
                    prefix + "skillicon/icon_ultimate_skill_test_01.png": {"size": 1, "md5": "e"},
                    prefix + "skillicon/icon_attack_sword.png": {"size": 1, "md5": "f"},
                    "assets/i18nassets/main/[[jp]]/" + prefix
                    + "charicon/icon_chr_0001_test.png": {"size": 99, "md5": "x"},
                },
            },
            "json": {"root": "public/Json", "files": {}},
        }
    }
    return {
        "i18n": i18n,
        "char_table": char_table,
        "profession_table": profession_table,
        "type_table": type_table,
        "growth_table": growth_table,
        "skill_data": skill_data,
        "index": index,
    }


def test_build_payload_fixture_is_valid():
    module = _fetch_module()
    fixture = _fixture_tables()
    payload = module.build_payload(
        manifest={"latest": "1.6.4@1-1", "sharedRevision": "rev"},
        version_record={"id": "1.6.4@1-1", "gameVersion": "1.6.4", "hotfixVersion": "1-1",
                        "tableCfgPath": "public/1.6.4/1-1/TableCfg"},
        char_table=fixture["char_table"],
        profession_table=fixture["profession_table"],
        type_table=fixture["type_table"],
        growth_table=fixture["growth_table"],
        i18n=fixture["i18n"],
        skill_data=fixture["skill_data"],
        index=fixture["index"],
        extras_path=EXTRAS,
    )
    assert module.validate_payload(payload) == []
    operator = payload["operators"][0]
    assert operator["name"] == "测试干员"
    assert operator["profession"] == "术师"
    assert operator["element"] == "电磁"
    assert operator["element_color"] == "FFC000"
    assert operator["weapon"] == "施术单元"
    assert operator["avatar"] == "operators/测试干员/avatar.png"
    assert operator["has_assets"] is True
    kinds = {skill["kind"]: skill for skill in operator["skills"]}
    assert kinds["skill"]["name"] == "测试战技"
    assert kinds["skill"]["desc"] == "战技描述标记"
    assert kinds["skill"]["icon"] == "icon_skill_test_01"
    assert kinds["skill"]["duration"] == pytest.approx(4.0)
    assert kinds["link"]["duration"] == pytest.approx(2.0)
    assert kinds["ult"]["duration"] == pytest.approx(8.0)
    assert kinds["heavy"]["duration"] == pytest.approx(3.0)
    assert kinds["heavy"]["name"] is None


def test_validate_payload_catches_bad_data():
    module = _fetch_module()
    payload = load_operators()
    broken = json.loads(json.dumps(payload))
    broken["operators"][0]["id"] = broken["operators"][1]["id"]
    broken["operators"][0]["skills"] = []
    broken["meta"]["operator_count"] = 1
    errors = module.validate_payload(broken)
    assert errors, "坏数据必须被校验器抓住"
    text = " ".join(errors)
    assert "id 重复" in text or "重复" in text
    assert "skills" in text or "技能组" in text
    assert "operator_count" in text


def test_plan_skill_downloads_dedupes_and_finds_heavy():
    module = _fetch_module()
    fixture = _fixture_tables()
    wanted = module.plan_skill_downloads(fixture["growth_table"])
    ids = [skill_id for _, _, skill_id in wanted]
    assert len(ids) == len(set(ids)), "重复 skillId 必须去重"
    assert "chr_0001_test_power_attack" in ids
    kinds = {kind for _, kind, _ in wanted}
    assert kinds == {"attack", "skill", "link", "ult", "heavy"}


def test_image_paths_excludes_i18n_copies():
    module = _fetch_module()
    fixture = _fixture_tables()
    images = fixture["index"]["datasets"]["images"]["files"]
    charicons = module.image_paths(images, "charicon")
    assert "icon_chr_0001_test" in charicons
    assert "i18n_icon_chr_0001_test" not in charicons


# ---------------------------------------------------------------------------
# tools/sync_game_data.py（离线纯函数）
# ---------------------------------------------------------------------------
def _sync_module():
    import importlib

    return importlib.import_module("tools.sync_game_data")


def _sync_fixture() -> tuple[list[dict], dict]:
    operator = {
        "id": "chr_0001_test",
        "name": "测试干员",
        "avatar": "operators/测试干员/avatar.png",
        "portrait": "operators/测试干员/portrait.png",
        "variant": None,
        "profession": "术师",
        "profession_icon": "icon_profession_5",
        "element": "电磁",
        "element_id": "Pulse",
        "skills": [
            {"id": "chr_0001_test_attack1", "kind": "attack", "icon": "icon_attack_sword"},
            {"id": "chr_0001_test_normal_skill", "kind": "skill", "icon": "icon_skill_test_01"},
            {"id": "chr_0001_test_combo_skill", "kind": "link", "icon": "icon_combo_skill_test_01"},
            {"id": "chr_0001_test_ultimate_skill", "kind": "ult", "icon": "icon_ultimate_skill_test_01"},
            {"id": "chr_0001_test_power_attack", "kind": "heavy", "icon": None},
        ],
    }
    prefix = "assets/beyond/dynamicassets/gameplay/ui/sprites/"
    files = {
        prefix + "charicon/icon_chr_0001_test.png": {"size": 10, "md5": "a"},
        prefix + "characterportrait/chr_0001_test.png": {"size": 20, "md5": "b"},
        prefix + "skillicon/icon_skill_test_01.png": {"size": 1, "md5": "c"},
        prefix + "skillicon/icon_combo_skill_test_01.png": {"size": 1, "md5": "d"},
        prefix + "skillicon/icon_ultimate_skill_test_01.png": {"size": 1, "md5": "e"},
        prefix + "skillicon/icon_attack_sword.png": {"size": 1, "md5": "f"},
        prefix + "charprofessionicon/icon_profession_5.png": {"size": 2, "md5": "g"},
        prefix + "elementicon/icon_charattrtype_pulse.png": {"size": 2, "md5": "h"},
    }
    index = {"datasets": {"images": {"root": "public/images", "files": files}}}
    return [operator], index


def test_plan_tasks_gamedata_layout(tmp_path):
    module = _sync_module()
    operators, index = _sync_fixture()
    tasks = module.plan_tasks(operators, index, tmp_path / "gamedata")
    dests = {task["dest"] for task in tasks}
    assert "operators/测试干员/avatar.png" in dests
    assert "operators/测试干员/skills/attack.png" in dests
    assert "operators/测试干员/skills/skill.png" in dests
    assert "operators/测试干员/skills/link.png" in dests
    assert "operators/测试干员/skills/ult.png" in dests
    assert "classes/术师.png" in dests
    assert "elements/电磁.png" in dests
    # gamedata 布局不写旧协议文件、不伪造重击图标
    assert "operators/测试干员/skill.png" not in dests
    assert "operators/测试干员/icon.png" not in dests
    assert not any(dest.endswith("heavy.png") for dest in dests)
    for task in tasks:
        assert not task["dest"].startswith("/")
        assert ":" not in task["dest"]


def test_plan_tasks_legacy_layout_adds_compat_files(tmp_path):
    module = _sync_module()
    operators, index = _sync_fixture()
    tasks = module.plan_tasks(operators, index, tmp_path / "素材")
    dests = {task["dest"] for task in tasks}
    assert "operators/测试干员/avatar.png" in dests
    assert "operators/测试干员/icon.png" in dests
    assert "operators/测试干员/skill.png" in dests
    assert "operators/测试干员/link.png" in dests
    assert "operators/测试干员/ult.png" in dests


def test_plan_tasks_skips_existing_files(tmp_path):
    module = _sync_module()
    operators, index = _sync_fixture()
    target = tmp_path / "gamedata"
    first = module.plan_tasks(operators, index, target)
    assert first
    for task in first:
        dest = target / task["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        expected = task.get("expected_size")
        dest.write_bytes(b"x" * (expected if expected is not None else 5))
    assert module.plan_tasks(operators, index, target) == [], "已存在且大小正确应跳过（断点）"
    forced = module.plan_tasks(operators, index, target, force=True)
    assert len(forced) == len(first)


def test_plan_tasks_dedupes_profession_icons(tmp_path):
    module = _sync_module()
    operators, index = _sync_fixture()
    twin = json.loads(json.dumps(operators[0]))
    twin["id"] = "chr_0002_test"
    twin["name"] = "测试干员二"
    twin["avatar"] = "operators/测试干员二/avatar.png"
    # 第二个干员没有头像/技能图，只保留职业图标任务，验证去重
    twin["skills"] = []
    tasks = module.plan_tasks([operators[0], twin], index, tmp_path / "gamedata")
    class_tasks = [task for task in tasks if task["kind"] == "profession-icon"]
    assert len(class_tasks) == 1


def test_plan_tasks_handles_admin_variants(tmp_path):
    module = _sync_module()
    operators, index = _sync_fixture()
    operator = operators[0]
    male = dict(operator, id="chr_0002_endminm", name="管理员", variant="m",
                avatar="operators/管理员/avatar.png")
    female = dict(operator, id="chr_0003_endminf", name="管理员", variant="f",
                  avatar="operators/管理员/avatar.png")
    index["datasets"]["images"]["files"].update({
        "assets/beyond/dynamicassets/gameplay/ui/sprites/charicon/icon_chr_0002_endminm.png":
            {"size": 10, "md5": "a"},
        "assets/beyond/dynamicassets/gameplay/ui/sprites/charicon/icon_chr_0003_endminf.png":
            {"size": 11, "md5": "b"},
    })
    tasks = module.plan_tasks([male, female], index, tmp_path / "gamedata", include_skills=False)
    dests = {task["dest"] for task in tasks}
    assert "operators/管理员/avatar.png" in dests
    assert "operators/管理员/avatar_f.png" in dests


def test_plan_text_files_for_new_dirs(tmp_path):
    module = _sync_module()
    operators, _ = _sync_fixture()
    plans = module.plan_text_files(operators, tmp_path / "gamedata")
    dests = {plan["dest"]: plan["text"] for plan in plans}
    assert dests["operators/测试干员/class.txt"] == "术师\n"
    assert dests["operators/测试干员/element.txt"] == "电磁\n"


def test_safe_dirname_and_operator_directory():
    module = _sync_module()
    assert module.safe_dirname("伊冯") == "伊冯"
    assert module.safe_dirname('a/b:c*?"<>|') == "a_b_c______"
    assert module.operator_directory({"avatar": "operators/管理员/avatar.png"}) == "管理员"
    assert module.operator_directory({"name": "明河"}) == "明河"


def test_sync_dry_run_does_not_need_network(tmp_path, capsys):
    module = _sync_module()
    operators, index = _sync_fixture()
    tasks = module.plan_tasks(operators, index, tmp_path / "gamedata")
    total_bytes, ok, failures, missing = module.run_tasks(
        tasks, tmp_path / "gamedata", jobs=1, retries=1, timeout=1,
        dry_run=True, no_network=False,
    )
    assert (total_bytes, ok, failures) == (0, 0, [])
    assert len(missing) == len(tasks)
    assert "dry-run" in capsys.readouterr().out


def test_both_tool_scripts_have_cli_parsers():
    fetch = _fetch_module()
    sync = _sync_module()
    fetch_args = fetch.parse_args(["--dry-run", "--no-network", "--validate-only"])
    assert fetch_args.dry_run and fetch_args.no_network and fetch_args.validate_only
    sync_args = sync.parse_args(["--only-avatars", "--version", "1.6.4"])
    assert sync_args.only_avatars and sync_args.version == "1.6.4"
    assert sync_args.target.endswith("gamedata"), "默认落点必须是 assets/gamedata"
    assert sync.parse_args(["--target", "素材"]).target.endswith("素材")
