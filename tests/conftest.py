# -*- coding: utf-8 -*-
"""测试公共设施：离屏 Qt、临时数据目录。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ENDBOX_NO_GLASS", "1")
os.environ.setdefault("ENDBOX_DATA_DIR", str(ROOT / "_devdata"))

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    from endbox.theme import fonts

    fonts.load_fonts()
    yield app


@pytest.fixture()
def tmp_settings(tmp_path):
    from endbox.config import Settings

    return Settings(tmp_path / "settings.json")
