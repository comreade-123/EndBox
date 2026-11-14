# -*- coding: utf-8 -*-
"""用 QSS 给原生输入控件上色（文本框、下拉、滚动条、提示条、菜单）。

自绘部件（按钮、面板等）不依赖 QSS；只有系统控件需要它。
"""
from __future__ import annotations

from endbox.theme.fonts import FONT_CJK, FONT_UI
from endbox.theme.tokens import Palette


def app_qss(pal: Palette, scale: float = 1.0) -> str:
    ink = pal.ink.name()
    ink2 = pal.ink2.name()
    rule = pal.rule.name()
    panel = pal.panel.name()
    input_bg = pal.input_bg.name()
    chip = pal.chip.name()
    signal = pal.signal.name()
    on_signal = pal.on_signal.name()
    paper = pal.paper.name()
    hover = pal.hover.name()
    is_dark = pal.is_dark
    sel_bg = pal.signal.name() if not is_dark else pal.signal.name()
    sel_fg = on_signal
    scroll_track = "transparent"
    scroll_handle = pal.ink2.name() if is_dark else "#BDBDB8"
    slider_groove = chip
    family = f"{FONT_UI}, {FONT_CJK}, 'Segoe UI'"
    fs = lambda n: int(round(n * scale))  # noqa: E731

    return f"""
    * {{ outline: none; }}
    QWidget {{
        color: {ink};
        font-family: {family};
        font-size: {fs(14)}px;
        selection-background-color: {sel_bg};
        selection-color: {sel_fg};
    }}
    QToolTip {{
        background: {panel};
        color: {ink};
        border: 1px solid {rule};
        padding: 4px 8px;
        font-family: {family};
        font-size: {fs(12)}px;
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
        background: {input_bg};
        color: {ink};
        border: 1px solid {rule};
        padding: 6px 8px;
        font-family: {family};
        font-size: {fs(14)}px;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {ink};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
        color: {ink2};
    }}
    QLineEdit[readOnly="true"] {{ color: {ink2}; }}
    QComboBox {{
        background: {input_bg};
        color: {ink};
        border: 1px solid {rule};
        padding: 5px 8px;
        font-family: {family};
        font-size: {fs(14)}px;
    }}
    QComboBox:hover {{ border: 1px solid {ink2}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {panel};
        color: {ink};
        border: 1px solid {rule};
        selection-background-color: {sel_bg};
        selection-color: {sel_fg};
        outline: none;
    }}
    QMenu {{
        background: {panel};
        color: {ink};
        border: 1px solid {rule};
        padding: 4px;
    }}
    QMenu::item {{ padding: 6px 24px 6px 14px; }}
    QMenu::item:selected {{ background: {sel_bg}; color: {sel_fg}; }}
    QMenu::separator {{ height: 1px; background: {rule}; margin: 4px 8px; }}
    QScrollBar:vertical {{
        background: {scroll_track}; width: 8px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {scroll_handle}; min-height: 28px; border: none;
    }}
    QScrollBar::handle:vertical:hover {{ background: {ink2}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{
        background: transparent; height: 8px;
    }}
    QScrollBar::handle:horizontal {{ background: {scroll_handle}; min-width: 28px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    QCheckBox, QRadioButton {{
        color: {ink};
        spacing: 8px;
        font-family: {family};
        font-size: {fs(14)}px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {ink2};
        background: {input_bg};
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {signal};
        border: 1px solid {signal};
    }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QSplitter::handle {{ background: {rule}; }}
    QProgressBar {{
        background: {chip}; border: none; height: 6px; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background: {signal}; }}
    QSlider::groove:horizontal {{ background: {slider_groove}; height: 2px; }}
    QSlider::handle:horizontal {{
        background: {ink}; width: 10px; margin: -5px 0; border: none;
    }}
    QSlider::sub-page:horizontal {{ background: {ink}; }}
    QDialog {{ background: {paper}; }}
    QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {rule}; }}
    """
