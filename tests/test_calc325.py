# -*- coding: utf-8 -*-
"""325 计算器测试：求解 / 随机 L1–L5 / Zc / 独立复算 / 历史收藏 / 旧接口兼容。"""
from __future__ import annotations

import json
import math
from fractions import Fraction

import pytest

from endbox.modules.calc325 import ast_eval, levels
from endbox.modules.calc325 import random_expr as R
from endbox.modules.calc325 import solver
from endbox.modules.calc325.history import CalcEntry, FavoriteStore, HistoryStore


# ---------------------------------------------------------------- 独立复算子
def _independent_det(matrix: list[list[int]]) -> Fraction:
    """测试侧独立实现：Laplace 展开（与生成器的高斯消元无关）。"""
    size = len(matrix)
    if size == 1:
        return Fraction(matrix[0][0])
    if size == 2:
        return Fraction(matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0])
    total = Fraction(0)
    for col in range(size):
        minor = [row[:col] + row[col + 1:] for row in matrix[1:]]
        total += Fraction((-1) ** col) * matrix[0][col] * _independent_det(minor)
    return total


def _independent_phi(value: int) -> int:
    count = 0
    for n in range(1, value + 1):
        if math.gcd(n, value) == 1:
            count += 1
    return count


def _independent_divisor_count(value: int) -> int:
    return sum(1 for d in range(1, value + 1) if value % d == 0)


def verify_factor_independently(factor: R.Factor) -> Fraction:
    """按模块元数据用测试侧自己的实现复算因子值。"""
    meta = factor.meta
    module = meta.get("module")
    if module == "poly_integral":
        coeffs = [Fraction(n, d) for n, d in meta["coeffs"]]
        lo = Fraction(*meta["lo"])
        hi = Fraction(*meta["hi"])
        degree = len(coeffs) - 1
        total = Fraction(0)
        for index, coeff in enumerate(coeffs):
            power = degree - index
            total += coeff * (hi ** (power + 1) - lo ** (power + 1)) / (power + 1)
        return total
    if module == "geometric":
        base = Fraction(*meta["base"])
        # 无穷几何级数：用前 400 项部分和做独立数值验证（不加生成器的闭式结果）
        partial = sum(base ** k for k in range(400))
        expected = Fraction(1, 1) / (1 - base)
        assert abs(partial - expected) < Fraction(1, 10 ** 30)
        return expected
    if module == "partial_geometric":
        return sum(Fraction(meta["base"]) ** k for k in range(meta["upper"] + 1))
    if module == "limit_ratio":
        return Fraction(meta["a"], meta["c"])
    if module == "complex_modulus":
        return Fraction(math.isqrt(meta["a"] ** 2 + meta["b"] ** 2))
    if module == "determinant":
        return _independent_det(meta["matrix"])
    if module == "divisor_count":
        return Fraction(_independent_divisor_count(meta["n"]))
    if module == "phi_quotient":
        return Fraction(_independent_phi(meta["n"]), meta["divisor"])
    if module == "nested_integral":
        return Fraction(meta["constant"], math.factorial(meta["depth"]))
    if module == "nested_limit":
        return Fraction(1)
    if module == "telescoping":
        partial = sum(Fraction(1, k * (k + 1)) for k in range(1, 5000))
        assert abs(partial - 1) < Fraction(1, 1000)
        return Fraction(1)
    if module == "geometric_tail":
        partial = sum(Fraction(1, 2 ** k) for k in range(1, 200))
        assert abs(partial - 1) < Fraction(1, 10 ** 20)
        return Fraction(1)
    if module == "series_rearrange":
        # Σ_{n=2}^∞ 2^{-n} = (1/4)/(1-1/2) = 1/2，再乘 2
        first_term = Fraction(1, 4)
        ratio = Fraction(1, 2)
        return 2 * (first_term / (1 - ratio))
    if module == "double_integral":
        return Fraction(1)
    if module == "euler_phi_sum":
        n = meta["n"]
        return Fraction(sum(_independent_phi(d) for d in range(1, n + 1) if n % d == 0), n)
    if module == "algebra":
        return ast_eval.parse_and_evaluate(meta["expr"])
    raise AssertionError(f"未知模块，需要测试侧补充独立复算：{module}")


