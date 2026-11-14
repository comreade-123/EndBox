# -*- coding: utf-8 -*-
"""插件系统测试：加载隔离、生命周期、视图注册、存储、权限、坏 manifest。"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from endbox.app_context import AppContext
from endbox.config import Settings
from endbox.core.event_bus import EventBus
from endbox.plugins.host import (
    STATE_DISABLED,
    STATE_ERROR,
    STATE_LOADED,
    PluginHost,
)
from endbox.plugins.storage import PluginStorage
from endbox.services import Services
from endbox.ui.views.base import ViewRegistry

VIEW_CLASS = """
from endbox.ui.views.base import BaseView


class DemoView(BaseView):
    key = "demo"
    eyebrow = "DEMO"
    title = "演示"
    icon = "spark"
    order = 10
    sidebar_label = "演示"

    def build(self):
        pass
"""

GOOD_PLUGIN = (
    """
from endbox.plugins.api import EndBoxPlugin
"""
    + VIEW_CLASS
    + """

class Plugin(EndBoxPlugin):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.load_calls = 0
        self.enable_calls = 0
        self.disable_calls = 0
        self.unload_calls = 0

    def on_load(self):
        self.load_calls += 1
        self.ctx.storage.set("loads", self.load_calls)
        self.ctx.register_view(DemoView, sidebar_icon="spark", sidebar_label="演示", order=7)

    def on_enable(self):
        self.enable_calls += 1

    def on_disable(self):
        self.disable_calls += 1

    def on_unload(self):
        self.unload_calls += 1
"""
)

CRASH_PLUGIN = (
    """
from endbox.plugins.api import EndBoxPlugin
"""
    + VIEW_CLASS
    + """

class Plugin(EndBoxPlugin):
    def on_load(self):
        self.ctx.register_view(DemoView)
        raise RuntimeError("boom in on_load")
"""
)


def write_plugin(
    root: Path,
    plugin_id: str,
    *,
    code: str | None = GOOD_PLUGIN,
    manifest: dict | None = None,
    entry: str = "plugin.py",
    folder: str | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """在 root 下造一个插件目录。"""
    directory = root / (folder or plugin_id)
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "id": plugin_id,
        "name": f"{plugin_id} 插件",
        "version": "0.1.0",
        "author": "tests",
        "description": "测试插件",
        "icon": "spark",
        "entry": entry,
        "order": 10,
        "min_app_version": "0.1.0",
        "permissions": [],
    }
    if manifest:
        data.update(manifest)
    (directory / "plugin.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    if code is not None:
        (directory / entry).write_text(textwrap.dedent(code), "utf-8")
    for name, content in (extra_files or {}).items():
        (directory / name).write_text(textwrap.dedent(content), "utf-8")
    return directory


@pytest.fixture
def env(tmp_path):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    bundled.mkdir()
    user.mkdir()
    settings = Settings(tmp_path / "settings.json")
    ctx = AppContext(settings=settings, bus=EventBus(), services=Services())
    registry = ViewRegistry()
    host = PluginHost(
        ctx,
        registry,
        roots=[(bundled, "bundled"), (user, "user")],
        data_dir=tmp_path / "plugin_data",
    )
    yield SimpleNamespace(
        tmp=tmp_path,
        bundled=bundled,
        user=user,
        ctx=ctx,
        registry=registry,
        host=host,
        settings=settings,
    )
    host.unload_all()


# ---------------------------------------------------------------------------
# 正常加载与视图注册
# ---------------------------------------------------------------------------


def test_load_all_registers_prefixed_plugin_view(env):
    write_plugin(env.bundled, "demo")
    records = env.host.load_all()

    assert env.host.summary() == {"total": 1, "loaded": 1, "disabled": 0, "error": 0}
    record = env.host.get("demo")
    assert record is not None
    assert record.state == STATE_LOADED
    assert record.source == "bundled"
    assert record.load_ms >= 0

    key = "plugin_demo_demo"
    assert key in record.view_keys
    assert env.registry.has(key)
    view_cls = env.registry.get(key)
    assert view_cls is not None
    assert view_cls.key == key
    assert view_cls.section == "plugin"
    assert view_cls.sidebar_label == "演示"
    assert view_cls.icon == "spark"
    assert view_cls.order == 7
    assert record.instance.load_calls == 1
    assert env.host.loaded == [record]
    assert len(records) == 1


def test_load_all_is_idempotent(env):
    write_plugin(env.bundled, "demo")
    env.host.load_all()
    env.host.load_all()

    keys = [view.key for view in env.registry.classes("plugin")]
    assert keys.count("plugin_demo_demo") == 1
    assert len(env.host.loaded) == 1
    assert env.host.get("demo").instance.load_calls == 1


def test_user_plugin_directory_is_scanned(env):
    write_plugin(env.user, "demo")
    env.host.load_all()
    record = env.host.get("demo")
    assert record is not None and record.state == STATE_LOADED
    assert record.source == "user"
    assert record.path.parent == env.user


def test_two_plugins_same_view_key_do_not_collide(env):
    write_plugin(env.bundled, "demo_a")
    write_plugin(env.bundled, "demo_b")
    env.host.load_all()
    keys = sorted(view.key for view in env.registry.classes("plugin"))
    assert keys == ["plugin_demo_a_demo", "plugin_demo_b_demo"]


def test_plugin_can_use_relative_imports(env):
    code = (
        """
