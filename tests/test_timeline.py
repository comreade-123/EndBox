# -*- coding: utf-8 -*-
"""排轴模块 v3 测试：有序步骤序列 / 分段反复 / 轴库编队库 / 分享码 / 运行推进 / 旧格式迁移。"""
from __future__ import annotations

import json

import pytest
from PyQt6.QtCore import Qt

from endbox import config
from endbox.app_context import AppContext
from endbox.config import Settings
from endbox.modules.timeline import share
from endbox.modules.timeline.assets import AssetLibrary
from endbox.modules.timeline.library import AxisLibrary
from endbox.modules.timeline.model import Step, Team, TimelineModel, normalize_loop_count
from endbox.modules.timeline.runtime import RunEngine, RunSequence, keys_from_settings, normalize_token
from endbox.modules.timeline.service import TimelineService
from endbox.services import Services


@pytest.fixture(autouse=True)
def _auto_confirm(monkeypatch):
    """离屏测试不弹真实确认框。"""
    import endbox.modules.timeline.ui_parts as parts
    import endbox.ui.views.timeline as view

    monkeypatch.setattr(parts, "confirm", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(view, "confirm", lambda *a, **k: True, raising=False)


class FakeBus:
    def __init__(self) -> None:
        self.events = []
        self._subs = {}

    def emit(self, topic, **payload) -> None:
        self.events.append((topic, payload))
        for handler in list(self._subs.get(topic, ())):
            handler(**payload)

    def subscribe(self, topic, handler):
        bus = self

        class _Sub:
            def cancel(self):
                bucket = bus._subs.get(topic) or []
                if handler in bucket:
                    bucket.remove(handler)

        bus._subs.setdefault(topic, []).append(handler)
        return _Sub()

    def topics(self):
        return [topic for topic, _ in self.events]


def make_ctx(tmp_path) -> AppContext:
    return AppContext(settings=Settings(tmp_path / "settings.json"), bus=FakeBus(), services=Services())


def make_service(tmp_path, *, assets=None, input_backend="mock", overlay_enabled=False) -> TimelineService:
    ctx = make_ctx(tmp_path)
    service = TimelineService(
        ctx, path=tmp_path / "timeline.json", library_path=tmp_path / "timeline_library.json",
        bus=ctx.bus, assets=assets, autosave=False,
        input_backend=input_backend, overlay_enabled=overlay_enabled,
    )
    ctx.services.timeline = service
    return service


@pytest.fixture()
def fake_assets(tmp_path) -> AssetLibrary:
    root = tmp_path / "game"
    operators = root / "operators" / "测试干员"
    operators.mkdir(parents=True)
    (root / "operators" / "roster.json").write_text('{"1": "测试干员"}', "utf-8")
    (operators / "class.txt").write_text("术士", "utf-8")
    (operators / "element.txt").write_text("灼热", "utf-8")
    (root / "classes").mkdir()
    (root / "elements").mkdir()
    (root / "classes" / "术士.png").write_bytes(b"")
    (root / "elements" / "灼热.png").write_bytes(b"")
    return AssetLibrary(root, extra_path=tmp_path / "missing_extra.json")


# --- 序列模型 -------------------------------------------------------------


def test_step_kinds_and_counts():
    model = TimelineModel()
    for kind in ("skill", "link", "ult", "heavy", "comment", "break", "segment", "loop_start", "loop_end"):
        model.add_step(kind, operator=0)
    assert model.total == 9
    counts = model.counts()
    assert counts["skill"] == 1 and counts["loop_end"] == 1
    assert model.kind_sequence()[:3] == ["skill", "link", "ult"]


def test_move_and_remove_steps():
    model = TimelineModel()
    a = model.add_step("skill", 0)
    b = model.add_step("link", 1)
    c = model.add_step("ult", 2)
    assert model.move_steps([c], -1) is True
    assert model.kind_sequence() == ["skill", "ult", "link"]
    assert model.move_steps([a], -1) is False
    assert model.remove_steps([c]) == 1
    assert model.kind_sequence() == ["skill", "link"]


def test_segments_and_loop_pairing():
    model = TimelineModel()
    model.add_step("skill")
    model.add_step("loop_start", loop_count=2)
    model.add_step("skill")
    model.add_step("loop_start", loop_count=3)
    model.add_step("link")
    model.add_step("loop_end")
    model.add_step("ult")
    model.add_step("loop_end")
    model.add_step("segment", label="第二段")
    model.add_step("skill")
    segments = model.segments()
    assert len(segments) == 2
    assert segments[1]["name"] == "第二段"
    loops = model.loops()
    assert len(loops) == 2
    assert loops[0]["start"] < loops[1]["start"]
    assert loops[1]["count"] == 3
    assert {loop["depth"] for loop in loops} == {0, 1}


def test_loop_count_normalization():
    assert normalize_loop_count(-1) == -1
    assert normalize_loop_count(0) == 1
    assert normalize_loop_count(999) == 99


def test_model_json_roundtrip(tmp_path):
    model = TimelineModel()
    model.add_step("skill", 0)
    model.add_step("loop_start", loop_count=3, label="循环")
    model.add_step("comment", label="注意")
    model.add_step("loop_end")
    path = tmp_path / "timeline.json"
    assert model.save(path) is True
    loaded = TimelineModel.load(path)
    assert loaded is not None
    assert loaded.to_dict() == model.to_dict()


def test_legacy_timeline_migration(tmp_path):
    legacy = {
        "version": 2,
        "name": "旧轴",
        "team": ["伊冯"],
        "model": {
            "duration": 30.0,
            "events": [
                {"operator": 0, "kind": "skill", "start": 3.0, "duration": 2.4},
                {"operator": 1, "kind": "link", "start": 1.0, "duration": 1.6},
                {"operator": 0, "kind": "comment", "start": 2.0, "duration": 0, "label": "注释"},
            ],
            "buffs": [{"name": "攻击", "start": 1.0, "end": 5.0}],
        },
    }
    (tmp_path / "timeline.json").write_text(json.dumps(legacy, ensure_ascii=False), "utf-8")
    service = make_service(tmp_path)
    assert [step.kind for step in service.model.steps[:3]] == ["link", "comment", "skill"]
    assert service.axis_name == "旧轴"
    assert service.last_error is not None  # 提示旧格式已迁移
    snap = service.snapshot()
    assert len(snap["events"]) == len(snap["steps"]) == 3


# --- 素材 / 补充资料 ------------------------------------------------------


def test_assets_and_extras(fake_assets):
    assert fake_assets.available is True
    assert fake_assets.count == 1
    assert fake_assets.extra_for("测试干员") is None
    assert fake_assets.extra_available is False


def test_assets_real_material():
    library = AssetLibrary(config.GAME_ASSETS)
    if not library.available:
        pytest.skip("素材目录不存在")
    assert library.count >= 30
    operator = library.get("伊冯")
    assert operator is not None and operator.avatar is not None
    if library.extra_available:
        extra = library.extra_for("伊冯")
        assert extra is not None and extra.rarity in (4, 5, 6)


# --- snapshot 兼容 --------------------------------------------------------


def test_snapshot_sequence_and_home_compat(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯", "余烬", "佩丽卡", "陈千语"])
    service.set_axis_name("序列轴")
    service.add_step("skill", 0)
    service.add_step("comment", label="注意")
    service.add_step("loop_start", loop_count=2)
    service.add_step("ult", 2)
    service.add_step("loop_end")
    service.add_step("segment", label="第二段")
    snapshot = service.snapshot()
    # home/HUD 旧字段仍在（只取 len）
    for key in ("t", "playing", "duration", "team", "active", "events", "buffs"):
        assert key in snapshot
    assert len(snapshot["team"]) == 4
    assert len(snapshot["events"]) == 6
    assert snapshot["name"] == "序列轴"
    assert snapshot["step_count"] == 6
    assert snapshot["steps"][0]["kind"] == "skill"
    assert snapshot["steps"][0]["operator_name"] == "伊冯"
    assert snapshot["kinds"]["skill"] == 1
    assert len(snapshot["segments"]) == 2
    assert len(snapshot["loops"]) == 1


# --- 运行序列 / 运行推进 --------------------------------------------------


def _axis(service):
    service.set_team(["伊冯", "余烬", "佩丽卡", "陈千语"])
    service.add_step("skill", 0)
    service.add_step("link", 1)
    service.add_step("comment", label="提示")
    service.add_step("heavy", 2)
    service.add_step("ult", 3)
    return service


def test_runtime_sequence_tokens_and_skip(tmp_path):
    service = _axis(make_service(tmp_path))
    sequence = RunSequence.from_model(service.model, service.team.to_list(), keys_from_settings({}))
    # 注释/重击不按键；只剩 3 个按键步骤
    assert sequence.total == 3
    # 实机键位：短按数字 = 战技、E = 连携、长按数字 = 终结
    assert [step.token for step in sequence.steps] == ["1", "E", "HOLD+4"]
    assert [step.key_text for step in sequence.steps] == ["1", "E", "长按4"]


def test_runtime_wrong_then_correct_and_controls(tmp_path):
    service = _axis(make_service(tmp_path))
    assert service.start_run() is True
    state = service.run_state()
    assert state["total"] == 3
    assert state["admin"] in (True, False)
    # 短按数字 = 战技：按错号位不推进、给提示；按对才前进
    assert service.feed_input("2") == "wrong"
    assert service.run_state()["index"] == 0
    assert "按错了" in service.run_state()["hint"]
    assert service.feed_input("1") == "ok"
    assert service.run_state()["index"] == 1
    # E = 连携
    assert service.feed_input("E") == "ok"
    # 长按数字 = 终结
    assert service.feed_input("HOLD+4") == "ok"
    assert service.run_state()["finished"] is True
    # 控制键
    assert service.feed_input("F9") == "control"
    assert service.run_state()["index"] == 0
    assert service.feed_input("F6") == "control"
    assert service.feed_input("F10") == "control"
    assert service.feed_input("F8") == "control"
    assert service.run_active is False


def test_runtime_loops_cycle(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯"])
    service.add_step("skill", 0)
    service.add_step("loop_start", loop_count=2)
    service.add_step("skill", 0)
    service.add_step("skill", 0)
    service.add_step("loop_end")
    service.add_step("skill", 0)
    sequence = RunSequence.from_model(service.model, service.team.to_list(), keys_from_settings({}))
    assert sequence.total == 4
    assert len(sequence.loops) == 1
    engine = RunEngine(sequence, loops_enabled=True)
    order = [engine.index]
    for _ in range(5):
        engine.advance()
        order.append(engine.index)
    assert order == [0, 1, 2, 1, 2, 3]
    assert engine.finished is False
    engine.toggle_loops()
    engine.reset()
    for _ in range(4):
        engine.advance()
    assert engine.finished is True


def test_run_settings_keys_persist(tmp_path):
    ctx = make_ctx(tmp_path)
    service = TimelineService(ctx, path=tmp_path / "timeline.json",
                              library_path=tmp_path / "lib.json", bus=ctx.bus,
                              input_backend="mock", overlay_enabled=False)
    settings = service.set_run_settings({"style": "compact", "anchor": "top-left", "scale": 1.2,
                                         "opacity": 0.55, "preview": False, "progress": False,
                                         "minimize": False, "gamepad": "xbox", "hold_ms": 350})
    assert settings["gamepad"] == "xbox" and settings["hold_ms"] == 350
    keys = service.set_keys({"link": "R", "heavy": "J"})
    assert keys["link"] == "R" and keys["heavy"] == "J"
    restored = TimelineService(ctx, path=tmp_path / "timeline.json",
                               library_path=tmp_path / "lib.json", bus=ctx.bus,
                               input_backend="mock", overlay_enabled=False)
    assert restored.run_settings()["gamepad"] == "xbox"
    assert restored.run_settings()["hold_ms"] == 350
    assert restored.keys()["link"] == "R"


def test_input_adapter_mock_and_digits():
    from endbox.modules.timeline.input_adapter import InputAdapter

    adapter = InputAdapter(backend="mock")
    received = []
    adapter.token.connect(received.append)
    assert adapter.start() is True
    adapter.feed("q+1")
    adapter.feed("1")
    assert received == ["Q+1", "1"]
    adapter.feed("Q+1")  # 60ms 内重复 → 去重
    assert received == ["Q+1", "1"]
    adapter.stop()
    assert adapter.available is False


# --- 轴库 / 编队库 / 分享码 ----------------------------------------------


def test_library_and_team_library(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯", "余烬"])
    service.set_axis_name("轴 A")
    service.add_step("skill", 0)
    entry = service.library_save("轴 A")
    assert entry is not None
    assert service.library_list("轴 A")[0]["name"] == "轴 A"
    service.clear_steps()
    assert service.library_load(entry["id"]) is True
    assert len(service.model.steps) == 1
    team_entry = service.team_library_save("测试编队")
    assert team_entry is not None
    assert service.team_library_list("测试")[0]["name"] == "测试编队"
    service.clear_team()
    assert service.team_library_load(team_entry["id"]) is True
    assert service.team.to_list()[:2] == ["伊冯", "余烬"]
    assert service.team_library_delete(team_entry["id"]) is True


def test_share_roundtrip(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯", "余烬", "佩丽卡", "陈千语"])
    service.set_axis_name("分享轴")
    service.add_step("skill", 0)
    service.add_step("loop_start", loop_count=3)
    service.add_step("comment", label="备注")
    service.add_step("loop_end")
    code = service.export_share()
    assert code.startswith(share.SHARE_PREFIX)
    service.clear_steps()
    service.clear_team()
    result = service.import_share(code)
    assert result["name"] == "分享轴"
    assert result["steps"] == 4
    assert service.model.steps[1].loop_count == 3
    assert service.team.to_list()[0] == "伊冯"


def test_share_errors(tmp_path):
    service = make_service(tmp_path)
    service.add_step("skill", 0)
    for bad in ("", "hello", "EB2:1234", json.dumps({"format": "Other"})):
        with pytest.raises(share.ShareCodeError):
            service.import_share(bad)
    empty = share.encode(share.make_payload("空", [], {"steps": []}))
    with pytest.raises(share.ShareCodeError):
        service.import_share(empty)
    assert len(service.model.steps) == 1


# --- 离屏视图 -------------------------------------------------------------


def test_view_without_service(qapp, tmp_path):
    from endbox.ui.kit import EmptyState
    from endbox.ui.views.timeline import TimelineView

    view = TimelineView(make_ctx(tmp_path))
    assert view.findChildren(EmptyState)
    view.deleteLater()


def test_view_sequence_edit_and_run(qapp, tmp_path):
    from endbox.modules.timeline.ui_canvas import SequenceCanvas
    from endbox.ui.views.timeline import TimelineView

    service = make_service(tmp_path)
    view = TimelineView(service.ctx)
    view.resize(1200, 800)
    qapp.processEvents()
    service.set_team(["伊冯", "余烬", "佩丽卡"])
    view._on_target_slot(0)
    view._add_step("skill")
    view._add_step("comment")
    view._add_step("loop_start")
    view._add_step("ult")
    view._add_step("loop_end")
    view.refresh()
    canvas = view.findChild(SequenceCanvas)
    assert canvas is not None
    assert len(service.model.steps) == 5
    assert view.topbar_fields()[0] == "5 步骤"
    assert not canvas.grab().isNull()

    canvas.select_step(service.model.steps[0])
    view._move_selected(1)
    assert service.model.steps[1].kind == "skill"
    view._delete_selected()
    assert len(service.model.steps) == 4

    view._toggle_run()
    qapp.processEvents()
    assert service.run_active is True
    assert view._run_btn.text() == "停止运行"
    service.feed_input("1")
    qapp.processEvents()
    view._toggle_run()
    assert service.run_active is False
    view.deleteLater()
    qapp.processEvents()


def test_run_overlay_states(qapp):
    from endbox.modules.timeline.run_overlay import RunOverlay, normalize_run_settings

    overlay = RunOverlay(normalize_run_settings({"style": "standard"}))
    overlay.apply_state({
        "active": True, "index": 0, "total": 8, "progress_text": "1 / 8",
        "step": {"key_text": "Q1", "title": "伊冯 · 战技", "operator_name": "伊冯", "kind_label": "战技"},
        "next": {"key_text": "E2", "operator_name": "余烬", "kind_label": "连携"},
        "segment": {"index": 0, "name": "第1段", "position": 1, "total": 4},
        "admin": False, "warning": "非管理员提示", "hint": "",
    })
    assert not overlay.grab().isNull()
    overlay.set_settings({"style": "compact", "anchor": "top-left", "scale": 1.2})
    assert not overlay.grab().isNull()
    overlay.deleteLater()
    qapp.processEvents()


# --- 按键语义：每种 kind / 重击 / 纯数字 / 长按 / F 键 -------------

ALL_KINDS = (
    "skill", "link", "ult", "heavy", "comment", "break", "segment", "loop_start", "loop_end",
)
KEY_KINDS = ("skill", "link", "ult")


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_run_state_never_crashes_for_each_kind(tmp_path, kind):
    service = make_service(tmp_path)
    service.set_team(["伊冯", "余烬", "佩丽卡", "陈千语"])
    label = "说明" if kind in ("comment", "segment") else ""
    service.add_step(kind, 2, label=label)
    idle = service.run_state()
    assert idle["active"] is False and idle["total"] == 0
    started = service.start_run()
    state = service.run_state()
    if kind in KEY_KINDS:
        assert started is True
        assert state["total"] == 1 and state["step"]["kind"] == kind
        assert state["step"]["operator"] == 2
        assert state["step"]["key_text"] in ("3", "E", "长按3")
        service.stop_run("test")
    else:
        # 注释/换行/分段/反复/重击都不产生按键步骤
        assert started is False
        assert state["total"] == 0
        assert state["hint"] == ""
    assert service.run_active is False


def test_heavy_is_prompt_only_and_h_does_not_advance(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯"])
    service.add_step("heavy", 0)
    service.add_step("skill", 0)
    service.add_step("heavy", 0)
    service.add_step("skill", 0)
    sequence = RunSequence.from_model(service.model, service.team.to_list(), keys_from_settings({}))
    assert sequence.total == 2
    assert all(step.kind == "skill" for step in sequence.steps)
    assert service.start_run() is True
    assert service.run_state()["step"]["kind"] == "skill"
    # 实机重击设定：重击只提示不按键，按 H 不能前进、也不能误报"按错"
    assert service.feed_input("H") == "ignored"
    assert service.run_state()["index"] == 0
    assert service.run_state()["hint"] == ""
    assert service.feed_input("1") == "ok"
    assert service.run_state()["index"] == 1
    assert service.feed_input("H") == "ignored"
    service.stop_run("test")


def test_short_digit_is_skill_and_hold_is_ult(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯"])
    service.add_step("skill", 0)
    service.add_step("ult", 0)
    assert service.start_run() is True
    # 短按数字 = 战技：2 号位按 2 才是战技，按 1 不对
    assert service.feed_input("2") == "wrong"
    assert service.run_state()["index"] == 0
    assert service.feed_input("1") == "ok"
    assert service.run_state()["index"] == 1
    # 当前是终结技：短按 1 不能推进（终结必须长按）
    assert service.feed_input("1") == "wrong"
    assert service.run_state()["index"] == 1
    assert service.feed_input("HOLD+1") == "ok"
    assert service.run_state()["finished"] is True
    service.stop_run("test")


def test_hold_number_is_the_only_ult_route(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯", "余烬"])
    service.add_step("ult", 1)
    assert service.start_run() is True
    # 短按 2 = 战技语义，不能当终结
    assert service.feed_input("2") == "wrong"
    assert service.run_state()["index"] == 0
    assert service.feed_input("HOLD+2") == "ok"
    assert service.run_state()["finished"] is True
    service.stop_run("test")


def test_control_keys_f6_f7_f10_f11(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯"])
    service.add_step("skill", 0)
    service.add_step("loop_start", 0, loop_count=2)
    service.add_step("skill", 0)
    service.add_step("skill", 0)
    service.add_step("loop_end", 0)
    service.add_step("skill", 0)
    assert service.start_run() is True
    engine = service._run_engine

    start_opacity = service.run_settings()["opacity"]
    assert service.feed_input("F6") == "control"
    assert abs(service.run_settings()["opacity"] - round(start_opacity - 0.1, 2)) < 1e-6
    assert service.feed_input("F7") == "control"
    assert abs(service.run_settings()["opacity"] - start_opacity) < 1e-6

    assert engine.loops_enabled is True
    assert service.feed_input("F10") == "control"
    assert engine.loops_enabled is False

    assert service.feed_input("1") == "ok"   # 0 -> 1（反复起点）
    assert service.feed_input("1") == "ok"   # 1 -> 2
    assert engine.index == 2
    assert service.feed_input("F11") == "control"   # 回到最内层反复起点
    assert engine.index == 1

    assert service.feed_input("F9") == "control"
    assert engine.index == 0
    assert service.feed_input("F8") == "control"
    assert service.run_active is False
    assert service.feed_input("F8") == "ignored"


# --- 手柄（实机按键设定）：PAD_* → 步骤 / 提示条键位跟随输入模式 ------------------


def test_pad_tokens_map_to_skill_and_link(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯", "余烬", "佩丽卡", "陈千语"])
    service.add_step("skill", 0)
    service.add_step("link", 1)
    service.add_step("skill", 3)
    sequence = RunSequence.from_model(service.model, service.team.to_list(), keys_from_settings({}))
    assert sequence.steps[0].pad_tokens == ("PAD_MOD+PAD_X",)
    assert sequence.steps[1].pad_tokens == ("PAD_Y", "PAD_RB")
    assert sequence.steps[2].pad_tokens == ("PAD_MOD+PAD_A",)
    assert service.start_run() is True
    # 单按面键 = 连携语义，不能当战技
    assert service.feed_input("PAD_X") == "wrong"
    # LB/L1 + 面键 = 战技
    assert service.feed_input("PAD_MOD+PAD_X") == "ok"
    # LB + 面键 = 战技语义，不是连携
    assert service.feed_input("PAD_MOD+PAD_Y") == "wrong"
    assert service.feed_input("PAD_RB") == "ok"
    assert service.feed_input("PAD_MOD+PAD_A") == "ok"
    assert service.run_state()["finished"] is True
    service.stop_run("test")


def test_pad_tokens_not_accepted_for_ult(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯"])
    service.add_step("ult", 0)
    sequence = RunSequence.from_model(service.model, service.team.to_list(), keys_from_settings({}))
    assert sequence.steps[0].pad_tokens == ()
    assert service.start_run() is True
    for token in ("PAD_A", "PAD_RB", "PAD_MOD+PAD_X"):
        assert service.feed_input(token) == "wrong"
        assert service.run_state()["index"] == 0
    assert service.feed_input("HOLD+1") == "ok"
    service.stop_run("test")


def test_run_bar_key_label_follows_input_mode(tmp_path):
    service = make_service(tmp_path)
    service.set_team(["伊冯", "余烬", "佩丽卡", "陈千语"])
    service.add_step("skill", 0)
    service.add_step("link", 1)
    service.add_step("ult", 2)
    service.set_run_settings({"input_display": "pad", "gamepad": "xbox"})
    assert service.start_run() is True
    cards = service.run_state()["cards"]
    assert cards[0]["key_text"] == "1" and cards[0]["pad_key"] == "LB+X"
    assert cards[1]["key_text"] == "E" and cards[1]["pad_key"] == "RB"
    # 终结技没有手柄键位：回退键盘键位（提示条按 key_text 显示）
    assert cards[2]["pad_key"] == "" and cards[2]["key_text"] == "长按3"
    # DS 布局同步更新
    service.set_run_settings({"gamepad": "ds"})
    cards = service.run_state()["cards"]
    assert cards[0]["pad_key"] == "L1+□"
    assert cards[1]["pad_key"] == "R1"
    service.stop_run("test")


def test_editor_canvas_and_run_bar_share_key_text(tmp_path, qapp):
    from endbox.ui.views.timeline import TimelineView

    service = make_service(tmp_path)
    view = TimelineView(service.ctx)
    view.resize(1200, 900)
    service.set_team(["伊冯", "余烬", "佩丽卡", "陈千语"])
    for kind, operator in (("skill", 0), ("link", 1), ("ult", 2)):
        service.add_step(kind, operator)
    view.refresh()
    sequence = RunSequence.from_model(service.model, service.team.to_list(), keys_from_settings({}))
    canvas = view._canvas
    for model_step, run_step in zip(service.model.steps, sequence.steps):
        assert canvas.step_key_text(model_step) == run_step.key_text
    # 动作面板里的键位文本与运行条一致（1 / E / 长按3）
    panel = view._actions
    assert panel._badge_text("skill", 0) == sequence.steps[0].key_text == "1"
    assert panel._badge_text("link", 1) == sequence.steps[1].key_text == "E"
    assert panel._badge_text("ult", 2) == sequence.steps[2].key_text == "长按3"
    view.deleteLater()


# --- 动作面板：点干员直接插入 -------------------------------------------------


def _click_cell(panel, row_kind, slot):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest

    rows, _heavy = panel._cell_rects()
    for kind, _label, cells in rows:
        if kind != row_kind:
            continue
        for cell_slot, rect in cells:
            if cell_slot == slot:
                QTest.mouseClick(panel, Qt.MouseButton.LeftButton,
                                 pos=QPoint(int(rect.center().x()), int(rect.center().y())))
                return True
    return False


def test_action_panel_click_inserts_operator_skill(qapp, tmp_path):
    from endbox.ui.views.timeline import TimelineView

    service = make_service(tmp_path)
    view = TimelineView(service.ctx)
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    view.resize(1200, 900)
    view.show()
    service.set_team(["伊冯", "余烬", "佩丽卡", "陈千语"])
    view.refresh()
    panel = view._actions
    assert panel.width() > 320

    # 点「余烬 · 战技」→ 插入 operator=1 的战技
    assert _click_cell(panel, "skill", 1) is True
    assert len(service.model.steps) == 1
    step = service.model.steps[0]
    assert (step.kind, step.operator) == ("skill", 1)
    assert view._canvas.selection == [step]

    # 接着点「伊冯 · 连携」→ 追加在选中步骤之后，顺序保持
    assert _click_cell(panel, "link", 0) is True
    assert [(s.kind, s.operator) for s in service.model.steps] == [("skill", 1), ("link", 0)]

    # 点「陈千语 · 终结」→ operator=3 的终结技
    assert _click_cell(panel, "ult", 3) is True
    assert (service.model.steps[-1].kind, service.model.steps[-1].operator) == ("ult", 3)

    # 重击没有干员：只插入 heavy，operator 归零
    from PyQt6.QtCore import QPoint
    from PyQt6.QtTest import QTest

    _rows, heavy = panel._cell_rects()
    QTest.mouseClick(panel, Qt.MouseButton.LeftButton,
                     pos=QPoint(int(heavy.center().x()), int(heavy.center().y())))
    assert service.model.steps[-1].kind == "heavy"
    assert service.model.steps[-1].operator == 0
    view.hide()
    view.deleteLater()


def test_action_panel_empty_slots_do_not_insert(qapp, tmp_path):
    from endbox.ui.views.timeline import TimelineView

    service = make_service(tmp_path)
    view = TimelineView(service.ctx)
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    view.resize(1200, 900)
    view.show()
    panel = view._actions
    assert [panel._name(slot) for slot in range(4)] == ["", "", "", ""]
    assert _click_cell(panel, "skill", 0) is True
    assert service.model.steps == []
    view.hide()
    view.deleteLater()


def test_legacy_modifier_combos_are_not_used(tmp_path):
    """Q/E/Alt 组合键不再参与运行态：只有数字短按 / 长按 / E。"""
    service = _axis(make_service(tmp_path))
    assert service.start_run() is True
    for token in ("Q+1", "E+2", "ALT+4"):
        assert service.feed_input(token) == "wrong"
        assert service.run_state()["index"] == 0
    assert service.feed_input("1") == "ok"
    service.stop_run("test")


def test_input_adapter_distinguishes_tap_and_hold():
    from endbox.core.input import KeyEvent
    from endbox.modules.timeline.input_adapter import InputAdapter

    adapter = InputAdapter(backend="mock")
    adapter._raw_available = True
    adapter._hold_ms = 280
    received = []
    adapter.token.connect(received.append)
    adapter.start()
    # 按下不发 token：等松开才知道是短按还是长按
    adapter._on_service_event("1")
    assert received == []
    adapter._on_service_raw(KeyEvent(token="1", kind="down", vk=0x31, timestamp=100.0))
    adapter._on_service_raw(KeyEvent(token="1", kind="up", vk=0x31, timestamp=100.15))
    assert received == ["1"]
    # 长按：HOLD token 发终结；随后松开不再补发短按
    adapter._on_service_raw(KeyEvent(token="2", kind="down", vk=0x32, timestamp=200.0))
    adapter._on_service_event("HOLD+2")
    adapter._on_service_raw(KeyEvent(token="2", kind="up", vk=0x32, timestamp=200.4))
    assert received == ["1", "HOLD+2"]
    adapter.stop()


# --- 文案：timeline section 完整 + 代码里没有中文字面量 ----------------------


def test_timeline_i18n_section_has_kept_keys():
    from endbox.core.text import catalog

    keys = [key for key in catalog.keys() if key.startswith("timeline.")]
    assert len(keys) >= 250
    for kind in ALL_KINDS:
        assert catalog.has(f"timeline.kind.{kind}")
        assert catalog.has(f"timeline.kind_short.{kind}")


def test_no_hardcoded_chinese_literals_in_timeline_code():
    import ast
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    files = [root / "endbox/ui/views/timeline.py"]
    files += sorted((root / "endbox/modules/timeline").glob("*.py"))
    cjk = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")

    def skip_ids(tree):
        ids = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    if isinstance(body[0].value.value, str):
                        ids.add(id(body[0].value))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id in ("log", "logger", "_log"):
                    for arg in node.args:
                        for sub in ast.walk(arg):
                            if isinstance(sub, ast.Constant):
                                ids.add(id(sub))
        return ids

    offenders = []
    for path in files:
        tree = ast.parse(path.read_text("utf-8"))
        skip = skip_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                if cjk.search(node.value):
                    offenders.append((path.name, node.lineno, node.value[:40]))
    assert not offenders, offenders