# ---------------------------------------------------------------- 种子 / 等级
def test_seed_lengths_per_level():
    assert levels.LEVEL_SEED_LENGTHS == {1: 12, 2: 20, 3: 24, 4: 32, 5: 64}
    for level in levels.LEVELS:
        seed = levels.make_seed(level)
        assert len(seed) == levels.seed_length(level)
        assert seed[0] != "0"
        assert seed.isdigit()


def test_zc_roundtrip_and_grouping():
    for level, seed in (
        (1, "202611130001"),
        (2, "20261113000000000001"),
        (5, "20261113" + "0" * 56),
    ):
        zc = levels.zc_encode(seed)
        assert set(zc) <= set("ZzCc")
        assert levels.parse_seed_input(zc, "zc", level) == seed
        assert levels.zc_decode_int(zc) == int(seed)
        preview = levels.seed_preview(seed, "zc")
        assert " " in preview
        for line in preview.splitlines():
            assert all(len(group) <= 4 for group in line.split(" "))


def test_zc_invalid_inputs():
    with pytest.raises(ValueError, match="交替"):
        levels.zc_decode_int("Cc")          # 第 1 位应为 Z
    with pytest.raises(ValueError):
        levels.zc_decode_int("ZcX")
    with pytest.raises(ValueError):
        levels.parse_seed_input("ZcZc", "number", 1)   # 数字模式不能是字母
    with pytest.raises(ValueError):
        levels.parse_seed_input("2026", "number", 1)   # L1 需要 12 位


def test_seed_input_fixed_length():
    seed = "202611130001"
    assert levels.parse_seed_input(seed, "number", 1) == seed
    with pytest.raises(ValueError, match="12 位"):
        levels.parse_seed_input("2026111300001", "number", 1)
    with pytest.raises(ValueError):
        levels.parse_seed_input("", "number", 1)


# ---------------------------------------------------------------- 随机模式
def _make_equation(level: int, seed: str | None = None) -> R.RandomEquation:
    seed = seed or levels.default_seed(level)
    return R.generate(seed, level)


def test_random_equation_is_deterministic():
    for level in levels.LEVELS:
        seed = levels.default_seed(level)
        first = R.generate(seed, level)
        second = R.generate(seed, level)
        assert first.formula_text == second.formula_text
        assert first.latex == second.latex


def test_random_equation_depths_and_values():
    for level in levels.LEVELS:
        for seed in (levels.default_seed(level), levels.make_seed(level)):
            equation = R.generate(seed, level)
            low, high = levels.depth_range(level)
            assert low <= equation.depth <= high, (level, equation.depth)
            assert equation.evaluate() == Fraction(325)
            assert equation.verify_with_ast() == Fraction(325)
            assert len(equation.factors) >= 3
            assert equation.latex.strip()
            assert equation.formula_text.strip()


def test_random_equation_factors_are_independently_verifiable():
    for level in levels.LEVELS:
        for seed in (levels.default_seed(level), levels.make_seed(level)):
            equation = R.generate(seed, level)
            for factor in equation.factors:
                assert factor.value == verify_factor_independently(factor)
                assert factor.pretty.strip()
                assert factor.latex.strip()
            product = Fraction(1)
            for factor in equation.factors:
                product *= verify_factor_independently(factor)
            assert product == Fraction(325)


def test_random_equation_has_no_naked_target():
    banned = ("325", "650", "13·25", "13×25", "13 × 25", "25×13", "13*25")
    for level in levels.LEVELS:
        for seed in (levels.default_seed(level), levels.make_seed(level)):
            formula = R.generate(seed, level).formula_text
            for token in banned:
                assert token not in formula, (level, token, formula)


def test_level1_is_four_operations_only():
    equation = _make_equation(1)
    allowed = set("0123456789 +−×÷().")
    assert set(equation.formula_text) <= allowed
    assert equation.depth == 0
    assert equation.verify_with_ast() == Fraction(325)


def test_l2_classic_modules_unpublished_shape():
    equation = _make_equation(2)
    text = equation.formula_text
    assert any(symbol in text for symbol in ("∫", "Σ", "lim", "|")), text


