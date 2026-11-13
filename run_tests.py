# -*- coding: utf-8 -*-
"""测试入口：把 vendor/ 里的 pytest 带进 sys.path。

    python run_tests.py -q
    python run_tests.py tests/test_foundation.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

if __name__ == "__main__":
    try:
        import pytest
    except ModuleNotFoundError:
        print("需要 pytest：pip install pytest（或保留仓库里的 vendor/ 目录）")
        raise SystemExit(2)

    raise SystemExit(pytest.main(sys.argv[1:]))
