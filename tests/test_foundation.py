# -*- coding: utf-8 -*-
"""基础层测试：设计 token、绘制、部件、视图注册、325 计算器、加密存储。

    python run_tests.py tests/test_foundation.py -q
"""
from __future__ import annotations

import json

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap


# ---------------------------------------------------------------- 325 计算器
def test_calc325_is_deterministic():
    from endbox.modules.calc325.generator import make_puzzle

    a = make_puzzle("20261113")
    b = make_puzzle("20261113")
    assert a.expression == b.expression
    assert a.result == 325
    assert sum(t.value for t in a.terms) == 325


def test_calc325_printed_formula_evaluates_to_325():
    """显示出来的算式（不是内部数值）本身必须能算出 325。"""
    from endbox.modules.calc325.generator import make_puzzle

    for seed, seed_type, complexity in (
        ("20261113", "number", 1),
        ("20261113", "number", 2),
        ("20261113", "number", 3),
        ("20261113", "number", 4),
        ("ZcZcZcZc", "zc", 2),
        ("ZcZcZcZc", "zc", 4),
    ):
        puzzle = make_puzzle(seed, complexity=complexity, seed_type=seed_type)
        expression = puzzle.expression.replace("×", "*").replace("÷", "//").replace("−", "-")
        assert eval(expression) == 325, (seed, expression)


def test_calc325_zc_seed_mode():
    from endbox.modules.calc325.generator import make_puzzle, parse_seed, seed_bits

    assert parse_seed("zc", "zc") == 0
    assert parse_seed("Zc", "zc") == 2  # 1,0 -> 10b = 2
    assert parse_seed("ZcZcZc", "zc") == 42
    assert seed_bits("ZcZc", "zc") == "1010"
    puzzle = make_puzzle("ZcZcZc", complexity=2, seed_type="zc")
    assert puzzle.seed_type == "zc"
    assert sum(t.value for t in puzzle.terms) == 325
    # 同一个 Zc 种子 + 同一复杂度 → 同一条式子
    assert puzzle.expression == make_puzzle("ZcZcZc", complexity=2, seed_type="zc").expression


def test_calc325_invalid_zc_seed():
    import pytest

    from endbox.modules.calc325.generator import parse_seed

    with pytest.raises(ValueError):
        parse_seed("abc", "zc")
    with pytest.raises(ValueError):
        parse_seed("12a", "number")


def test_calc325_complexity_levels():
    from endbox.modules.calc325.generator import make_puzzle

    simple = make_puzzle("2026", complexity=1)
    complex_ = make_puzzle("2026", complexity=4)
    assert sum(t.value for t in simple.terms) == 325
    assert sum(t.value for t in complex_.terms) == 325
    assert len(complex_.expression) > len(simple.expression)
    assert len(complex_.terms) > len(simple.terms)


def test_calc325_seed_changes_formula():
    from endbox.modules.calc325.generator import make_puzzle

    assert make_puzzle("20261113").expression != make_puzzle("325").expression


def test_calc325_all_terms_are_consistent():
    from endbox.modules.calc325.generator import make_puzzle

    for seed in ("1", "2", "325", "2026", "20260101"):
        puzzle = make_puzzle(seed)
        assert sum(t.value for t in puzzle.terms) == 325
        rows = puzzle.steps()
        assert rows[-1][0] == "Σ"
        assert rows[-1][1].endswith("= 325")


# ---------------------------------------------------------------- 安全存储
def test_secure_store_roundtrip(tmp_path):
    from endbox.core.secure_store import SecureStore

    store = SecureStore(tmp_path / "secure.dat")
    store.set("bili.sessdata", "secret-value-123")
    assert store.get("bili.sessdata") == "secret-value-123"
    # 新实例重新解密
    reloaded = SecureStore(tmp_path / "secure.dat")
    assert reloaded.get("bili.sessdata") == "secret-value-123"
    # 落盘内容不能是明文
    raw = (tmp_path / "secure.dat").read_bytes()
    assert b"secret-value-123" not in raw
    reloaded.delete("bili.sessdata")
    assert reloaded.get("bili.sessdata") is None


# ---------------------------------------------------------------- 事件总线
def test_event_bus_exact_and_prefix():
    from endbox.core.event_bus import EventBus

    bus = EventBus()
    seen = []
    bus.subscribe("timeline.tick", lambda **kw: seen.append(("exact", kw)))
    bus.subscribe_prefix("timeline.", lambda **kw: seen.append(("prefix", kw)))
    bus.subscribe("other.topic", lambda **kw: seen.append(("other", kw)))
    bus.emit("timeline.tick", t=1.5)
    assert seen == [("exact", {"t": 1.5}), ("prefix", {"t": 1.5})]


def test_event_bus_isolates_handler_errors():
    from endbox.core.event_bus import EventBus

    bus = EventBus()
    calls = []

    def boom(**_):
        raise RuntimeError("handler bug")

    bus.subscribe("x", boom)
    bus.subscribe("x", lambda **_: calls.append(1))
    bus.emit("x")
    assert calls == [1]


