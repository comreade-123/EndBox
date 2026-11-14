# -*- coding: utf-8 -*-
"""统一日志：控制台 + 文件（按大小轮转）。"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from endbox import config

_configured = False


def setup(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    config.ensure_dirs()
    root = logging.getLogger("endbox")
    root.setLevel(level)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-28s %(message)s", datefmt="%H:%M:%S"
    )

    try:
        fh = logging.handlers.RotatingFileHandler(
            config.LOG_DIR / "endbox.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass

    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        root.addHandler(sh)


def get_logger(name: str) -> logging.Logger:
    setup()
    if name.startswith("endbox."):
        return logging.getLogger(name)
    return logging.getLogger("endbox." + name)
