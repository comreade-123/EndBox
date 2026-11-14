# -*- coding: utf-8 -*-
"""运动曲线与动画辅助。

endfield-ui 的三条曲线：
  snap   cubic-bezier(0.16, 1, 0.3, 1)   出现、落位
  mech   cubic-bezier(0.7, 0, 0.2, 1)   快门、擦入、灌满、以及所有退出
  ease   cubic-bezier(0.2, 0.7, 0.1, 1) 颜色、普通过渡
Qt 的 QEasingCurve 没有对应类型，所以在这里自己求值。
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPointF, QVariantAnimation, Qt

CURVES = {
    "snap": (0.16, 1.0, 0.3, 1.0),
    "mech": (0.7, 0.0, 0.2, 1.0),
    "ease": (0.2, 0.7, 0.1, 1.0),
    "linear": (0.0, 0.0, 1.0, 1.0),
}


def _bezier(t: float, p1: float, p2: float) -> float:
    return 3 * (1 - t) * (1 - t) * t * p1 + 3 * (1 - t) * t * t * p2 + t * t * t


def _bezier_d(t: float, p1: float, p2: float) -> float:
    return 3 * (1 - t) * (1 - t) * p1 + 6 * (1 - t) * t * (p2 - p1) + 3 * t * t * (1 - p2)


def curve_at(kind: str, x: float) -> float:
    """给定线性进度 x，返回缓动后的 y。"""
    x = max(0.0, min(1.0, x))
    _, y1, _, y2 = CURVES.get(kind, CURVES["mech"])
    c1x, _, c2x, _ = CURVES.get(kind, CURVES["mech"])
    # 牛顿迭代求 t 使得 bezier_x(t) = x
    t = x
    for _ in range(6):
        fx = _bezier(t, c1x, c2x) - x
        dfx = _bezier_d(t, c1x, c2x)
        if abs(dfx) < 1e-6:
            break
        t -= fx / dfx
        t = max(0.0, min(1.0, t))
    return _bezier(t, y1, y2)


class Curve(QVariantAnimation):
    """0→1 的动画，valueChanged 给的是缓动后的值。"""

    def __init__(self, parent=None, *, duration: int = 280, kind: str = "mech"):
        super().__init__(parent)
        self.setStartValue(0.0)
        self.setEndValue(1.0)
        self.setDuration(duration)
        self._kind = kind

    def kind(self, kind: str) -> "Curve":
        self._kind = kind
        return self

    def start(self, *, reverse: bool = False) -> None:
        self.stop()
        if reverse:
            self.setStartValue(1.0)
            self.setEndValue(0.0)
        else:
            self.setStartValue(0.0)
            self.setEndValue(1.0)
        super().start()


class CurveValue(QVariantAnimation):
    """在两个数值之间动画。"""

    def __init__(self, start: float, end: float, parent=None, *, duration: int = 280, kind: str = "mech"):
        super().__init__(parent)
        self._start = start
        self._end = end
        self.setStartValue(0.0)
        self.setEndValue(1.0)
        self.setDuration(duration)
        self._kind = kind
        self.valueChanged.connect(self._emit_value)

    def _emit_value(self, t: float) -> None:
        v = self._start + (self._end - self._start) * curve_at(self._kind, float(t))
        self._value = v


def ensure_windows_curve():
    """给需要 QEasingCurve 的地方一个最接近的替代（保留接口）。"""
    return QEasingCurve(QEasingCurve.Type.OutCubic)