# ---------------------------------------------------------------- 主题 / 绘制
def test_theme_switch(qapp):
    from endbox.theme.tokens import theme

    theme.set_mode("dark", emit=False)
    dark_ink = theme.p.ink.name()
    theme.set_mode("light", emit=False)
    light_ink = theme.p.ink.name()
    assert dark_ink != light_ink
    assert theme.p.signal.name() == "#fffa00"
    theme.set_mode("dark", emit=False)


def test_paint_helpers(qapp):
    from endbox.theme import paint

    path = paint.cut_rect(QRectF(0, 0, 100, 40), 8, "br")
    assert not path.isEmpty()
    mask = paint.window_mask(400, 300, 16)
    assert not mask.isEmpty()
    pix = QPixmap(120, 60)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    paint.paint_tri_strip(p, QRectF(0, 0, 120, 3), __import__("endbox.theme.tokens", fromlist=["theme"]).theme.p)
    paint.paint_hatch(p, QRectF(0, 10, 120, 20), QColor("#EDEDED"), 0.22)
    paint.paint_contours(p, QRectF(0, 0, 120, 60), QColor("#353535"))
    paint.hairline(p, 0, 59, 120, 59, QColor("#353535"))
    p.end()


def test_every_icon_paints(qapp):
    from endbox.ui import icons

    for name in icons.ICONS:
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        icons.paint_icon(painter, name, QRectF(0, 0, 32, 32), QColor("#EDEDED"))
        painter.end()
        assert not pix.isNull()


# ---------------------------------------------------------------- 部件与视图
def test_kit_widgets_construct(qapp):
    from endbox.ui.kit import (
        Badge,
        CutButton,
        EmptyState,
        FieldText,
        HatchBand,
        IconButton,
        Label,
        NumberStat,
        Panel,
        ProgressBar,
        QRCanvas,
        ScrollArea,
        SectionHead,
        Segmented,
        Toggle,
    )

    widgets = [
        Label("测试", "body"),
        SectionHead("TEST", "测试"),
        Badge("No.024"),
        FieldText(["2026.11.13", "6,380"]),
        NumberStat("事件", "12"),
        CutButton("按钮", "primary", icon="spark"),
        IconButton("settings"),
        Segment_ := Segmented(["A", "B"]),
        Toggle("开关", checked=True),
        Panel("标题", "TEST"),
        ScrollArea(),
        EmptyState("这里还没有内容", "添加一条记录后会出现"),
        HatchBand(),
        ProgressBar(),
        QRCanvas(),
    ]
    for widget in widgets:
        widget.repaint()
    assert Segment_.sizeHint().width() > 0


def test_button_hover_wipe_resets(qapp):
    """悬停黄色必须能退回（用户反馈过不消失的 BUG）。"""
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QEnterEvent
    from PyQt6.QtTest import QTest

    from endbox.ui.kit import CutButton

    button = CutButton("测试", "default", icon="spark")
    button.resize(button.sizeHint())
    enter = QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5))
    qapp.sendEvent(button, enter)
    QTest.qWait(360)
    assert button._wipe > 0.95
    qapp.sendEvent(button, QEvent(QEvent.Type.Leave))
    QTest.qWait(360)
    assert button._wipe < 0.05


def test_view_registry_and_window(qapp, tmp_settings):
    from endbox.app_context import AppContext
    from endbox.core.event_bus import EventBus
    from endbox.services import Services
    from endbox.ui.main_window import MainWindow
    from endbox.ui.views.base import BaseView, ViewRegistry

    class AlphaView(BaseView):
        key = "alpha"
        eyebrow = "ALPHA"
        title = "阿尔法"
        icon = "grid"
        order = 10

        def build(self):
            from endbox.ui.kit import Label

            self.add(Label("alpha view", "body"))

    class PluginView(BaseView):
        key = "plugin_x"
        eyebrow = "PLUGIN"
        title = "插件视图"
        icon = "spark"
        order = 1
        section = "plugin"

        def build(self):
            from endbox.ui.kit import Label

            self.add(Label("plugin view", "body"))

    registry = ViewRegistry()
    registry.register(AlphaView)
    registry.register(PluginView)
    assert [c.key for c in registry.classes()] == ["alpha", "plugin_x"]
    assert [c.key for c in registry.classes("plugin")] == ["plugin_x"]

    ctx = AppContext(settings=tmp_settings, bus=EventBus(), services=Services())
    window = MainWindow(ctx, registry)
    window.show_view("alpha")
    assert window.stack.currentWidget().key == "alpha"
    window.show_view("plugin_x")
    assert window.stack.currentWidget().key == "plugin_x"
    window.close()


