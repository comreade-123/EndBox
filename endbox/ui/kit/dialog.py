# -*- coding: utf-8 -*-
"""确认对话框：所有删除 / 重置操作必须先经过它。

设计沿用 EndBox 的壳层语言：圆角面板（壳层）、切角按钮、1px 细线；
红色只用于"删除"这个动作，取消是墨色幽灵按钮。
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from endbox.core.text import t
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.ui.kit.base import pal
from endbox.ui.kit.buttons import CutButton

DIALOG_RADIUS = 12
DIALOG_CUT = 18


class _Panel(QWidget):
    """对话框底板：板色 + 细线 + 右下切角。"""

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        paint.fill_round(p, QRectF(self.rect()), colors.panel, DIALOG_RADIUS)
        paint.stroke_round(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), colors.rule, DIALOG_RADIUS, 1.0)
        # 右下角的切角（可点击的暗示：这里是动作区）
        p.fillRect(QRectF(self.width() - 2, self.height() - 2, 2, 2), colors.rule)
        p.end()


class ConfirmDialog(QDialog):
    """确认对话框。``danger=True`` 时确认按钮是红色（只给删除用）。"""

    def __init__(
        self,
        parent=None,
        *,
        title: str = "",
        message: str = "",
        action_text: str | None = None,
        danger: bool = True,
    ):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(420)
        self._result_ok = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = _Panel(self)
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        eyebrow = QLabel(f"// {'DELETE' if danger else 'CONFIRM'}")
        eyebrow.setFont(F.eyebrow_font(10))
        eyebrow.setStyleSheet(f"color: {pal().ink2.name()}; background: transparent;")
        layout.addWidget(eyebrow)

        title_label = QLabel(title or t("common.confirm_title", "确认操作"))
        title_label.setFont(F.font(19, weight=F.QFont.Weight.Bold, wide=True))
        title_label.setStyleSheet(f"color: {pal().ink.name()}; background: transparent;")
        layout.addWidget(title_label)

        body = QLabel(message or t("common.confirm_message", "确定要继续吗？"))
        body.setWordWrap(True)
        body.setFont(F.font(13, cjk_first=True))
        body.setStyleSheet(f"color: {pal().ink2.name()}; background: transparent;")
        layout.addWidget(body)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 6, 0, 0)
        buttons.setSpacing(10)
        buttons.addStretch(1)
        self._cancel = CutButton(t("common.cancel", "取消"), "ghost", height=32)
        self._cancel.clicked.connect(self.reject)
        buttons.addWidget(self._cancel)
        self._ok = CutButton(
            action_text or (t("common.delete_confirm", "删除") if danger else t("common.confirm", "确认")),
            "danger" if danger else "primary",
            height=32,
            icon="trash" if danger else "check",
        )
        self._ok.clicked.connect(self.accept)
        buttons.addWidget(self._ok)
        layout.addLayout(buttons)

        # 默认焦点放在"取消"上，避免误按回车删掉东西
        self._cancel.setFocus()

    # 方便测试与调用方
    def exec_confirm(self) -> bool:
        return self.exec() == QDialog.DialogCode.Accepted

    @property
    def cancel_button(self) -> CutButton:
        return self._cancel

    @property
    def ok_button(self) -> CutButton:
        return self._ok

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


def confirm(
    parent,
    title: str,
    message: str,
    *,
    action_text: str | None = None,
    danger: bool = True,
) -> bool:
    """弹确认框；用户点确认返回 True。所有删除/清空/重置都必须用它。"""
    dialog = ConfirmDialog(
        parent, title=title, message=message, action_text=action_text, danger=danger
    )
    if parent is not None:
        try:
            dialog.adjustSize()
            center = parent.window().frameGeometry().center()
            dialog.move(center.x() - dialog.width() // 2, center.y() - dialog.height() // 2)
        except Exception:  # noqa: BLE001 - 定位失败不影响功能
            pass
    return dialog.exec() == QDialog.DialogCode.Accepted