from endbox.plugins.api import EndBoxPlugin
"""
        + VIEW_CLASS
        + """

from .helper import VALUE


class Plugin(EndBoxPlugin):
    def on_load(self):
        self.value = VALUE
        self.ctx.register_view(DemoView)
"""
    )
    write_plugin(env.bundled, "demo", code=code, extra_files={"helper.py": "VALUE = 42\n"})
    env.host.load_all()
    record = env.host.get("demo")
    assert record.state == STATE_LOADED
    assert record.instance.value == 42


# ---------------------------------------------------------------------------
# 隔离：崩溃 / 坏 manifest / 缺入口 / 重复 id
# ---------------------------------------------------------------------------


def test_crashing_plugin_is_isolated(env):
    write_plugin(env.bundled, "good")
    write_plugin(env.bundled, "crash", code=CRASH_PLUGIN)

    env.host.load_all()  # 不抛异常

    crash = env.host.get("crash")
    assert crash is not None
    assert crash.state == STATE_ERROR
    assert "boom in on_load" in (crash.error or "")
    assert crash.detail and "RuntimeError" in crash.detail
    assert crash.instance is None
    # on_load 里已经注册的视图必须回滚
    assert not env.registry.has("plugin_crash_demo")
    assert env.registry.has("plugin_good_demo")
    assert env.host.get("good").state == STATE_LOADED
    assert env.host.summary()["error"] == 1


def test_bad_manifests_are_skipped(env):
    bad_json = env.bundled / "badjson"
    bad_json.mkdir()
    (bad_json / "plugin.json").write_text("{ not json", "utf-8")

    no_id = env.bundled / "noid"
    no_id.mkdir()
    (no_id / "plugin.json").write_text(json.dumps({"name": "无 id"}), "utf-8")
    (no_id / "plugin.py").write_text("x = 1\n", "utf-8")

    write_plugin(env.bundled, "noentry", manifest={"entry": "nothere.py"}, code=None)

    env.host.load_all()

    summary = env.host.summary()
    assert summary["total"] == 3
    assert summary["loaded"] == 0
    assert summary["error"] == 3
    errors = " ".join(record.error or "" for record in env.host.plugins)
    assert "JSON" in errors
    assert "id" in errors
    assert "入口文件不存在" in errors


def test_directory_without_manifest_is_ignored(env):
    helper = env.bundled / "helper_lib"
    helper.mkdir()
    (helper / "util.py").write_text("x = 1\n", "utf-8")

    env.host.load_all()
    assert env.host.plugins == []


def test_duplicate_plugin_id_is_isolated(env):
    write_plugin(env.bundled, "demo")
    write_plugin(env.user, "demo")

    env.host.load_all()

    summary = env.host.summary()
    assert summary["total"] == 2
    assert summary["loaded"] == 1
    assert summary["error"] == 1
    duplicate_errors = [record.error or "" for record in env.host.plugins if record.has_error]
    assert any("重复" in message for message in duplicate_errors)


def test_min_app_version_too_high_is_rejected(env):
    write_plugin(env.bundled, "demo", manifest={"min_app_version": "99.0.0"})
    env.host.load_all()
    record = env.host.get("demo")
    assert record.state == STATE_ERROR
    assert "99.0.0" in (record.error or "")


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def test_disable_enable_lifecycle(env):
    write_plugin(env.bundled, "demo")
    env.host.load_all()
    old_instance = env.host.get("demo").instance

    assert env.host.disable("demo") is True
    record = env.host.get("demo")
    assert record.state == STATE_DISABLED
    assert record.instance is None
    assert old_instance.disable_calls == 1
    assert old_instance.unload_calls == 1
    assert not env.registry.has("plugin_demo_demo")
    assert "demo" in (env.settings.get("plugins.disabled") or [])

    assert env.host.enable("demo") is True
    record = env.host.get("demo")
    assert record.state == STATE_LOADED
    assert record.instance is not old_instance
    assert record.instance.load_calls == 1
    assert record.instance.enable_calls == 1
    assert env.registry.has("plugin_demo_demo")
    assert "demo" not in (env.settings.get("plugins.disabled") or [])


def test_disabled_plugin_is_not_imported_until_enabled(env):
    write_plugin(env.bundled, "demo")
    env.settings.set("plugins.disabled", ["demo"])
    env.host.load_all()

    record = env.host.get("demo")
    assert record.state == STATE_DISABLED
    assert record.instance is None
    assert not env.registry.has("plugin_demo_demo")

    assert env.host.enable("demo") is True
    assert env.host.get("demo").state == STATE_LOADED


def test_reload_rebuilds_single_plugin(env):
    write_plugin(env.bundled, "demo")
    env.host.load_all()
    old_instance = env.host.get("demo").instance

    assert env.host.reload("demo") is True

    record = env.host.get("demo")
    assert record.state == STATE_LOADED
    assert record.instance is not old_instance
    assert old_instance.unload_calls == 1
    keys = [view.key for view in env.registry.classes("plugin")]
    assert keys.count("plugin_demo_demo") == 1


def test_unload_all_removes_views_and_subscriptions(env):
    code = (
        """
