# -*- coding: utf-8 -*-
"""后台任务：QRunnable + Qt 信号，回到 UI 线程回调。"""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal

from endbox.core.log import get_logger

log = get_logger("core.workers")


class _Signals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(object)
    progress = pyqtSignal(object)


class Worker(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _Signals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401 - Qt 接口
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            log.error("background task failed: %s\n%s", exc, traceback.format_exc())
            self.signals.failed.emit(exc)
        else:
            self.signals.done.emit(result)


def run_async(
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    **kwargs: Any,
) -> Worker:
    """在全局线程池里跑一个函数，回调在 UI 线程执行。"""
    worker = Worker(fn, *args, **kwargs)
    if on_done is not None:
        worker.signals.done.connect(on_done)
    if on_error is not None:
        worker.signals.failed.connect(on_error)
    QThreadPool.globalInstance().start(worker)
    return worker


class Poller(QObject):
    """周期性轮询（UI 线程定时器），用于扫码登录状态检查等。"""

    def __init__(self, interval_ms: int, callback: Callable[[], Any], parent: QObject | None = None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(max(200, interval_ms))
        self._timer.timeout.connect(callback)
        self._callback = callback
        self._ticks = 0

    def start(self) -> None:
        self._ticks = 0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def running(self) -> bool:
        return self._timer.isActive()


def debounce(widget: QObject, delay_ms: int, fn: Callable[[], None]) -> Callable[[], None]:
    """返回一个防抖包装：停止触发 delay_ms 后执行 fn。"""
    timer = QTimer(widget)
    timer.setSingleShot(True)
    timer.setInterval(delay_ms)
    timer.timeout.connect(fn)

    def trigger() -> None:
        timer.start(delay_ms)

    return trigger