def test_l5_has_advanced_modules():
    equation = _make_equation(5)
    modules = equation.module_counts()
    advanced = {"determinant", "divisor_count", "phi_quotient", "nested_integral", "nested_limit"}
    assert advanced & set(modules), modules
    assert equation.depth >= 10


def test_zc_seed_generates_same_formula_as_number():
    seed = "20261113000000000001"   # L2 20 位
    zc = levels.zc_encode(seed)
    by_number = R.generate(seed, 2, "number")
    by_zc = R.generate(zc, 2, "zc")
    assert by_number.formula_text == by_zc.formula_text
    assert by_zc.seed_type == "zc"
    assert by_zc.seed_display == zc


def test_different_seeds_give_different_formulas():
    seeds = [levels.make_seed(2) for _ in range(4)]
    formulas = {R.generate(seed, 2).formula_text for seed in seeds}
    assert len(formulas) >= 3


# ---------------------------------------------------------------- 求解模式
def test_solver_readme_example_1000():
    result = solver.solve("1000", solver.SolveOptions(pattern="325", max_blocks=2, time_budget_ms=8000))
    assert result.ok, result.error
    assert result.verified
    assert result.blocks <= 2
    assert result.pretty
    assert result.latex.strip()
    # 独立解析复算
    assert ast_eval.parse_and_evaluate(result.expression) == 1000
    writings = [result.pretty] + [item["pretty"] for item in result.alternatives]
    assert any("325×3+25" in text for text in writings), writings
    for item in result.alternatives:
        check = solver.verify_expression(item["expression"], 1000, pattern="325")
        assert check.ok, (item["expression"], check.error)


def test_solver_simple_and_decimal_negative_targets():
    r1 = solver.solve("325", solver.SolveOptions(pattern="325", max_blocks=1, time_budget_ms=2000))
    assert r1.ok and r1.expression == "325"
    r2 = solver.solve("3.25", solver.SolveOptions(pattern="325", max_blocks=1, time_budget_ms=2000))
    assert r2.ok and ast_eval.parse_and_evaluate(r2.expression) == Fraction(13, 4)
    r3 = solver.solve("-7", solver.SolveOptions(pattern="325", max_blocks=3, time_budget_ms=5000))
    assert r3.ok and ast_eval.parse_and_evaluate(r3.expression) == -7


def test_solver_no_solution_branch():
    result = solver.solve("1000", solver.SolveOptions(pattern="325", max_blocks=1, time_budget_ms=2000))
    assert not result.ok
    assert result.error and ("没有找到" in result.error or "没找到" in result.error)
    assert not result.timeout


def test_solver_timeout_branch():
    result = solver.solve("62", solver.SolveOptions(pattern="325", max_blocks=4, time_budget_ms=0))
    assert not result.ok
    assert result.timeout
    assert result.error and "超时" in result.error


def test_solver_any_order_mode_verifies():
    result = solver.solve(
        "1000", solver.SolveOptions(pattern="325", max_blocks=2, order_mode="any", time_budget_ms=8000)
    )
    assert result.ok, result.error
    check = solver.verify_expression(result.expression, 1000, pattern="325", order_mode="any")
    assert check.digits_ok and check.value_ok


def test_solver_advanced_operations():
    cases = [
        ("8", "23", {"allow_power": True}, "8"),
        ("5", "25", {"allow_sqrt": True}, "5"),
        ("3", "32", {"allow_comb": True}, "3"),
        ("0", "84", {"allow_gcd_mod": True}, "0"),
    ]
    for target, pattern, extra, expected in cases:
        result = solver.solve(
            target,
            solver.SolveOptions(
                pattern=pattern,
                max_blocks=1,
                allow_factorial=False,
                allow_decimal=False,
                time_budget_ms=3000,
                **extra,
            ),
        )
        assert result.ok, (target, pattern, result.error)
        assert ast_eval.parse_and_evaluate(result.expression) == int(expected)
    # 具体运算符确实出现
    power = solver.solve("8", solver.SolveOptions(pattern="23", max_blocks=1, allow_power=True,
                                                 allow_factorial=False, allow_decimal=False))
    assert "^" in power.expression
    comb = solver.solve("3", solver.SolveOptions(pattern="32", max_blocks=1, allow_comb=True,
                                                 allow_factorial=False, allow_decimal=False))
    assert "comb(" in comb.expression