from endbox.plugins.api import EndBoxPlugin
"""
        + VIEW_CLASS
        + """

class Plugin(EndBoxPlugin):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.ticks = 0

    def on_load(self):
        self.ctx.event_bus.subscribe("timeline.tick", self._on_tick)
        self.ctx.register_view(DemoView)

    def _on_tick(self, **payload):
        self.ticks += 1
"""
    )
    write_plugin(env.bundled, "demo", code=code)
    env.host.load_all()
    instance = env.host.get("demo").instance

    env.ctx.bus.emit("timeline.tick", t=1.0, playing=True)
    assert instance.ticks == 1

    env.host.unload_all()
    env.ctx.bus.emit("timeline.tick", t=2.0, playing=True)
    assert instance.ticks == 1
    assert not env.registry.has("plugin_demo_demo")


# ---------------------------------------------------------------------------
# storage 持久化
# ---------------------------------------------------------------------------

STORAGE_PLUGIN = (
    """
from endbox.plugins.api import EndBoxPlugin
"""
    + VIEW_CLASS
    + """

class Plugin(EndBoxPlugin):
    def on_load(self):
        runs = int(self.ctx.storage.get("runs", 0)) + 1
        self.ctx.storage.set("runs", runs)
        self.runs = runs
        self.ctx.register_view(DemoView)
"""
)


def test_storage_persists_across_host_restarts(env):
    write_plugin(env.bundled, "storage_demo", code=STORAGE_PLUGIN)
    env.host.load_all()
    assert env.host.get("storage_demo").instance.runs == 1

    data_file = env.tmp / "plugin_data" / "storage_demo.json"
    assert data_file.is_file()
    assert json.loads(data_file.read_text("utf-8"))["runs"] == 1
    assert not list(data_file.parent.glob("*.tmp"))

    env.host.unload_all()
    second = PluginHost(
        env.ctx,
        ViewRegistry(),
        roots=[(env.bundled, "bundled")],
        data_dir=env.tmp / "plugin_data",
    )
    second.load_all()
    assert second.get("storage_demo").instance.runs == 2
    second.unload_all()


def test_plugin_storage_is_private_and_atomic(tmp_path):
    path = tmp_path / "plugin_data" / "demo.json"
    storage = PluginStorage("demo", path=path)
    storage.set("answer", 42)
    assert path.is_file()
    assert "42" in path.read_text("utf-8")

    reopened = PluginStorage("demo", path=path)
    assert reopened.get("answer") == 42
    assert not list(path.parent.glob("*.tmp"))

    # 损坏的存储文件退化为空字典，而不是抛异常
    path.write_text("{ broken", "utf-8")
    assert PluginStorage("demo", path=path).all() == {}


# ---------------------------------------------------------------------------
# 权限与事件白名单
# ---------------------------------------------------------------------------

DENIED_PLUGIN = (
    """
from endbox.plugins.api import EndBoxPlugin
"""
    + VIEW_CLASS
    + """

class Plugin(EndBoxPlugin):
    def on_load(self):
        self.results = {}
        for api in ("timeline_snapshot", "account_snapshot"):
            try:
                getattr(self.ctx, api)()
            except PermissionError as exc:
                self.results[api] = str(exc)
        try:
            self.ctx.event_bus.subscribe("account.state", lambda **_: None)
        except PermissionError as exc:
            self.results["subscribe"] = str(exc)
        self.ctx.register_view(DemoView)
