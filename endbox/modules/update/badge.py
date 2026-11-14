# -*- coding: utf-8 -*-
"""顶栏低干扰更新提示（主壳可选接线）。

用法（由 Lead 接入 ``ui/topbar.py``，两行）::

    from endbox.modules.update.badge import UpdateHint
    self.update_hint = UpdateHint(self.ctx, self)
    layout.addWidget(self.update_hint, 0, Qt.AlignmentFlag.AlignVCenter)

行为：
  · 启动后延迟几秒在后台静默检查一次（失败什么都不显示）；
  · 只有「有更新且未被忽略」时才显示一个小黄条「有新版本 vX」；
  · 点击打开设置页（更新区）；不抢焦点、不弹窗、不阻塞界面。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from endbox.core.text import t
from endbox.core.workers import run_async
from endbox.modules.update.service import UpdateService, update_check_disabled
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.ui.kit.base import EWidget, pal
from endbox.version import version_label


class UpdateHint(EWidget):
    """小号更新提示条：有更新才出现，点击进设置页。」

    主壳把 ``ctx`` 传进来即可。即使主壳不接，设置页的更新区也能独立工作。
    """

    activated = pyqtSignal(object)

    def __init__(
        self,
        ctx: Any,
        parent: QWidget | None = None,
        *,
        service: UpdateService | None = None,
        delay_ms: int = 4000,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self._service = service or getattr(getattr(ctx, "services", None), "update", None)
        if self._service is None:
            self._service = UpdateService(settings=getattr(ctx, "settings", None))
        self._version = ""
        self._busy = False
        self._result: Any = None
        self.setFixedHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(t("update.badge.tooltip"))
        self.hide()
        if delay_ms >= 0:
            QTimer.singleShot(delay_ms, self.refresh_quietly)

    # -- 检查 ---------------------------------------------------------------
    def refresh_quietly(self) -> None:
        """后台静默检查；没有更新 / 失败都保持隐藏。"""
        if self._busy or update_check_disabled():
            return
        self._busy = True

        def _failed(_exc: Exception) -> None:
            self._busy = False

        try:
            run_async(
                self._service.check,
                on_done=self._on_result,
                on_error=_failed,
                force=False,
            )
        except Exception:  # noqa: BLE001 - 主壳装不上时不影响界面
            self._busy = False

    def _on_result(self, result: Any) -> None:
        self._busy = False
        self._result = result
        try:
            if result is not None and result.has_update and result.info is not None:
                self._version = result.info.version
                self.setToolTip(t("update.badge.available", version=self._version))
                self.show()
                self.update()
            else:
                self.hide()
        except RuntimeError:  # 窗口已销毁
            pass

    # -- 交互 ---------------------------------------------------------------
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_settings()
        super().mouseReleaseEvent(event)

    def _open_settings(self) -> None:
        try:
            self.activated.emit(self._result)
        except Exception:  # noqa: BLE001
            pass
        open_view = getattr(self.ctx, "open_view", None)
        if callable(open_view):
            try:
                open_view("settings")
                return
            except Exception:  # noqa: BLE001
                pass
        window = getattr(self.ctx, "main_window", None)
        show_view = getattr(window, "show_view", None)
        if callable(show_view):
            try:
                show_view("settings")
            except Exception:  # noqa: BLE001
                pass

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        rect = QRectF(0, 0, self.width(), self.height())
        p.fillPath(paint.cut_rect(rect, 6, "br"), colors.signal)
        text = t("update.badge.available", version=self._version or version_label())
        paint.draw_text(
            p,
            rect.adjusted(10, 0, -10, 0),
            text,
            F.font(12, weight=QFont.Weight.Bold, cjk_first=True),
            colors.on_signal,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
        )
        p.end()

    def sizeHint(self):  # noqa: N802
        from PyQt6.QtCore import QSize

        width = 10 + 10
        try:
            from endbox.theme import fonts as F2

            metrics = self.fontMetrics()
            text = t("update.badge.available", version=self._version or "v0.0.0")
            width += metrics.horizontalAdvance(text) + 8
        except Exception:  # noqa: BLE001
            width += 96
        return QSize(max(72, width), 24)


__all__ = ["UpdateHint"]
