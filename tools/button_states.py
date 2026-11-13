# -*- coding: utf-8 -*-
"""验证「悬停黄色擦入 / 离开后退回」：输出三态对比图。"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENDBOX_NO_GLASS", "1")
os.environ.setdefault("ENDBOX_DATA_DIR", str(ROOT / "_devdata"))
from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QEnterEvent, QFont, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from endbox.theme import fonts
from endbox.theme import fonts as F
from endbox.theme.tokens import theme
from endbox.ui.kit import CutButton, IconButton

app = QApplication([])
fonts.load_fonts()

W, H = 760, 210
canvas = QPixmap(W, H)
canvas.fill(QColor(theme.p.paper))
p = QPainter(canvas)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
p.setFont(F.font(12, cjk_first=True))

buttons = [
    ("主要按钮 · 悬停前", CutButton("生成算式", "primary", icon="spark")),
    ("主要按钮 · 悬停中", CutButton("生成算式", "primary", icon="spark")),
    ("主要按钮 · 移开后", CutButton("生成算式", "primary", icon="spark")),
]
# 先画标签，再画按钮，最后统一画在画布上
for i, (label, btn) in enumerate(buttons):
    btn.resize(btn.sizeHint())
    btn.ensurePolished()
    btn.repaint()
    if i == 1:
        app.sendEvent(btn, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        QTest.qWait(360)
    if i == 2:
        app.sendEvent(btn, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        QTest.qWait(360)
        app.sendEvent(btn, QEvent(QEvent.Type.Leave))
        QTest.qWait(360)
    p.setPen(theme.p.ink2)
    p.drawText(QRectF(24, 26 + i * 62, 200, 20), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), label)
    p.drawPixmap(230, 18 + i * 62, btn.grab())

p.end()
canvas.save(str(ROOT / "shots" / "button_states.png"))
print("saved shots/button_states.png")
