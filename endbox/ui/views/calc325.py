# -*- coding: utf-8 -*-
"""325 计算器：求解模式（区间 DP）+ 随机模式（L1–L5 / Zc 种子）+ 历史收藏。

参考 Calculate325-main 的两块核心：
  · 求解：数字串 + 最多块数 + 顺序/阶乘/小数/高级运算 → 精确算式 + 耗时；
  · 随机：等级 + 固定长度种子（数字 / Zc）→ 因子链 + 经典数学模块，
    生成后由独立 AST 复算，必须严格等于 325。

界面沿用 EndBox 的细线 / 切角 / 黄色只填充规则；数据写入 ``config.DATA_DIR``。
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont, QImage, QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from endbox import config
from endbox.core.text import t
from endbox.core.workers import run_async
from endbox.modules.calc325 import levels, random_expr, solver
from endbox.modules.calc325.history import CalcEntry, FavoriteStore, HistoryStore
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.ui.kit import (
    BusyBar,
    CutButton,
    FieldText,
    IconButton,
    InfoFlag,
    Label,
    LaTeXView,
    Panel,
    ScrollArea,
    Segmented,
    Toggle,
    confirm,
)
from endbox.ui.kit.base import EWidget, pal
from endbox.ui.views.base import BaseView

_IMAGE_W = 1400


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class _FactorRow(EWidget):
    """随机模式的一行因子：编号 + Unicode 算式 + 精确值 + 嵌套层数。"""

    def __init__(self, index: int, factor: random_expr.Factor, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(8)
        index_label = Label(f"{index:02d}", "field")
        index_label.setFixedWidth(28)
        layout.addWidget(index_label, 0, Qt.AlignmentFlag.AlignTop)
        pretty = Label(factor.pretty, "body", wrap=True)
        pretty.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(pretty, 1)
        value = Label(f"= {factor.value_text}", "field")
        layout.addWidget(value, 0, Qt.AlignmentFlag.AlignTop)
        if factor.depth:
            layout.addWidget(Label(t("calc325.factors.depth_tag", depth=factor.depth), "small"), 0, Qt.AlignmentFlag.AlignTop)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        colors = pal()
        paint.hairline(p, 0, self.height() - 1, self.width(), self.height() - 1, colors.rule)
        p.end()


class _CalcRecordRow(QAbstractButton):
    """历史 / 收藏的一行（RecordRow 风格）：可点击，悬停黄块压暗。"""

    def __init__(self, title: str, subtitle: str, right_text: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._subtitle = subtitle
        self._right = right_text
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 2.0)
        if self._hover:
            p.fillPath(paint.cut_rect(rect, 6, "br"), colors.chip)
        if self.hasFocus():
            paint.stroke_cut(p, rect.adjusted(1.5, 1.5, -1.5, -1.5), colors.trace, 5, "br", 1.5)
        paint.draw_text(
            p, QRectF(8, 4, self.width() - 200, 20), self._title,
            F.font(13, weight=QFont.Weight.Bold, cjk_first=True), colors.ink, elide=True,
        )
        paint.draw_text(
            p, QRectF(8, 22, self.width() - 200, 16), self._subtitle,
            F.font(11, cjk_first=True), colors.ink2, elide=True,
        )
        paint.draw_text(
            p, QRectF(self.width() - 186, 0, 178, self.height()), self._right,
            F.field_font(11), colors.ink2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        paint.hairline(p, 0, self.height() - 1, self.width(), self.height() - 1, colors.rule)
        p.end()


class Calc325View(BaseView):
    key = "calc325"
    eyebrow = "325 CALCULATOR"
    title = t("calc325.title")
    icon = "calc"
    order = 40
    sidebar_label = t("calc325.sidebar_label")
    description = t("calc325.description")

    # ------------------------------------------------------------ 构建
    def build(self) -> None:
        self._mode = "random"
        self._equation: random_expr.RandomEquation | None = None
        self._canonical_seed = ""
        self._solve_result: solver.SolveResult | None = None
        self._solve_alternatives: list[dict] = []
        self._solve_choice = 0
        self._solve_view_mode = "latex"
        self._solve_busy = False
        self._solve_generation = 0
        self._loading_state = True
        self._seed_timer = QTimer(self)
        self._seed_timer.setSingleShot(True)
        self._seed_timer.setInterval(450)
        self._seed_timer.timeout.connect(self._debounced_generate)
        self._history = HistoryStore()
        self._favorites = FavoriteStore()
        self._history_filter_level = 0

        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 顶部：模式切换
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(12)
        top_layout.addStretch(1)
        top_layout.addWidget(Label(t("calc325.mode"), "body"))
        self._mode_seg = Segmented([t("calc325.mode.solve"), t("calc325.mode.random")], index=0)
        self._mode_seg.changed.connect(self._on_mode_changed)
        top_layout.addWidget(self._mode_seg)
        layout.addWidget(top)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._solve_page = self._build_solve_page()
        self._random_page = self._build_random_page()
        self._stack.addWidget(self._solve_page)
        self._stack.addWidget(self._random_page)
        layout.addWidget(self._stack, 1)
        layout.addStretch(1)

        scroll.set_content(host)
        self._root.addWidget(scroll)

        # 快捷键只在视图聚焦时生效
        for sequence, handler in (
            ("Ctrl+Return", self._on_primary_shortcut),
            ("Ctrl+Enter", self._on_primary_shortcut),
            ("Ctrl+R", self._on_random_shortcut),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)

        self._apply_saved_state()
        self._loading_state = False

    # ------------------------------------------------------------ 求解页
    def _build_solve_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        form = Panel(t("calc325.solve.panel"), "SOLVE", padding=16, spacing=10)

        row_target = QWidget()
        row_target_layout = QHBoxLayout(row_target)
        row_target_layout.setContentsMargins(0, 0, 0, 0)
        row_target_layout.setSpacing(8)
        row_target_layout.addWidget(Label(t("calc325.solve.target"), "body"))
        self._target_input = QLineEdit("325")
        self._target_input.setFixedHeight(32)
        self._target_input.setFixedWidth(130)
        self._target_input.returnPressed.connect(self._start_solve)
        row_target_layout.addWidget(self._target_input)
        for example in ("325", "1000", "2025", "62", "3.25", "-7"):
            chip = CutButton(example, "chip", compact=True)
            chip.clicked.connect(lambda _checked=False, text=example: self._use_target(text))
            row_target_layout.addWidget(chip)
        row_target_layout.addStretch(1)
        form.add(row_target)

        row_pattern = QWidget()
        row_pattern_layout = QHBoxLayout(row_pattern)
        row_pattern_layout.setContentsMargins(0, 0, 0, 0)
        row_pattern_layout.setSpacing(8)
        row_pattern_layout.addWidget(Label(t("calc325.solve.pattern"), "body"))
        self._pattern_seg = Segmented(
            [str(solver.BUILTIN_PATTERNS[0]), str(solver.BUILTIN_PATTERNS[1]), str(solver.BUILTIN_PATTERNS[2]), str(solver.BUILTIN_PATTERNS[3]), t("calc325.solve.pattern.custom"), t("calc325.solve.pattern.all")], index=0
        )
        self._pattern_seg.changed.connect(self._on_pattern_changed)
        row_pattern_layout.addWidget(self._pattern_seg)
        self._custom_pattern = QLineEdit()
        self._custom_pattern.setPlaceholderText(t("calc325.solve.custom_placeholder"))
        self._custom_pattern.setFixedHeight(30)
        self._custom_pattern.setFixedWidth(180)
        self._custom_pattern.hide()
        row_pattern_layout.addWidget(self._custom_pattern)
        row_pattern_layout.addSpacing(10)
        row_pattern_layout.addWidget(Label(t("calc325.solve.max_blocks"), "body"))
        self._blocks_seg = Segmented(["1", "2", "3", "4", "5", "6"], index=1)
        row_pattern_layout.addWidget(self._blocks_seg)
        row_pattern_layout.addStretch(1)
        form.add(row_pattern)

        row_rules = QWidget()
        row_rules_layout = QHBoxLayout(row_rules)
        row_rules_layout.setContentsMargins(0, 0, 0, 0)
        row_rules_layout.setSpacing(12)
        row_rules_layout.addWidget(Label(t("calc325.solve.order"), "body"))
        self._order_seg = Segmented([t("calc325.solve.order.fixed"), t("calc325.solve.order.any")], index=0)
        row_rules_layout.addWidget(self._order_seg)
        self._factorial_toggle = Toggle(t("calc325.solve.factorial"), checked=True)
        row_rules_layout.addWidget(self._factorial_toggle)
        row_rules_layout.addWidget(Label(t("calc325.solve.fact_limit"), "body"))
        self._factmax_seg = Segmented(["3", "5", "8", "12"], index=3)
        row_rules_layout.addWidget(self._factmax_seg)
        self._decimal_toggle = Toggle(t("calc325.solve.decimal"), checked=True)
        row_rules_layout.addWidget(self._decimal_toggle)
        row_rules_layout.addStretch(1)
        form.add(row_rules)

        row_advanced = QWidget()
        row_advanced_layout = QHBoxLayout(row_advanced)
        row_advanced_layout.setContentsMargins(0, 0, 0, 0)
        row_advanced_layout.setSpacing(12)
        row_advanced_layout.addWidget(Label(t("calc325.solve.advanced"), "body"))
        self._power_toggle = Toggle(t("calc325.solve.power"), checked=False)
        self._sqrt_toggle = Toggle(t("calc325.solve.sqrt"), checked=False)
        self._comb_toggle = Toggle(t("calc325.solve.comb"), checked=False)
        self._gcd_toggle = Toggle("gcd·mod", checked=False)
        for toggle in (self._power_toggle, self._sqrt_toggle, self._comb_toggle, self._gcd_toggle):
            row_advanced_layout.addWidget(toggle)
        row_advanced_layout.addStretch(1)
        form.add(row_advanced)

        row_run = QWidget()
        row_run_layout = QHBoxLayout(row_run)
        row_run_layout.setContentsMargins(0, 0, 0, 0)
        row_run_layout.setSpacing(10)
        row_run_layout.addWidget(Label(t("calc325.solve.time_budget"), "body"))
        self._budget_seg = Segmented([t("calc325.solve.budget.2"), t("calc325.solve.budget.5"), t("calc325.solve.budget.8")], index=1)
        row_run_layout.addWidget(self._budget_seg)
        self._solve_btn = CutButton(t("calc325.solve.run"), "primary", icon="spark")
        self._solve_btn.clicked.connect(self._start_solve)
        row_run_layout.addWidget(self._solve_btn)
        self._solve_busy_bar = BusyBar()
        self._solve_busy_bar.setFixedWidth(180)
        row_run_layout.addWidget(self._solve_busy_bar)
        row_run_layout.addStretch(1)
        form.add(row_run)

        self._solve_flag = InfoFlag(
            t("calc325.solve.rule"),
            kind="info",
        )
        form.add(self._solve_flag)
        layout.addWidget(form)

        result_panel = Panel(t("calc325.result.panel"), "RESULT", padding=16, spacing=8)
        self._solve_fields = FieldText(["—"])
        result_panel.add(self._solve_fields)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        self._solve_view_seg = Segmented([t("calc325.result.view.rendered"), t("calc325.result.view.plain")], index=0)
        self._solve_view_seg.changed.connect(self._on_solve_view_changed)
        controls_layout.addWidget(self._solve_view_seg)
        controls_layout.addStretch(1)
        self._copy_solve_btn = CutButton(t("calc325.result.copy_ascii"), "ghost", icon="copy")
        self._copy_solve_btn.clicked.connect(self._copy_solve_expression)
        controls_layout.addWidget(self._copy_solve_btn)
        self._copy_solve_latex_btn = CutButton(t("calc325.result.copy_latex"), "ghost", icon="copy")
        self._copy_solve_latex_btn.clicked.connect(self._copy_solve_latex)
        controls_layout.addWidget(self._copy_solve_latex_btn)
        result_panel.add(controls)

        self._solve_latex_view = LaTeXView(fontsize=18)
        result_panel.add(self._solve_latex_view)
        self._solve_expr = Label(t("calc325.result.placeholder"), "body", wrap=True)
        self._solve_expr.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._solve_expr.setFont(F.font(16, cjk_first=True))
        self._solve_expr.hide()
        result_panel.add(self._solve_expr)
        self._solve_alt_label = Label("", "small", wrap=True)
        self._solve_alt_label.hide()
        result_panel.add(self._solve_alt_label)
        self._solve_alt_host = QWidget()
        self._solve_alt_layout = QVBoxLayout(self._solve_alt_host)
        self._solve_alt_layout.setContentsMargins(0, 0, 0, 0)
        self._solve_alt_layout.setSpacing(4)
        result_panel.add(self._solve_alt_host)
        layout.addWidget(result_panel)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------ 随机页
    def _build_random_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        seed_panel = Panel(t("calc325.seed.panel"), "SEED", padding=16, spacing=10)
        row_level = QWidget()
        row_level_layout = QHBoxLayout(row_level)
        row_level_layout.setContentsMargins(0, 0, 0, 0)
        row_level_layout.setSpacing(8)
        row_level_layout.addWidget(Label(t("calc325.seed.level"), "body"))
        self._level_seg = Segmented(["L1", "L2", "L3", "L4", "L5"], index=1)
        self._level_seg.changed.connect(self._on_level_changed)
        row_level_layout.addWidget(self._level_seg)
        row_level_layout.addSpacing(14)
        row_level_layout.addWidget(Label(t("calc325.seed.type"), "body"))
        self._type_seg = Segmented([t("calc325.seed.type.number"), t("calc325.seed.type.zc")], index=0)
        self._type_seg.changed.connect(self._on_seed_type_changed)
        row_level_layout.addWidget(self._type_seg)
        row_level_layout.addStretch(1)
        self._seed_meta = FieldText(["—"])
        row_level_layout.addWidget(self._seed_meta, 0, Qt.AlignmentFlag.AlignVCenter)
        seed_panel.add(row_level)

        row_seed = QWidget()
        row_seed_layout = QHBoxLayout(row_seed)
        row_seed_layout.setContentsMargins(0, 0, 0, 0)
        row_seed_layout.setSpacing(8)
        row_seed_layout.addWidget(Label(t("calc325.seed.label"), "body"))
        self._seed_input = QLineEdit()
        self._seed_input.setFixedHeight(32)
        self._seed_input.setPlaceholderText(t("calc325.seed.placeholder"))
        self._seed_input.returnPressed.connect(self._generate_random)
        self._seed_input.textChanged.connect(self._update_seed_preview)
        row_seed_layout.addWidget(self._seed_input, 1)
        self._random_btn = CutButton(t("calc325.seed.random"), "ghost", icon="refresh")
        self._random_btn.clicked.connect(self._random_seed)
        row_seed_layout.addWidget(self._random_btn)
        self._generate_btn = CutButton(t("calc325.seed.generate"), "primary", icon="spark")
        self._generate_btn.clicked.connect(self._generate_random)
        row_seed_layout.addWidget(self._generate_btn)
        seed_panel.add(row_seed)

        self._seed_preview_label = Label(t("calc325.seed.preview_none"), "mono", wrap=True)
        self._seed_preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        seed_panel.add(self._seed_preview_label)
        self._seed_flag = InfoFlag(
            t("calc325.seed.flag"), kind="info"
        )
        seed_panel.add(self._seed_flag)
        layout.addWidget(seed_panel)

        formula_panel = Panel(t("calc325.formula.panel"), "FORMULA", padding=16, spacing=8)
        self._formula_fields = FieldText(["—"])
        formula_panel.add(self._formula_fields)
        self._formula_host = QWidget()
        self._formula_layout = QVBoxLayout(self._formula_host)
        self._formula_layout.setContentsMargins(0, 0, 0, 0)
        self._formula_layout.setSpacing(2)
        formula_panel.add(self._formula_host)
        self._formula_views: list[LaTeXView] = []
        self._exact_label = Label("", "mono", wrap=True)
        self._exact_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        formula_panel.add(self._exact_label)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        for text, icon, handler in (
            (t("calc325.formula.copy"), "copy", self._copy_random_expression),
            (t("calc325.formula.copy_latex"), "copy", self._copy_random_latex),
            (t("calc325.formula.copy_seed"), "copy", self._copy_random_seed),
        ):
            button = CutButton(text, "ghost", icon=icon)
            button.clicked.connect(handler)
            actions_layout.addWidget(button)
        self._favorite_btn = CutButton(t("calc325.formula.favorite"), "default", icon="spark")
        self._favorite_btn.clicked.connect(self._toggle_favorite)
        actions_layout.addWidget(self._favorite_btn)
        save_btn = CutButton(t("calc325.formula.save_png"), "ghost", icon="download")
        save_btn.clicked.connect(self._save_png)
        actions_layout.addWidget(save_btn)
        actions_layout.addStretch(1)
        formula_panel.add(actions)
        layout.addWidget(formula_panel)

        steps_panel = Panel(t("calc325.factors.panel"), "FACTORS", padding=16, spacing=2)
        self._steps_host = QWidget()
        self._steps_layout = QVBoxLayout(self._steps_host)
        self._steps_layout.setContentsMargins(0, 0, 0, 0)
        self._steps_layout.setSpacing(0)
        steps_panel.add(self._steps_host)
        layout.addWidget(steps_panel)

        history_panel = Panel(t("calc325.history.panel"), "HISTORY", padding=16, spacing=8)
        clear_btn = CutButton(t("calc325.history.clear"), "danger", icon="trash", compact=True)
        clear_btn.clicked.connect(self._clear_history)
        history_panel.add_header_action(clear_btn)
        open_btn = CutButton(t("calc325.history.open_dir"), "ghost", icon="folder", compact=True)
        open_btn.clicked.connect(self._open_data_dir)
        history_panel.add_header_action(open_btn)
        self._history_filter = Segmented([t("calc325.history.filter.all"), "L1", "L2", "L3", "L4", "L5"], index=0)
        self._history_filter.changed.connect(self._on_history_filter)
        history_panel.add(self._history_filter)
        self._history_host = QWidget()
        self._history_layout = QVBoxLayout(self._history_host)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(0)
        history_panel.add(self._history_host)
        self._history_field = FieldText(["—"])
        history_panel.add(self._history_field)
        layout.addWidget(history_panel)

        favorites_panel = Panel(t("calc325.favorites.panel"), "FAVORITES", padding=16, spacing=8)
        clear_fav = CutButton(t("calc325.favorites.clear"), "danger", icon="trash", compact=True)
        clear_fav.clicked.connect(self._clear_favorites)
        favorites_panel.add_header_action(clear_fav)
        self._favorites_host = QWidget()
        self._favorites_layout = QVBoxLayout(self._favorites_host)
        self._favorites_layout.setContentsMargins(0, 0, 0, 0)
        self._favorites_layout.setSpacing(0)
        favorites_panel.add(self._favorites_host)
        self._favorites_field = FieldText(["—"])
        favorites_panel.add(self._favorites_field)
        layout.addWidget(favorites_panel)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------ 状态恢复
    def _apply_saved_state(self) -> None:
        ctx = self.ctx
        mode = str(ctx.settings.get("calc325.mode", "random") or "random")
        self._mode = "solve" if mode == "solve" else "random"
        self._mode_seg.set_index(0 if self._mode == "solve" else 1)

        # 求解参数
        pattern = str(ctx.settings.get("calc325.solve_pattern", "325") or "325")
        if pattern in solver.BUILTIN_PATTERNS:
            self._pattern_seg.set_index(solver.BUILTIN_PATTERNS.index(pattern))
        elif pattern == "all":
            self._pattern_seg.set_index(5)
        else:
            self._pattern_seg.set_index(4)
            self._custom_pattern.setText(pattern)
            self._custom_pattern.show()
        self._blocks_seg.set_index(max(0, min(5, int(ctx.settings.get("calc325.solve_max_blocks", 2) or 2) - 1)))
        self._order_seg.set_index(1 if ctx.settings.get("calc325.solve_order", "fixed") == "any" else 0)
        self._factorial_toggle.setChecked(bool(ctx.settings.get("calc325.solve_factorial", True)))
        fact_max = int(ctx.settings.get("calc325.solve_fact_max", 12) or 12)
        self._factmax_seg.set_index({3: 0, 5: 1, 8: 2, 12: 3}.get(fact_max, 3))
        self._decimal_toggle.setChecked(bool(ctx.settings.get("calc325.solve_decimal", True)))
        advanced = ctx.settings.get("calc325.solve_advanced", {}) or {}
        if isinstance(advanced, dict):
            self._power_toggle.setChecked(bool(advanced.get("power", False)))
            self._sqrt_toggle.setChecked(bool(advanced.get("sqrt", False)))
            self._comb_toggle.setChecked(bool(advanced.get("comb", False)))
            self._gcd_toggle.setChecked(bool(advanced.get("gcd_mod", False)))
        budget = int(ctx.settings.get("calc325.solve_time_budget", 5000) or 5000)
        self._budget_seg.set_index({2000: 0, 5000: 1, 8000: 2}.get(budget, 1))
        target = str(ctx.settings.get("calc325.solve_target", "325") or "325")
        try:
            solver.parse_target(target)
        except solver.SolveError:
            target = "325"
        self._target_input.setText(target)

        # 随机参数
        level = levels.resolve_level(
            ctx.settings.get("calc325.level"), ctx.settings.get("calc325.complexity")
        )
        self._level_seg.set_index(level - 1)
        seed_type = str(ctx.settings.get("calc325.seed_type", "number") or "number")
        self._type_seg.set_index(0 if seed_type != "zc" else 1)
        saved_seed = str(ctx.settings.get("calc325.last_seed", "") or "")
        try:
            canonical = levels.parse_seed_input(saved_seed, seed_type, level)
        except ValueError:
            canonical = levels.default_seed(level)
        self._canonical_seed = canonical
        self._seed_input.setText(levels.seed_display(canonical, seed_type))
        self._update_seed_preview()
        self._sync_stack()

        if self._mode == "solve":
            self._start_solve()
        else:
            self._generate_random()

    def _sync_stack(self) -> None:
        self._stack.setCurrentIndex(0 if self._mode == "solve" else 1)
        self._mode_seg.set_index(0 if self._mode == "solve" else 1)

    # ------------------------------------------------------------ 模式 / 选项
    def _on_mode_changed(self, index: int) -> None:
        self._mode = "solve" if index == 0 else "random"
        self._sync_stack()
        self.ctx.settings.set("calc325.mode", self._mode, save=False)
        self._sync_topbar()
        if self._loading_state:
            return
        if self._mode == "solve" and self._solve_result is None:
            self._start_solve()
        elif self._mode == "random" and self._equation is None:
            self._generate_random()

    def _on_pattern_changed(self, index: int) -> None:
        self._custom_pattern.setVisible(index == 4)
        self.ctx.settings.set("calc325.solve_pattern", self._current_pattern_setting(), save=False)

    def _current_pattern_setting(self) -> str:
        index = self._pattern_seg.index
        if index < 4:
            return solver.BUILTIN_PATTERNS[index]
        if index == 5:
            return "all"
        return self._custom_pattern.text().strip()

    def _on_solve_view_changed(self, index: int) -> None:
        self._solve_view_mode = "latex" if index == 0 else "plain"
        self._render_solve_expression()

    def _on_history_filter(self, index: int) -> None:
        self._history_filter_level = 0 if index == 0 else index
        self._refresh_history()

    def _use_target(self, text: str) -> None:
        self._target_input.setText(text)
        self._start_solve()

    # ------------------------------------------------------------ 求解
    def _collect_solve_options(self) -> solver.SolveOptions | None:
        index = self._pattern_seg.index
        pattern = "325"
        if index < 4:
            pattern = solver.BUILTIN_PATTERNS[index]
        elif index == 4:
            custom = self._custom_pattern.text().strip()
            if not solver.normalize_pattern(custom):
                self._set_solve_flag(t("calc325.solve.custom_invalid"), warn=True)
                return None
            pattern = custom
        else:
            pattern = "325"  # solve_all 会依次尝试内置数字串
        budget = (2000, 5000, 8000)[self._budget_seg.index]
        options = solver.SolveOptions(
            pattern=pattern,
            max_blocks=self._blocks_seg.index + 1,
            order_mode="any" if self._order_seg.index == 1 else "fixed",
            allow_factorial=self._factorial_toggle.isChecked(),
            fact_max=(3, 5, 8, 12)[self._factmax_seg.index],
            allow_decimal=self._decimal_toggle.isChecked(),
            allow_power=self._power_toggle.isChecked(),
            allow_sqrt=self._sqrt_toggle.isChecked(),
            allow_comb=self._comb_toggle.isChecked(),
            allow_gcd_mod=self._gcd_toggle.isChecked(),
            time_budget_ms=budget,
            solution_limit=8,
        )
        return options.normalized()

    def _start_solve(self) -> None:
        if self._solve_busy:
            return
        target_text = self._target_input.text().strip()
        try:
            solver.parse_target(target_text)
        except solver.SolveError as exc:
            self._set_solve_flag(str(exc), warn=True)
            return
        options = self._collect_solve_options()
        if options is None:
            return
        self._solve_busy = True
        self._solve_generation += 1
        generation = self._solve_generation
        self._solve_btn.setEnabled(False)
        self._solve_busy_bar.start()
        self._set_solve_flag(
            t(
                "calc325.result.searching",
                pattern=options.pattern,
                blocks=options.max_blocks,
                seconds=options.time_budget_ms // 1000,
            ),
            warn=False,
        )
        self._save_solve_settings(options)
        all_patterns = self._pattern_seg.index == 5

        def done(results) -> None:
            self._finish_solve(generation, results)

        def failed(exc: Exception) -> None:
            if generation != self._solve_generation:
                return
            self._solve_busy = False
            self._solve_btn.setEnabled(True)
            self._solve_busy_bar.stop()
            self._set_solve_flag(t("calc325.result.failed", error=f"{type(exc).__name__}: {exc}"), warn=True)

        if all_patterns:
            run_async(solver.solve_all, target_text, options, on_done=done, on_error=failed)
        else:
            run_async(solver.solve, target_text, options, on_done=done, on_error=failed)

    def _finish_solve(self, generation: int, results) -> None:
        if generation != self._solve_generation:
            return
        self._solve_busy = False
        self._solve_btn.setEnabled(True)
        self._solve_busy_bar.stop()
        if isinstance(results, list):
            first_ok = next((item for item in results if item.ok), None)
            result = first_ok or (results[-1] if results else None)
        else:
            result = results
        if result is None:
            self._set_solve_flag(t("calc325.result.no_return"), warn=True)
            return
        self._solve_result = result
        if result.ok:
            self._solve_alternatives = list(result.alternatives)
            self._solve_choice = 0
            attempts = ""
            if isinstance(results, list) and len(results) > 1:
                attempts = t("calc325.result.attempts", count=len(results))
            self._solve_fields.set_fields([
                t("calc325.result.field.blocks", blocks=result.blocks),
                t("calc325.result.field.tokens", tokens=result.tokens),
                t("calc325.result.field.elapsed", ms=round(result.elapsed_ms)),
                t("calc325.result.field.verified"),
                t("calc325.result.field.pattern", pattern=result.pattern),
            ])
            self._render_solve_expression()
            self._render_alternatives()
            self._set_solve_flag(
                t(
                    "calc325.result.found",
                    count=len(self._solve_alternatives),
                    attempts=attempts,
                ),
                warn=False,
            )
        else:
            self._solve_alternatives = []
            self._solve_choice = 0
            self._solve_fields.set_fields([t("calc325.result.not_found"), t("calc325.result.field.elapsed", ms=round(result.elapsed_ms))])
            self._solve_latex_view.clear()
            self._solve_latex_view.hide()
            self._solve_expr.setText("—")
            self._solve_expr.show()
            _clear_layout(self._solve_alt_layout)
            self._solve_alt_label.hide()
            self._set_solve_flag(result.error or t("calc325.result.no_solution"), warn=True)
        self._sync_topbar()

    def _render_solve_expression(self) -> None:
        if self._solve_result is None or not self._solve_result.ok:
            return
        if not self._solve_alternatives:
            return
        index = max(0, min(self._solve_choice, len(self._solve_alternatives) - 1))
        item = self._solve_alternatives[index]
        target = self._solve_result.target_text
        if self._solve_view_mode == "latex":
            self._solve_latex_view.set_latex(
                item["latex"] + f" = {target}",
                text=item["pretty"] + f" = {target}",
            )
            self._solve_latex_view.show()
            self._solve_expr.hide()
        else:
            self._solve_expr.setText(item["pretty"] + f" = {target}")
            self._solve_expr.show()
            self._solve_latex_view.hide()
        self._copy_solve_btn.setText(t("calc325.result.copy_ascii"))
        self._copy_solve_latex_btn.setText(t("calc325.result.copy_latex"))

    def _render_alternatives(self) -> None:
        _clear_layout(self._solve_alt_layout)
        if len(self._solve_alternatives) <= 1:
            self._solve_alt_label.hide()
            return
        self._solve_alt_label.setText(t("calc325.result.alt_title", count=len(self._solve_alternatives)))
        self._solve_alt_label.show()
        for index, item in enumerate(self._solve_alternatives[:8]):
            button = CutButton(item["pretty"], "chip", compact=True)
            button.clicked.connect(lambda _checked=False, i=index: self._select_alternative(i))
            self._solve_alt_layout.addWidget(button)

    def _select_alternative(self, index: int) -> None:
        self._solve_choice = index
        self._render_solve_expression()

    def _set_solve_flag(self, text: str, *, warn: bool) -> None:
        self._solve_flag.set_text(text)
        # InfoFlag 的 kind 在构造时固定；用前面的黄色小旗/图标区分，文字本身给出提示。
        self._solve_flag.setToolTip("")

    def _save_solve_settings(self, options: solver.SolveOptions) -> None:
        self.ctx.settings.update(
            {
                "calc325.solve_target": self._target_input.text().strip(),
                "calc325.solve_pattern": self._current_pattern_setting(),
                "calc325.solve_max_blocks": options.max_blocks,
                "calc325.solve_order": options.order_mode,
                "calc325.solve_factorial": options.allow_factorial,
                "calc325.solve_fact_max": options.fact_max,
                "calc325.solve_decimal": options.allow_decimal,
                "calc325.solve_time_budget": options.time_budget_ms,
                "calc325.solve_advanced": {
                    "power": options.allow_power,
                    "sqrt": options.allow_sqrt,
                    "comb": options.allow_comb,
                    "gcd_mod": options.allow_gcd_mod,
                },
            },
            save=False,
        )

    # -- 求解复制 -----------------------------------------------------------
    def _copy_solve_expression(self) -> None:
        if self._solve_result is None or not self._solve_result.ok or not self._solve_alternatives:
            self.ctx.notify(t("calc325.result.copy_none_ascii"), "warn")
            return
        item = self._solve_alternatives[max(0, min(self._solve_choice, len(self._solve_alternatives) - 1))]
        QApplication.clipboard().setText(item["pretty"] + f" = {self._solve_result.target_text}")
        self.ctx.notify(t("calc325.result.copy_ascii_ok"), "ok")

    def _copy_solve_latex(self) -> None:
        if self._solve_result is None or not self._solve_result.ok or not self._solve_alternatives:
            self.ctx.notify(t("calc325.result.copy_none_latex"), "warn")
            return
        item = self._solve_alternatives[max(0, min(self._solve_choice, len(self._solve_alternatives) - 1))]
        latex = item["latex"] + f" = {self._solve_result.target_text}"
        QApplication.clipboard().setText(latex)
        self.ctx.notify(t("calc325.result.copy_latex_ok"), "ok")

    # ------------------------------------------------------------ 随机
    @property
    def _level(self) -> int:
        return self._level_seg.index + 1

    @property
    def _seed_type(self) -> str:
        return "zc" if self._type_seg.index == 1 else "number"

    def _on_level_changed(self, _index: int) -> None:
        if self._loading_state:
            return
        level = self._level
        current = self._canonical_seed or levels.default_seed(level)
        try:
            canonical = levels.parse_seed_input(current, "number", level)
        except ValueError:
            canonical = levels.default_seed(level)
        self._canonical_seed = canonical
        self._seed_input.setText(levels.seed_display(canonical, self._seed_type))
        self._update_seed_preview()
        self._generate_random()

    def _on_seed_type_changed(self, _index: int) -> None:
        if self._loading_state:
            return
        level = self._level
        try:
            canonical = levels.parse_seed_input(
                self._seed_input.text(), "zc" if self._type_seg.index == 0 else "number", level
            )
        except ValueError:
            canonical = self._canonical_seed or levels.default_seed(level)
        self._canonical_seed = canonical
        self._seed_input.setText(levels.seed_display(canonical, self._seed_type))
        self._update_seed_preview()
        self._generate_random()

    def _update_seed_preview(self) -> None:
        if not hasattr(self, "_seed_preview_label"):
            return
        level = self._level
        seed_type = self._seed_type
        text = self._seed_input.text()
        valid, error = levels.validate_seed_text(text, seed_type, level)
        if not valid:
            self._seed_preview_label.setText(t("calc325.seed.preview_error", error=error))
            self._seed_flag.set_text(t("calc325.seed.invalid", error=error))
            self._seed_meta.set_fields([levels.LEVEL_NAMES[level], t("calc325.seed.need_length", length=levels.seed_length(level))])
            return
        canonical = levels.parse_seed_input(text, seed_type, level)
        self._canonical_seed = canonical
        preview = levels.seed_preview(canonical, seed_type)
        self._seed_preview_label.setText(
            t("calc325.seed.preview_body", seed=preview)
            if len(preview) > 34
            else t("calc325.seed.preview_inline", seed=preview)
        )
        depth_low, depth_high = levels.depth_range(level)
        depth_text = f"{depth_low}" if depth_low == depth_high else f"{depth_low}–{depth_high}"
        self._seed_meta.set_fields([
            levels.LEVEL_NAMES[level],
            t("calc325.seed.field.length", length=levels.seed_length(level)),
            t("calc325.seed.field.depth_range", range=depth_text),
        ])
        self._seed_flag.set_text(levels.LEVEL_DESCRIPTIONS[level])
        if not self._loading_state:
            self._seed_timer.start()

    def _random_seed(self) -> None:
        level = self._level
        canonical = levels.make_seed(level)
        self._canonical_seed = canonical
        self._seed_input.setText(levels.seed_display(canonical, self._seed_type))
        self._update_seed_preview()
        self._generate_random()

    def _generate_random(self) -> None:
        level = self._level
        seed_type = self._seed_type
        text = self._seed_input.text()
        try:
            equation = random_expr.generate(text, level, seed_type)
        except ValueError as exc:
            self._seed_flag.set_text(t("calc325.seed.invalid", error=exc))
            self._seed_preview_label.setText(t("calc325.seed.preview_none"))
            return
        self._equation = equation
        self._canonical_seed = equation.seed
        self._seed_timer.stop()
        self.ctx.settings.update(
            {
                "calc325.level": level,
                "calc325.complexity": levels.complexity_for_level(level),
                "calc325.seed_type": seed_type,
                "calc325.last_seed": equation.seed,
            },
            save=False,
        )
        self._render_formula_views()
        self._exact_label.setText(t("calc325.formula.exact_prefix", chain=equation.exact_chain_text))
        self._formula_fields.set_fields([
            levels.LEVEL_NAMES[level],
            t("calc325.seed.field.depth", depth=equation.depth),
            t("calc325.formula.field.factors", count=len(equation.factors)),
            t("calc325.seed.field.length", length=len(equation.seed)),
            t("calc325.formula.field.verified", value=equation.verify_with_ast()),
        ])
        self._refresh_steps()
        self._refresh_favorite_button()
        self._seed_flag.set_text(
            t("calc325.seed.description_suffix", description=levels.LEVEL_DESCRIPTIONS[level])
        )
        self._seed_meta.set_fields([
            levels.LEVEL_NAMES[level],
            t("calc325.seed.field.length", length=levels.seed_length(level)),
            t("calc325.seed.field.depth", depth=equation.depth),
        ])
        self._add_history(equation)
        self._refresh_history()
        self._sync_topbar()

    def _render_formula_views(self) -> None:
        """把随机算式按因子逐行渲染成 LaTeX 图形（mathtext），失败自动退回文本。"""
        if not hasattr(self, "_formula_layout"):
            return
        _clear_layout(self._formula_layout)
        self._formula_views = []
        equation = self._equation
        if equation is None:
            return
        for index, factor in enumerate(equation.factors):
            latex = ("" if index == 0 else "\\times\\,") + f"\\left({factor.latex}\\right)"
            view = LaTeXView(fontsize=16)
            view.set_latex(latex, text=(factor.pretty if index == 0 else "× " + factor.pretty))
            self._formula_layout.addWidget(view)
            self._formula_views.append(view)
        if self._formula_views:
            last = self._formula_views[-1]
            last.set_latex(
                ("" if len(equation.factors) == 1 else "\\times\\,")
                + f"\\left({equation.factors[-1].latex}\\right) = 325",
                text=("" if len(equation.factors) == 1 else "× ")
                + equation.factors[-1].pretty
                + " = 325",
            )

    def _debounced_generate(self) -> None:
        if self._loading_state or self._mode != "random":
            return
        valid, _error = levels.validate_seed_text(
            self._seed_input.text(), self._seed_type, self._level
        )
        if valid:
            self._generate_random()

    def _refresh_steps(self) -> None:
        _clear_layout(self._steps_layout)
        equation = self._equation
        if equation is None:
            self._steps_layout.addWidget(Label("—", "muted"))
            return
        for index, factor in enumerate(equation.factors, start=1):
            self._steps_layout.addWidget(_FactorRow(index, factor, self._steps_host))
        product = Label(t("calc325.factors.product", chain=" × ".join(f.value_text for f in equation.factors)), "small", wrap=True)
        self._steps_layout.addWidget(product)

    # -- 随机复制 / 收藏 / PNG ---------------------------------------------
    def _copy_random_expression(self) -> None:
        if self._equation is None:
            self.ctx.notify(t("calc325.formula.no_equation"), "warn")
            return
        QApplication.clipboard().setText(self._equation.formula_text + " = 325")
        self.ctx.notify(t("calc325.formula.copy_ok"), "ok")

    def _copy_random_latex(self) -> None:
        if self._equation is None:
            self.ctx.notify(t("calc325.formula.no_equation"), "warn")
            return
        QApplication.clipboard().setText(self._equation.latex)
        self.ctx.notify(t("calc325.result.copy_latex_ok"), "ok")

    def _copy_random_seed(self) -> None:
        if self._equation is not None:
            text = self._equation.seed_preview
        else:
            try:
                canonical = levels.parse_seed_input(self._seed_input.text(), self._seed_type, self._level)
            except ValueError as exc:
                self.ctx.notify(t("calc325.seed.invalid", error=exc), "warn")
                return
            text = levels.seed_preview(canonical, self._seed_type)
        QApplication.clipboard().setText(text)
        self.ctx.notify(t("calc325.seed.copy_ok"), "ok")

    def _toggle_favorite(self) -> None:
        if self._equation is None:
            return
        key = (self._equation.seed, self._equation.level, self._equation.seed_type)
        if self._favorites.has(key):
            self._favorites.remove(key)
            self.ctx.notify(t("calc325.formula.favorite_remove"), "info")
        else:
            self._favorites.add(
                CalcEntry(
                    seed=self._equation.seed,
                    level=self._equation.level,
                    seed_type=self._equation.seed_type,
                    formula=self._equation.formula_text,
                    latex=self._equation.latex,
                )
            )
            self.ctx.notify(t("calc325.formula.favorite_add"), "ok")
        self._refresh_favorite_button()
        self._refresh_favorites()

    def _refresh_favorite_button(self) -> None:
        if self._equation is None:
            self._favorite_btn.setText(t("calc325.formula.favorite"))
            return
        key = (self._equation.seed, self._equation.level, self._equation.seed_type)
        self._favorite_btn.setText(
            t("calc325.formula.favorited") if self._favorites.has(key) else t("calc325.formula.favorite")
        )

    def _save_png(self) -> None:
        if self._equation is None:
            self.ctx.notify(t("calc325.formula.no_equation"), "warn")
            return
        default = str(Path.home() / f"EndBox_325_L{self._equation.level}_{self._equation.seed[:8]}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, t("calc325.formula.png_dialog"), default, t("calc325.formula.png_filter")
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        image = self._render_equation_image(self._equation)
        if image.save(path):
            self.ctx.notify(t("calc325.formula.png_saved"), "ok")
        else:
            self.ctx.notify(t("calc325.formula.png_failed"), "warn")

    def _render_equation_image(self, equation: random_expr.RandomEquation) -> QImage:
        """把当前算式的 LaTeXView 渲染结果 + 标题合成 PNG。"""
        from endbox.ui.kit.mathview import render_latex

        colors = pal()
        margin = 40
        max_width = 1200
        title_font = F.font(26, weight=QFont.Weight.Bold, wide=True, cjk_first=True)
        small_font = F.font(13, cjk_first=True)
        mono_font = F.mono_font(12)

        def wrap(text: str, font, width: float) -> list[str]:
            rows: list[str] = []
            current = ""
            for char in text:
                if current and paint.text_width(font, current + char) > width:
                    rows.append(current)
                    current = char
                else:
                    current += char
            if current:
                rows.append(current)
            return rows or [""]

        rendered: list[tuple[object, str]] = []
        for index, factor in enumerate(equation.factors):
            latex = ("" if index == 0 else "\\times\\,") + f"\\left({factor.latex}\\right)"
            pix = render_latex(latex, dpi=170, ink=colors.ink.name())
            fallback = ("" if index == 0 else "× ") + factor.pretty
            rendered.append((pix, fallback))

        meta_text = (
            t("calc325.formula.png_meta", level=levels.LEVEL_NAMES[equation.level], depth=equation.depth, length=len(equation.seed))
        )
        meta_lines = wrap(meta_text, small_font, max_width)
        seed_lines = wrap(t("calc325.formula.png_seed", seed=equation.seed_display), mono_font, max_width)
        chain_lines = wrap(t("calc325.formula.exact_prefix", chain=equation.exact_chain_text), small_font, max_width)

        heights = 56 + len(meta_lines) * 26 + len(seed_lines) * 22
        for pix, _fallback in rendered:
            heights += (pix.height() + 16) if pix is not None and not pix.isNull() else 34
        heights += len(chain_lines) * 24 + 72 + margin * 2
        image = QImage(_IMAGE_W, max(360, heights), QImage.Format.Format_ARGB32)
        image.fill(colors.paper)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        paint.draw_text(p, QRectF(margin, margin - 8, max_width, 40), t("calc325.formula.png_title"), title_font, colors.ink)
        paint.hairline(p, margin, margin + 40, _IMAGE_W - margin, margin + 40, colors.rule)
        y = margin + 56
        for line in meta_lines:
            paint.draw_text(p, QRectF(margin, y, max_width, 26), line, small_font, colors.ink2)
            y += 26
        for line in seed_lines:
            paint.draw_text(p, QRectF(margin, y, max_width, 22), line, mono_font, colors.ink2)
            y += 22
        for pix, fallback in rendered:
            if pix is not None and not pix.isNull():
                scale = min(1.0, max_width / max(1, pix.width()))
                width = pix.width() * scale
                height = pix.height() * scale
                p.drawPixmap(QRectF(margin, y, width, height), pix, QRectF(pix.rect()))
                y += height + 16
            else:
                paint.draw_text(p, QRectF(margin, y, max_width, 34), fallback, F.font(17, cjk_first=True), colors.ink)
                y += 34
        for line in chain_lines:
            paint.draw_text(p, QRectF(margin, y, max_width, 24), line, small_font, colors.ink2)
            y += 24
        paint.draw_text(p, QRectF(margin, y, max_width, 72), "= 325", F.number_font(46), colors.ink)
        p.end()
        return image

    # ------------------------------------------------------------ 历史 / 收藏
    def _add_history(self, equation: random_expr.RandomEquation) -> None:
        self._history.add(
            CalcEntry(
                seed=equation.seed,
                level=equation.level,
                seed_type=equation.seed_type,
                formula=equation.formula_text,
                latex=equation.latex,
            )
        )

    def _refresh_history(self) -> None:
        if not hasattr(self, "_history_layout"):
            return
        _clear_layout(self._history_layout)
        entries = self._history.entries()
        if self._history_filter_level:
            entries = [entry for entry in entries if entry.level == self._history_filter_level]
        self._history_field.set_fields([
            t("calc325.history.field_total", count=len(entries)),
            t("calc325.history.field_limit", limit=self._history.limit),
        ])
        if not entries:
            self._history_layout.addWidget(Label(t("calc325.history.empty"), "muted", wrap=True))
            return
        for entry in entries[:12]:
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(6)
            title = t(
                "calc325.history.row_title",
                level=entry.level,
                seed=levels.seed_preview(entry.seed, entry.seed_type).splitlines()[0][:28],
            )
            row = _CalcRecordRow(title, (entry.formula or "")[:60], entry.time_text, holder)
            row.clicked.connect(lambda _checked=False, item=entry: self._restore_entry(item))
            holder_layout.addWidget(row, 1)
            remove = IconButton("trash", t("calc325.history.remove_tooltip"), size=30, icon_size=14)
            remove.clicked.connect(lambda _checked=False, item=entry: self._remove_history(item))
            holder_layout.addWidget(remove)
            self._history_layout.addWidget(holder)

    def _refresh_favorites(self) -> None:
        if not hasattr(self, "_favorites_layout"):
            return
        _clear_layout(self._favorites_layout)
        entries = self._favorites.entries()
        self._favorites_field.set_fields([
            t("calc325.history.field_total", count=len(entries)),
            t("calc325.history.field_limit", limit=self._favorites.limit),
        ])
        if not entries:
            self._favorites_layout.addWidget(Label(t("calc325.favorites.empty"), "muted", wrap=True))
            return
        for entry in entries[:12]:
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(6)
            title = t(
                "calc325.history.row_title",
                level=entry.level,
                seed=levels.seed_preview(entry.seed, entry.seed_type).splitlines()[0][:28],
            )
            row = _CalcRecordRow(title, (entry.formula or "")[:60], entry.time_text, holder)
            row.clicked.connect(lambda _checked=False, item=entry: self._restore_entry(item))
            holder_layout.addWidget(row, 1)
            remove = IconButton("trash", t("calc325.favorites.remove"), size=30, icon_size=14)
            remove.clicked.connect(lambda _checked=False, item=entry: self._remove_favorite(item))
            holder_layout.addWidget(remove)
            self._favorites_layout.addWidget(holder)

    def _restore_entry(self, entry: CalcEntry) -> None:
        self._mode = "random"
        self._sync_stack()
        self._level_seg.set_index(entry.level - 1)
        self._type_seg.set_index(0 if entry.seed_type != "zc" else 1)
        self._canonical_seed = entry.seed
        self._seed_input.setText(levels.seed_display(entry.seed, entry.seed_type))
        self._update_seed_preview()
        self._generate_random()

    def _remove_history(self, entry: CalcEntry) -> None:
        if not confirm(
            self,
            title=t("calc325.history.confirm.remove_title"),
            message=t("calc325.history.confirm.remove_message"),
            action_text=t("calc325.history.confirm.remove_action"),
        ):
            return
        self._history.remove(entry.key)
        self._refresh_history()
        self.ctx.notify(t("calc325.history.cleared"), "info")

    def _remove_favorite(self, entry: CalcEntry) -> None:
        if not confirm(
            self,
            title=t("calc325.favorites.confirm.remove_title"),
            message=t("calc325.favorites.confirm.remove_message"),
            action_text=t("calc325.favorites.confirm.remove_action"),
        ):
            return
        self._favorites.remove(entry.key)
        self._refresh_favorites()
        self._refresh_favorite_button()
        self.ctx.notify(t("calc325.formula.favorite_remove"), "info")

    def _clear_history(self) -> None:
        if not confirm(
            self,
            title=t("calc325.history.confirm.clear_title"),
            message=t("calc325.history.confirm.clear_message"),
            action_text=t("calc325.history.confirm.clear_action"),
        ):
            return
        self._history.clear()
        self._refresh_history()
        self.ctx.notify(t("calc325.history.cleared"), "info")

    def _clear_favorites(self) -> None:
        if not confirm(
            self,
            title=t("calc325.favorites.confirm.clear_title"),
            message=t("calc325.favorites.confirm.clear_message"),
            action_text=t("calc325.favorites.confirm.clear_action"),
        ):
            return
        self._favorites.clear()
        self._refresh_favorites()
        self._refresh_favorite_button()
        self.ctx.notify(t("calc325.favorites.cleared"), "info")

    def _open_data_dir(self) -> None:
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.DATA_DIR)))
        except Exception:  # noqa: BLE001
            self.ctx.notify(t("calc325.data_dir_failed", path=config.DATA_DIR), "warn")

    # ------------------------------------------------------------ 快捷键 / 顶栏
    def _on_primary_shortcut(self) -> None:
        if self._mode == "solve":
            self._start_solve()
        else:
            self._generate_random()

    def _on_random_shortcut(self) -> None:
        if self._mode == "random":
            self._random_seed()

    def _sync_topbar(self) -> None:
        """异步求解 / 生成后立即刷新顶栏字段（主窗口切换视图时才自动刷新）。"""
        window = getattr(self.ctx, "main_window", None)
        topbar = getattr(window, "topbar", None)
        if topbar is None:
            return
        try:
            topbar.set_fields(self.topbar_fields())
        except Exception:  # noqa: BLE001 - 顶栏刷新失败不影响计算
            pass

    def topbar_fields(self) -> list[str]:
        if self._mode == "solve":
            if self._solve_result is None:
                return [t("calc325.result.status.target", target=self._target_input.text().strip() or "—"), t("calc325.result.status.mode_solve")]
            if self._solve_result.ok:
                return [
                    t("calc325.result.status.target", target=self._solve_result.target_text),
                    t("calc325.result.status.blocks", blocks=self._solve_result.blocks),
                    f"{self._solve_result.elapsed_ms:.0f} ms",
                ]
            return [t("calc325.result.status.target", target=self._target_input.text().strip() or "—"), t("calc325.result.not_found")]
        if self._equation is None:
            return [t("calc325.result.status.mode_random")]
        return [
            f"L{self._equation.level}",
            t("calc325.seed.field.depth", depth=self._equation.depth),
            t("calc325.formula.field.factors", count=len(self._equation.factors)),
        ]