def test_solver_invalid_target():
    result = solver.solve("abc")
    assert not result.ok and "只能输入" in (result.error or "")
    with pytest.raises(solver.SolveError):
        solver.parse_target("1/2")


def test_verify_expression_rejects_bad_values_and_digits():
    check = solver.verify_expression("325*3", 1000, pattern="325")
    assert not check.value_ok and not check.ok
    check2 = solver.verify_expression("333", 333, pattern="325")
    assert not check2.digits_ok
    check3 = solver.verify_expression("1/0", 1, pattern="1")
    assert check3.error


def test_solve_all_tries_builtin_patterns():
    results = solver.solve_all("1000", solver.SolveOptions(max_blocks=2, time_budget_ms=8000))
    assert results and results[0].ok
    assert results[0].pattern in solver.BUILTIN_PATTERNS


def test_solver_latex_is_nonempty():
    result = solver.solve("1000", solver.SolveOptions(pattern="325", max_blocks=2, time_budget_ms=8000))
    assert result.latex.strip()
    assert "\\times" in result.latex or "\\frac" in result.latex


# ---------------------------------------------------------------- 历史 / 收藏
def _entry(seed: str, level: int = 2, formula: str = "f", latex: str = "l") -> CalcEntry:
    return CalcEntry(seed=seed, level=level, seed_type="number", formula=formula, latex=latex)


def test_history_store_persists_and_caps(tmp_path):
    path = tmp_path / "calc325_history.json"
    store = HistoryStore(path)
    for index in range(105):
        store.add(_entry(f"seed-{index:03d}"))
    assert len(store) == 100
    reloaded = HistoryStore(path)
    assert len(reloaded) == 100
    assert reloaded.entries()[0].seed == "seed-104"      # 最新在前
    assert reloaded.entries()[-1].seed == "seed-005"     # 最旧的被挤掉
    store.clear()
    assert len(HistoryStore(path)) == 0


def test_history_store_dedupes_same_seed(tmp_path):
    store = HistoryStore(tmp_path / "h.json")
    store.add(_entry("a", formula="first"))
    store.add(_entry("b"))
    store.add(_entry("a", formula="second"))
    entries = store.entries()
    assert len(entries) == 2
    assert entries[0].key[0] == "a"
    assert entries[0].formula == "second"


def test_store_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ broken", "utf-8")
    assert len(HistoryStore(path)) == 0
    path.write_text(json.dumps([{"seed": "x", "level": 9, "seed_type": "zc"}]), "utf-8")
    store = HistoryStore(path)
    assert len(store) == 1
    assert store.entries()[0].level == 5
    assert store.entries()[0].seed_type == "zc"


def test_favorites_add_remove_and_dedupe(tmp_path):
    store = FavoriteStore(tmp_path / "fav.json")
    store.add(_entry("a", latex="same"))
    store.add(_entry("b", latex="same"))       # 按 LaTeX 去重
    store.add(_entry("c", latex="other"))
    assert len(store) == 2
    assert store.has(("c", 2, "number"))
    assert store.remove(("c", 2, "number"))
    assert not store.has(("c", 2, "number"))
    reloaded = FavoriteStore(tmp_path / "fav.json")
    assert len(reloaded) == 1
    reloaded.clear()
    assert len(reloaded) == 0


# ---------------------------------------------------------------- 旧设置 / 旧接口
def test_level_resolution_is_backward_compatible():
    assert levels.resolve_level(None, None) == 2          # 旧默认复杂度 2 → L2
    assert levels.resolve_level(None, 1) == 1
    assert levels.resolve_level(None, 4) == 4
    assert levels.resolve_level(5, 1) == 5                # 新键优先
    assert levels.level_from_complexity(3) == 3
    assert levels.complexity_for_level(5) == 4


def test_legacy_generator_api_still_works():
    from endbox.modules.calc325.generator import make_puzzle

    for complexity in (1, 2, 3, 4):
        puzzle = make_puzzle("20261113", complexity=complexity)
        assert puzzle.result == 325
        assert sum(term.value for term in puzzle.terms) == 325
        expression = puzzle.expression.replace("×", "*").replace("÷", "//").replace("−", "-")
        assert eval(expression) == 325
        assert puzzle.steps()[-1][0] == "Σ"