def test_startup_always_opens_home(qapp, tmp_settings):
    """用户要求：每次打开都进首页（不看 last_view）；点开工具仍会记录 last_view。"""
    from endbox.app_context import AppContext
    from endbox.core.event_bus import EventBus
    from endbox.services import Services
    from endbox.ui.main_window import MainWindow
    from endbox.ui.views.base import BaseView, ViewRegistry

    class HomeView(BaseView):
        key = "home"
        eyebrow = "HOME"
        title = "终末地工具箱"
        icon = "grid"
        order = 5

        def build(self):
            from endbox.ui.kit import Label

            self.add(Label("home", "body"))

    class ToolView(BaseView):
        key = "tool"
        eyebrow = "TOOL"
        title = "工具"
        icon = "grid"
        order = 10

        def build(self):
            from endbox.ui.kit import Label

            self.add(Label("tool", "body"))

    from PyQt6.QtTest import QTest

    tmp_settings.set("app.first_run", False)
    tmp_settings.set("app.last_view", "tool")  # 上次停在工具页，重启也必须回首页
    registry = ViewRegistry()
    registry.register(HomeView)
    registry.register(ToolView)
    ctx = AppContext(settings=tmp_settings, bus=EventBus(), services=Services())
    window = MainWindow(ctx, registry)
    QTest.qWait(50)  # 让启动时的 singleShot 生效
    assert window.stack.currentWidget().key == "home"
    window.show_view("tool")
    assert window.stack.currentWidget().key == "tool"
    assert tmp_settings.get("app.last_view") == "tool"
    window.close()


def test_portable_data_dir_and_migration(tmp_path, monkeypatch):
    """数据默认落在软件目录 userdata/；旧 %APPDATA% 数据可迁移；不可写时回退。"""
    import json

    from endbox import config

    fake_root = tmp_path / "app"
    fake_root.mkdir()
    fake_appdata = tmp_path / "roaming"
    legacy = fake_appdata / "EndBox"
    legacy.mkdir(parents=True)
    (legacy / "settings.json").write_text(json.dumps({"app": {"theme": "light"}}), "utf-8")
    (legacy / "timeline.json").write_text("{}", "utf-8")
    monkeypatch.setattr(config, "ROOT", fake_root)
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.delenv("ENDBOX_DATA_DIR", raising=False)

    data_dir = config._data_dir()
    assert data_dir == fake_root / "userdata"
    assert (data_dir / "settings.json").exists()  # 旧数据已迁移
    assert (data_dir / "timeline.json").exists()


def test_app_icon_assets_load(qapp):
    from endbox.ui.branding import app_icon, logo_pixmap

    icon = app_icon()
    assert not icon.isNull()
    assert logo_pixmap(64).width() == 64


def test_latex_view_renders_or_falls_back(qapp):
    """325 计算器的公式要能渲染成图形；matplotlib 不可用时必须安全退回文本。"""
    from endbox.ui.kit import LaTeXView

    view = LaTeXView(fontsize=18)
    view.resize(600, 120)
    view.set_latex(r"\left(4 \times 4\right) + \frac{172+3}{5} = 325", text="(4 × 4) + (172+3)/5 = 325")
    QPixmap_cache = view.grab()
    assert not QPixmap_cache.isNull()
    assert view.sizeHint().height() >= 48
    view.refresh_theme()


def test_confirm_dialog_construct_and_accept(qapp):
    from PyQt6.QtWidgets import QDialog

    from endbox.ui.kit import ConfirmDialog

    dialog = ConfirmDialog(None, title="确认删除", message="删除后无法恢复", danger=True)
    assert dialog.ok_button.text()  # 有确认按钮
    assert dialog.cancel_button.text()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    reset = ConfirmDialog(None, title="恢复默认", message="重置外观", danger=False)
    assert reset.ok_button.text()
    reset.reject()
    assert reset.result() == QDialog.DialogCode.Rejected


def test_app_icon_is_transparent(qapp):
    from PyQt6.QtGui import QImage

    from endbox import config

    image = QImage(str(config.ASSETS / "branding" / "endbox.png"))
    assert not image.isNull()
    assert image.hasAlphaChannel()
    assert image.pixelColor(0, 0).alpha() == 0
    ico = config.ASSETS / "branding" / "endbox.ico"
    assert ico.exists() and ico.stat().st_size > 0


def test_text_catalog_keys_exist(qapp):
    """所有静态 t("...") 的 key 都必须在文案 JSON 里存在（防手滑）。"""
    import re
    from pathlib import Path

    from endbox.core.text import catalog

    root = Path(__file__).resolve().parents[1] / "endbox"
    pattern = re.compile(r"""\bt\(\s*["']([a-zA-Z0-9_.]+)["']""")
    missing = []
    for path in root.rglob("*.py"):
        if "thirdparty" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name == "text.py" and path.parent.name == "core":
            continue  # 模块文档里的示例 key 不是实际调用
        for match in pattern.finditer(path.read_text("utf-8", errors="ignore")):
            key = match.group(1)
            if not catalog.has(key):
                missing.append((str(path.relative_to(root)), key))
    assert not missing, missing[:10]


def test_settings_roundtrip(tmp_path):
    from endbox.config import Settings

    path = tmp_path / "s.json"
    settings = Settings(path)
    settings.set("hud.scale", 1.25)
    settings.set("timeline.team", ["伊冯", "余烬"])
    again = Settings(path)
    assert again.get("hud.scale") == 1.25
    assert again.get("timeline.team") == ["伊冯", "余烬"]
    assert json.loads(path.read_text("utf-8"))["hud"]["scale"] == 1.25