"""
)


def test_permission_denied_is_recorded(env):
    write_plugin(env.bundled, "demo", code=DENIED_PLUGIN)
    env.host.load_all()

    record = env.host.get("demo")
    assert record.state == STATE_LOADED  # 越权不会导致插件崩溃
    results = record.instance.results
    assert "timeline:read" in results["timeline_snapshot"]
    assert "account:read" in results["account_snapshot"]
    assert "account.state" in results["subscribe"]


def test_granted_permission_returns_empty_snapshot_without_service(env):
    code = (
        """
from endbox.plugins.api import EndBoxPlugin
"""
        + VIEW_CLASS
        + """

class Plugin(EndBoxPlugin):
    def on_load(self):
        self.snapshot = self.ctx.timeline_snapshot()
        self.ctx.register_view(DemoView)
"""
    )
    write_plugin(env.bundled, "demo", code=code, manifest={"permissions": ["timeline:read"]})
    env.host.load_all()
    record = env.host.get("demo")
    assert record.state == STATE_LOADED
    assert record.instance.snapshot == {}


def test_granted_permission_passes_real_snapshot(env):
    class FakeTimeline:
        def snapshot(self):
            return {"duration": 12.5, "team": [], "events": [{"kind": "skill"}]}

    env.ctx.services.timeline = FakeTimeline()
    code = (
        """
from endbox.plugins.api import EndBoxPlugin
"""
        + VIEW_CLASS
        + """

class Plugin(EndBoxPlugin):
    def on_load(self):
        self.snapshot = self.ctx.timeline_snapshot()
        self.ctx.register_view(DemoView)
"""
    )
    write_plugin(env.bundled, "demo", code=code, manifest={"permissions": ["timeline:read"]})
    env.host.load_all()
    assert env.host.get("demo").instance.snapshot == {
        "duration": 12.5,
        "team": [],
        "events": [{"kind": "skill"}],
    }


def test_unknown_permission_is_ignored(env):
    write_plugin(
        env.bundled,
        "demo",
        manifest={"permissions": ["timeline:read", "root:all"]},
    )
    env.host.load_all()
    record = env.host.get("demo")
    assert record.state == STATE_LOADED
    assert record.permissions == ["timeline:read"]
    assert record.ignored_permissions == ["root:all"]


def test_snapshot_service_error_does_not_crash_plugin(env):
    class BrokenTimeline:
        def snapshot(self):
            raise RuntimeError("service exploded")

    env.ctx.services.timeline = BrokenTimeline()
    code = (
        """
from endbox.plugins.api import EndBoxPlugin
"""
        + VIEW_CLASS
        + """

class Plugin(EndBoxPlugin):
    def on_load(self):
        self.snapshot = self.ctx.timeline_snapshot()
        self.ctx.register_view(DemoView)
"""
    )
    write_plugin(env.bundled, "demo", code=code, manifest={"permissions": ["timeline:read"]})
    env.host.load_all()
    record = env.host.get("demo")
    assert record.state == STATE_LOADED
    assert record.instance.snapshot == {}


# ---------------------------------------------------------------------------
# 卸载记录 / 随程序分发的插件
# ---------------------------------------------------------------------------


def test_host_remove_drops_record_and_views(env):
    write_plugin(env.bundled, "demo")
    env.host.load_all()
    record = env.host.get("demo")
    assert record is not None
    key = record.view_keys[0]
    assert env.registry.has(key)

    assert env.host.remove("demo")
    assert env.host.get("demo") is None
    assert not env.registry.has(key)
    assert not env.host.remove("demo")


def test_repo_bundled_plugins_all_load(tmp_path, qapp):
    """仓库自带的示例插件必须都能加载（含新增的市场示例 operator_roster）。"""
    from endbox import config
    from endbox.app_context import AppContext
    from endbox.core.event_bus import EventBus
    from endbox.services import Services

    settings = Settings(tmp_path / "settings.json")
    ctx = AppContext(settings=settings, bus=EventBus(), services=Services())
    registry = ViewRegistry()
    host = PluginHost(
        ctx,
        registry,
        roots=[(config.PLUGINS_DIR, "bundled")],
        data_dir=tmp_path / "plugin_data",
    )
    try:
        records = host.load_all()
        summary = host.summary()
        assert summary["error"] == 0, [
            (record.plugin_id, record.error) for record in records if record.error
        ]
        ids = {record.plugin_id for record in records}
        assert {"timers", "timeline_stats", "permission_demo", "operator_roster"} <= ids
        assert summary["loaded"] >= 4
    finally:
        host.unload_all()
