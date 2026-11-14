# -*- coding: utf-8 -*-
"""325 计算器：求解模式 + 随机模式（L1–L5 / Zc 种子）+ 历史收藏。

模块划分：

  · ``ast_eval``    独立表达式解析 / Fraction 求值 / LaTeX 输出（复算层）
  · ``solver``      区间 DP 求解（325 / 799 / 325799 / 114514 / 自定义数字串）
  · ``levels``      L1–L5、固定长度种子、Zc 编码
  · ``random_expr`` 随机模式的因子链与经典数学模块
  · ``history``     生成历史 / 收藏的 JSON 存储
  · ``generator``   旧版兼容接口（tests/test_foundation.py 仍在使用）
"""
from __future__ import annotations

from endbox.modules.calc325.history import (
    MAX_ENTRIES,
    CalcEntry,
    EntryStore,
    FavoriteStore,
    HistoryStore,
)
from endbox.modules.calc325.levels import (
    DEFAULT_LEVEL,
    LEVEL_DESCRIPTIONS,
    LEVEL_NAMES,
    LEVEL_SEED_LENGTHS,
    SEED_TYPE_LABELS,
    normalize_level,
    resolve_level,
    seed_length,
)
from endbox.modules.calc325.random_expr import Factor, RandomEquation, generate
from endbox.modules.calc325.solver import (
    BUILTIN_PATTERNS,
    SolveOptions,
    SolveResult,
    expression_latex,
    parse_target,
    solve,
    solve_all,
    verify_expression,
)

__all__ = [
    "BUILTIN_PATTERNS",
    "CalcEntry",
    "DEFAULT_LEVEL",
    "EntryStore",
    "Factor",
    "FavoriteStore",
    "HistoryStore",
    "LEVEL_DESCRIPTIONS",
    "LEVEL_NAMES",
    "LEVEL_SEED_LENGTHS",
    "MAX_ENTRIES",
    "RandomEquation",
    "SEED_TYPE_LABELS",
    "SolveOptions",
    "SolveResult",
    "expression_latex",
    "generate",
    "normalize_level",
    "parse_target",
    "resolve_level",
    "seed_length",
    "solve",
    "solve_all",
    "verify_expression",
]
