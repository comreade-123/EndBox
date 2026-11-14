# -*- coding: utf-8 -*-
"""325 随机模式：因子链、经典数学模块、恒 1 陷阱层与 L1–L5 嵌套。

设计参考 Calculate325-main（random-design.md / RandomExpr.cs / Traps.cs）：

  · 一条算式 = 若干因子相乘；每个因子带**精确有理值**（Fraction），乘积严格 = 325；
  · 经典模块：∫₀¹ xᵖ dx、几何级数、部分和、极限、复数模长、行列式、数论函数……
  · 陷阱层：值恒等于 1 的可验证变形（伸缩级数、恒等极限、嵌套积分等）；
  · L1 只有四则；L2 单层；L3 固定 4 层嵌套；L4 5–9 层；L5 10+ 层并加入矩阵/数论；
  · 禁止出现裸 325 / 650 / 13·25；生成后由 ``ast_eval`` 独立复算，必须 = 325 才算成功。

每个因子额外带 ``meta``，测试层按 ``meta`` 用独立方法复算（积分、级数、行列式、
数论函数、嵌套积分各自重算），确认模块文案与精确值一致。
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable

from endbox.modules.calc325 import ast_eval, levels
from endbox.core.text import t

TARGET = 325

_IDENTITY = Fraction(1)

# ---------------------------------------------------------------- Unicode 助手
_SUP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "n": "ⁿ", "k": "ᵏ", "i": "ⁱ", "+": "⁺", "-": "⁻", "(": "⁽", ")": "⁾",
}
_SUB = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "n": "ₙ", "k": "ₖ", "i": "ᵢ", "d": "d", "m": "ₘ",
}


def sup(text: Any) -> str:
    return "".join(_SUP.get(ch, ch) for ch in str(text))


def sub(text: Any) -> str:
    return "".join(_SUB.get(ch, ch) for ch in str(text))


def _frac_text(value: Fraction) -> str:
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def frac_latex(value: Fraction) -> str:
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    sign = "-" if value < 0 else ""
    return f"{sign}\\frac{{{abs(value.numerator)}}}{{{value.denominator}}}"


def _var(index: int) -> str:
    return f"x{sub(index)}"


def _linear(var: str, a: int, b: int) -> str:
    """a·var + b（a=1 时省略系数，b=0 时省略常数）。"""
    head = var if a == 1 else f"{a}{var}"
    return f"{head} + {b}" if b else head


# ---------------------------------------------------------------- 数据模型
@dataclass(frozen=True)
class Factor:
    """一个因子：可显示的 Unicode 形式 / LaTeX / 精确值 / 嵌套深度 / 复算元数据。"""

    module: str
    pretty: str
    latex: str
    value: Fraction
    depth: int = 0
    meta: Any = field(default_factory=dict, compare=False, hash=False)

    @property
    def value_text(self) -> str:
        return _frac_text(self.value)

    @property
    def is_identity(self) -> bool:
        return self.value == 1

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "pretty": self.pretty,
            "latex": self.latex,
            "value": [self.value.numerator, self.value.denominator],
            "depth": self.depth,
        }


@dataclass
class RandomEquation:
    seed: str                       # 规范化后的十进制种子（固定长度）
    level: int = 2
    seed_type: str = "number"
    factors: list[Factor] = field(default_factory=list)

    # -- 展示 ---------------------------------------------------------------
    @property
    def level_name(self) -> str:
        return levels.level_name(self.level)

    @property
    def seed_display(self) -> str:
        return levels.seed_display(self.seed, self.seed_type)

    @property
    def seed_preview(self) -> str:
        return levels.seed_preview(self.seed, self.seed_type)

    @property
    def depth(self) -> int:
        return max((factor.depth for factor in self.factors), default=0)

    @property
    def result(self) -> Fraction:
        return Fraction(TARGET)

    @property
    def formula_text(self) -> str:
        return " × ".join(factor.pretty for factor in self.factors)

    def formula_lines(self) -> list[str]:
        """每个因子一行，方便长算式在 QLabel 里换行阅读。"""
        lines: list[str] = []
        for index, factor in enumerate(self.factors):
            prefix = "" if index == 0 else "× "
            lines.append(prefix + factor.pretty)
        return lines

    @property
    def latex(self) -> str:
        return (
            " \\times ".join(f"\\left({factor.latex}\\right)" for factor in self.factors)
            + f" = {TARGET}"
        )

    @property
    def exact_chain_text(self) -> str:
        return " × ".join(factor.value_text for factor in self.factors) + f" = {TARGET}"

    @property
    def verify_expression(self) -> str:
        pieces = []
        for factor in self.factors:
            value = factor.value
            if value.denominator == 1:
                pieces.append(f"({value.numerator})")
            else:
                pieces.append(f"(({value.numerator})/({value.denominator}))")
        return " * ".join(pieces)

    # -- 计算 / 复算 --------------------------------------------------------
    def evaluate(self) -> Fraction:
        result = Fraction(1)
        for factor in self.factors:
            result *= factor.value
        return result

    def verify_with_ast(self) -> Fraction:
        """独立 AST 解析 ``verify_expression`` 后复算，返回结果。"""
        return ast_eval.parse_and_evaluate(self.verify_expression)

    def steps(self) -> list[tuple[int, str, str, int]]:
        rows = []
        for index, factor in enumerate(self.factors, start=1):
            rows.append((index, factor.pretty, factor.value_text, factor.depth))
        return rows

    def module_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for factor in self.factors:
            counts[factor.module] = counts.get(factor.module, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "level": self.level,
            "seed_type": self.seed_type,
            "formula": self.formula_text,
            "latex": self.latex,
            "depth": self.depth,
            "factors": [factor.to_dict() for factor in self.factors],
        }


# ---------------------------------------------------------------- 数学模块
def _poly_value(coeffs: list[Fraction], lo: Fraction, hi: Fraction) -> Fraction:
    """∫_lo^hi (c_n x^n + … + c_0) dx，精确有理数。"""
    total = Fraction(0)
    degree = len(coeffs) - 1
    for index, coeff in enumerate(coeffs):
        power = degree - index
        total += Fraction(coeff) * (Fraction(hi) ** (power + 1) - Fraction(lo) ** (power + 1)) / (power + 1)
    return total


def _poly_text(coeffs: list[Fraction], lo: Fraction, hi: Fraction, var: str = "x") -> str:
    degree = len(coeffs) - 1
    terms: list[str] = []
    for index, coeff in enumerate(coeffs):
        coeff = Fraction(coeff)
        if coeff == 0:
            continue
        power = degree - index
        if power == 0:
            body = _frac_text(abs(coeff))
        elif power == 1:
            body = var if coeff == 1 else (f"{_frac_text(abs(coeff))}{var}" if coeff.denominator != 1 else f"{abs(coeff)}{var}")
        else:
            body = f"{var}{sup(power)}" if coeff == 1 else f"{_frac_text(abs(coeff))}{var}{sup(power)}"
        if coeff < 0:
            terms.append("− " + body if terms else "−" + body)
        else:
            terms.append(("+ " if terms else "") + body)
    if not terms:
        return "0"
    return " ".join(terms)


def _poly_latex(coeffs: list[Fraction], lo: Fraction, hi: Fraction, var: str = "x") -> str:
    degree = len(coeffs) - 1
    terms: list[str] = []
    for index, coeff in enumerate(coeffs):
        coeff = Fraction(coeff)
        if coeff == 0:
            continue
        power = degree - index
        coef_tex = "" if abs(coeff) == 1 and power > 0 else frac_latex(abs(coeff))
        if power == 0:
            body = frac_latex(abs(coeff))
        elif power == 1:
            body = f"{coef_tex}{var}"
        else:
            body = f"{coef_tex}{var}^{{{power}}}"
        if coeff < 0:
            terms.append("-" + body)
        else:
            terms.append(("+" if terms else "") + body)
    return "".join(terms) or "0"


def _bound(value: Fraction) -> str:
    value = Fraction(value)
    if value.denominator == 1:
        return sup(abs(value.numerator)) if value >= 0 else "−" + sup(abs(value.numerator))
    return _frac_text(value)


def poly_integral_factor(
    coeffs: list[Fraction], lo: Fraction, hi: Fraction, var: str = "x"
) -> Factor:
    value = _poly_value(coeffs, lo, hi)
    lower = sub(abs(Fraction(lo).numerator))
    upper = sup(abs(Fraction(hi).numerator))
    pretty = f"∫{lower}{upper} ({_poly_text(coeffs, lo, hi, var)}) d{var}"
    latex = (
        f"\\int_{{{frac_latex(Fraction(lo))}}}^{{{frac_latex(Fraction(hi))}}}"
        f"\\left({_poly_latex(coeffs, lo, hi, var)}\\right)\\,d{var}"
    )
    return Factor("poly_integral", pretty, latex, value, 1, {
        "module": "poly_integral",
        "coeffs": [[Fraction(c).numerator, Fraction(c).denominator] for c in coeffs],
        "lo": [Fraction(lo).numerator, Fraction(lo).denominator],
        "hi": [Fraction(hi).numerator, Fraction(hi).denominator],
    })


def geometric_factor(base: Fraction) -> Factor:
    value = Fraction(1, 1) / (1 - base)
    pretty = f"Σₖ₌₀→∞ ({_frac_text(base)}){sup('k')}"
    latex = (
        f"\\sum_{{k=0}}^{{\\infty}}\\left({frac_latex(base)}\\right)^{{k}}"
    )
    return Factor("geometric", pretty, latex, value, 1, {
        "module": "geometric",
        "base": [Fraction(base).numerator, Fraction(base).denominator],
    })


def partial_geometric_factor(base: int, upper: int) -> Factor:
    value = sum(Fraction(base) ** k for k in range(upper + 1))
    pretty = f"Σₖ₌₀{sup(upper)} {base}{sup('k')}"
    latex = f"\\sum_{{k=0}}^{{{upper}}}{base}^{{k}}"
    return Factor("partial_geometric", pretty, latex, value, 1, {
        "module": "partial_geometric",
        "base": base,
        "upper": upper,
    })


def limit_ratio_factor(a: int, b: int, c: int, d: int) -> Factor:
    value = Fraction(a, c)
    num = _linear("n", a, b)
    den = _linear("n", c, d)
    pretty = f"limₙ→∞ ({num})/({den})"
    latex = f"\\lim_{{n\\to\\infty}}\\frac{{{num}}}{{{den}}}"
    return Factor("limit_ratio", pretty, latex, value, 1, {
        "module": "limit_ratio",
        "a": a, "b": b, "c": c, "d": d,
    })


def complex_modulus_factor(a: int, b: int) -> Factor:
    square = a * a + b * b
    root = math.isqrt(square)
    if root * root != square:
        raise ValueError("complex modulus is not rational")
    sign = " + " if b >= 0 else " − "
    pretty = f"|{a}{sign}{abs(b)}i|"
    latex = f"\\left|{a}{'+' if b >= 0 else '-'}{abs(b)}i\\right|"
    return Factor("complex_modulus", pretty, latex, Fraction(root), 0, {
        "module": "complex_modulus", "a": a, "b": b,
    })


def determinant(matrix: list[list[int]]) -> Fraction:
    """Fraction 高斯消元（精确）。"""
    size = len(matrix)
    work = [[Fraction(x) for x in row] for row in matrix]
    result = Fraction(1)
    for col in range(size):
        pivot = next((row for row in range(col, size) if work[row][col] != 0), None)
        if pivot is None:
            return Fraction(0)
        if pivot != col:
            work[col], work[pivot] = work[pivot], work[col]
            result = -result
        pivot_value = work[col][col]
        result *= pivot_value
        for row in range(col + 1, size):
            factor = work[row][col] / pivot_value
            if factor:
                for index in range(col, size):
                    work[row][index] -= factor * work[col][index]
    return result


def determinant_factor(matrix: list[list[int]]) -> Factor:
    value = determinant(matrix)
    rows = "; ".join(" ".join(str(x) for x in row) for row in matrix)
    pretty = f"det [{rows}]"
    if len(matrix) == 2 and len(matrix[0]) == 2:
        a, b = matrix[0]
        c, d = matrix[1]
        # mathtext 不支持 pmatrix 环境，2×2 行列式写成 mathtext 可渲染的展开式
        latex = f"\\mathrm{{det}}\\left({a}\\cdot {d} - {b}\\cdot {c}\\right)"
    else:
        latex = "\\mathrm{det}"
    return Factor("determinant", pretty, latex, value, 1, {
        "module": "determinant", "matrix": [list(row) for row in matrix],
    })


def divisor_count(value: int) -> int:
    count = 0
    for d in range(1, math.isqrt(value) + 1):
        if value % d == 0:
            count += 1 if d * d == value else 2
    return count


def divisor_count_factor(value: int, pretty_n: str) -> Factor:
    total = divisor_count(value)
    pretty = f"τ({pretty_n})"
    latex = f"\\tau\\left({pretty_n.replace('¹²', '^{12}').replace('²', '^{2}')}\\right)"
    return Factor("divisor_count", pretty, latex, Fraction(total), 1, {
        "module": "divisor_count", "n": value,
    })


def euler_phi(value: int) -> int:
    result = value
    n = value
    d = 2
    while d * d <= n:
        if n % d == 0:
            while n % d == 0:
                n //= d
            result -= result // d
        d += 1
    if n > 1:
        result -= result // n
    return result


def phi_quotient_factor(value: int, divisor: int) -> Factor:
    exact = Fraction(euler_phi(value), divisor)
    pretty = f"φ({value}) ÷ {divisor}"
    latex = f"\\frac{{\\varphi({value})}}{{{divisor}}}"
    return Factor("phi_quotient", pretty, latex, exact, 1, {
        "module": "phi_quotient", "n": value, "divisor": divisor,
    })


def nested_integral_factor(depth: int) -> Factor:
    depth = max(1, int(depth))
    constant = math.factorial(depth)
    symbols = ["∫₀¹"] + [f"∫₀^{_var(index - 1)}" for index in range(2, depth + 1)]
    diffs = "".join(f" d{_var(index)}" for index in range(depth, 0, -1))
    pretty = "".join(symbols) + f" {depth}!{diffs}"
    latex_symbols = ["\\int_{0}^{1}"] + [
        f"\\int_{{0}}^{{x_{{{index - 1}}}}}" for index in range(2, depth + 1)
    ]
    latex_diffs = "".join(f"\\,dx_{{{index}}}" for index in range(depth, 0, -1))
    latex = "".join(latex_symbols) + f" {depth}!{latex_diffs}"
    return Factor("nested_integral", pretty, latex, Fraction(1), depth, {
        "module": "nested_integral", "depth": depth, "constant": constant,
    })


def nested_limit_factor(depth: int) -> Factor:
    depth = max(1, int(depth))
    limits = " ".join(f"limₙ{sub(index)}→∞" for index in range(1, depth + 1))
    product = "".join(f"n{sub(index)}" for index in range(1, depth + 1))
    pretty = f"{limits} (1 − 1/({product}))"
    latex_limits = " ".join(f"\\lim_{{n_{{{i}}}\\to\\infty}}" for i in range(1, depth + 1))
    latex_product = "".join(f"n_{{{i}}}" for i in range(1, depth + 1))
    latex = f"{latex_limits}\\left(1-\\frac{{1}}{{{latex_product}}}\\right)"
    return Factor("nested_limit", pretty, latex, Fraction(1), depth, {
        "module": "nested_limit", "depth": depth,
    })


# ---------------------------------------------------------------- 恒 1 陷阱层
def telescoping_identity() -> Factor:
    return Factor(
        "telescoping",
        "Σₙ₌₁→∞ 1/(n(n+1))",
        "\\sum_{n=1}^{\\infty}\\frac{1}{n(n+1)}",
        Fraction(1), 1,
        {"module": "telescoping"},
    )


def geometric_tail_identity() -> Factor:
    return Factor(
        "geometric_tail",
        "Σₙ₌₁→∞ 2⁻ⁿ",
        "\\sum_{n=1}^{\\infty}2^{-n}",
        Fraction(1), 1,
        {"module": "geometric_tail"},
    )


def limit_identity() -> Factor:
    return limit_ratio_factor(1, 0, 1, 1)


def poly_integral_identity() -> Factor:
    return poly_integral_factor([2, 0], Fraction(0), Fraction(1))


def series_rearrange_identity() -> Factor:
    return Factor(
        "series_rearrange",
        "2 · Σₙ₌₂→∞ 2⁻ⁿ",
        "2\\cdot\\sum_{n=2}^{\\infty}2^{-n}",
        Fraction(1), 1,
        {"module": "series_rearrange"},
    )


def double_integral_identity() -> Factor:
    return Factor(
        "double_integral",
        "∫₀¹∫₀¹ 4xy dx dy",
        "\\int_{0}^{1}\\!\\int_{0}^{1}4xy\\,dx\\,dy",
        Fraction(1), 2,
        {"module": "double_integral"},
    )


def matrix_identity() -> Factor:
    return determinant_factor([[2, 1], [1, 1]])


def euler_phi_sum_identity(value: int = 12) -> Factor:
    total = sum(euler_phi(d) for d in range(1, value + 1) if value % d == 0)
    exact = Fraction(total, value)  # Σ_{d|n} φ(d) = n
    pretty = f"Σ(d|{value}) φ(d) ÷ {value}"
    latex = f"\\frac{{\\sum_{{d\\mid {value}}}\\varphi(d)}}{{{value}}}"
    return Factor("euler_phi_sum", pretty, latex, exact, 1, {
        "module": "euler_phi_sum", "n": value,
    })


def _algebra_factor(module: str, expr: str, value: Fraction, depth: int = 0) -> Factor:
    pretty = (
        expr.replace("*", "×")
        .replace("/", "÷")
        .replace("-", "−")
        .replace("^2", "²")
    )
    latex = ast_eval.expression_to_latex(expr)
    return Factor(module, pretty, latex, value, depth, {"module": "algebra", "expr": expr})


def power_identity_factor(rng: random.Random) -> Factor:
    a = rng.randint(2, 40)
    expr = f"(({a}+1)^2-{a}^2-2*{a})"
    pretty = f"({a}+1)² − {a}² − 2·{a}"
    return Factor("power_identity", pretty, ast_eval.expression_to_latex(expr), Fraction(1), 0, {
        "module": "algebra", "expr": expr,
    })


def factorial_identity_factor(rng: random.Random) -> Factor:
    n = rng.randint(4, 9)
    expr = f"({n}!/({n}*({n}-1)!))"
    pretty = f"{n}! ÷ ({n}·({n}−1)!)"
    return Factor("factorial_identity", pretty, ast_eval.expression_to_latex(expr), Fraction(1), 0, {
        "module": "algebra", "expr": expr,
    })


def decimal_identity_factor() -> Factor:
    expr = "(0.5+0.5)"
    return Factor("decimal_identity", "0.5 + 0.5", ast_eval.expression_to_latex(expr), Fraction(1), 0, {
        "module": "algebra", "expr": expr,
    })


def four_ops_identity_factor(rng: random.Random) -> Factor:
    mode = rng.choice(("sub", "div", "mul"))
    k = rng.randint(30, 90)
    if mode == "sub":
        expr = f"(({k + 1})-{k})"
    elif mode == "div":
        expr = f"(({k * 3})/({k * 3}))"
    else:
        expr = f"(({k}*7)/({k}*7))"
    pretty = (
        expr.replace("*", "×").replace("/", "÷").replace("-", "−")
    )
    return Factor("four_ops_identity", pretty, ast_eval.expression_to_latex(expr), Fraction(1), 0, {
        "module": "algebra", "expr": expr,
    })


def four_ops_factor(target: int, rng: random.Random) -> Factor:
    """L1：只用 + − × ÷ 的整数算式，精确等于 target。"""
    def _add() -> str:
        a = rng.randint(2, max(2, target - 2))
        return f"({a}+{target - a})"

    def _sub() -> str:
        b = rng.randint(3, 60)
        return f"({target + b}-{b})"

    def _div() -> str:
        b = rng.randint(2, 9)
        return f"({target * b}/{b})"

    def _mul() -> str | None:
        for a in range(2, math.isqrt(target) + 1):
            if target % a == 0:
                return f"({a}*{target // a})"
        return None

    builders: list[Callable[[], str]] = [_add, _sub, _div]
    if target > 1 and not _is_prime(target):
        builders.append(lambda: _mul() or _add())
    expr = rng.choice(builders)()
    pretty = expr.replace("*", "×").replace("/", "÷").replace("-", "−")
    return Factor("four_ops", pretty, ast_eval.expression_to_latex(expr), Fraction(target), 0, {
        "module": "algebra", "expr": expr,
    })


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    for d in range(2, math.isqrt(value) + 1):
        if value % d == 0:
            return False
    return True


# ---------------------------------------------------------------- 等级配置
CORE_DECOMPOSITIONS: list[list[Fraction]] = [
    [Fraction(13), Fraction(5), Fraction(5)],
    [Fraction(13), Fraction(5, 2), Fraction(2), Fraction(5)],
    [Fraction(13), Fraction(3), Fraction(5, 3), Fraction(5)],
    [Fraction(13), Fraction(4), Fraction(5, 4), Fraction(5)],
]
L1_DECOMPOSITIONS: list[list[int]] = [[13, 5, 5], [25, 13]]


def _classic_factor_for(value: Fraction, level: int, rng: random.Random, used_special: bool) -> tuple[Factor, bool]:
    """按目标值挑一个经典模块；L5 优先派发行列式/数论模块。"""
    value = Fraction(value)
    if level >= 5 and not used_special:
        if value == 13:
            choice = rng.choice(("det13", "tau13"))
            if choice == "det13":
                return determinant_factor([[3, 2], [1, 5]]), True
            return divisor_count_factor(4096, "2¹²"), True
        if value == 5:
            choice = rng.choice(("det5", "phi5"))
            if choice == "det5":
                return determinant_factor([[2, 1], [1, 3]]), True
            return phi_quotient_factor(11, 2), True

    options: dict[Fraction, list[Callable[[], Factor]]] = {
        Fraction(13): [
            lambda: partial_geometric_factor(3, 2),
            lambda: complex_modulus_factor(5, 12),
            lambda: poly_integral_factor([2, Fraction(9, 2)], Fraction(0), Fraction(2)),
        ],
        Fraction(5): [
            lambda: geometric_factor(Fraction(4, 5)),
            lambda: poly_integral_factor([4, 3], Fraction(0), Fraction(1)),
            lambda: limit_ratio_factor(5, 3, 1, 1),
            lambda: complex_modulus_factor(3, 4),
        ],
        Fraction(5, 2): [
            lambda: geometric_factor(Fraction(3, 5)),
            lambda: poly_integral_factor([3, 1], Fraction(0), Fraction(1)),
            lambda: limit_ratio_factor(5, 1, 2, 1),
        ],
        Fraction(2): [
            lambda: geometric_factor(Fraction(1, 2)),
            lambda: poly_integral_factor([2], Fraction(0), Fraction(1)),
            lambda: limit_ratio_factor(2, 3, 1, 1),
        ],
        Fraction(3): [
            lambda: geometric_factor(Fraction(2, 3)),
            lambda: poly_integral_factor([6, 0], Fraction(0), Fraction(1)),
            lambda: limit_ratio_factor(3, 1, 1, 1),
        ],
        Fraction(4): [
            lambda: geometric_factor(Fraction(3, 4)),
            lambda: poly_integral_factor([2, 0], Fraction(0), Fraction(2)),
            lambda: limit_ratio_factor(4, 1, 1, 1),
        ],
        Fraction(5, 3): [
            lambda: geometric_factor(Fraction(2, 5)),
            lambda: limit_ratio_factor(5, 2, 3, 1),
        ],
        Fraction(5, 4): [
            lambda: geometric_factor(Fraction(1, 5)),
            lambda: limit_ratio_factor(5, 1, 4, 1),
        ],
    }
    factory = rng.choice(options[value])
    return factory(), used_special


def _classic_identity(rng: random.Random) -> Factor:
    return rng.choice(
        (telescoping_identity, geometric_tail_identity, limit_identity, poly_integral_identity)
    )()


def _advanced_identity(rng: random.Random) -> Factor:
    return rng.choice(
        (
            lambda: power_identity_factor(rng),
            lambda: factorial_identity_factor(rng),
            decimal_identity_factor,
        )
    )()


# ---------------------------------------------------------------- 生成
def generate(seed_text: str, level: int = levels.DEFAULT_LEVEL, seed_type: str = "number") -> RandomEquation:
    """确定性生成一条「因子乘积 = 325」的随机算式。

    ``seed_text`` 可以是十进制种子或 Zc 串；返回值里的 ``seed`` 是规范化的
    固定长度十进制种子。非法种子抛 ValueError。
    """
    level = levels.normalize_level(level)
    seed_type = "zc" if seed_type == "zc" else "number"
    canonical = levels.parse_seed_input(seed_text, seed_type, level)
    rng = levels.rng_for(canonical, level, salt="random")

    if level == 1:
        decomposition = [Fraction(v) for v in rng.choice(L1_DECOMPOSITIONS)]
        core = [four_ops_factor(int(value), rng) for value in decomposition]
        identities: list[Factor] = [four_ops_identity_factor(rng) for _ in range(rng.randint(2, 3))]
    else:
        decomposition = list(rng.choice(CORE_DECOMPOSITIONS))
        core = []
        used_special = False
        for value in decomposition:
            factor, used_special = _classic_factor_for(value, level, rng, used_special)
            core.append(factor)
        identities = [_classic_identity(rng)]
        if level == 3:
            identities.append(nested_integral_factor(4))
            identities.append(_advanced_identity(rng))
        elif level == 4:
            identities.append(nested_integral_factor(rng.randint(5, 9)))
            identities.append(double_integral_identity())
            identities.append(series_rearrange_identity())
            identities.append(_advanced_identity(rng))
        elif level >= 5:  # L5
            identities.append(nested_integral_factor(rng.randint(10, 12)))
            identities.append(nested_limit_factor(3))
            identities.append(euler_phi_sum_identity(12))
            identities.append(matrix_identity())
            identities.append(_advanced_identity(rng))

    factors = core + identities
    rng.shuffle(factors)

    equation = RandomEquation(seed=canonical, level=level, seed_type=seed_type, factors=factors)
    if equation.evaluate() != TARGET:
        raise RuntimeError(t("calc325.random.internal.product", product=equation.evaluate(), target=TARGET))
    low, high = levels.depth_range(level)
    if not (low <= equation.depth <= high):
        raise RuntimeError(t("calc325.random.internal.depth", level=level, depth=equation.depth, low=low, high=high))
    if equation.verify_with_ast() != TARGET:
        raise RuntimeError(t("calc325.random.internal.verify"))
    return equation


def generate_for_seed(seed_decimal: str, level: int = levels.DEFAULT_LEVEL) -> RandomEquation:
    """用规范化的十进制种子直接生成（种子类型只影响显示）。"""
    level = levels.normalize_level(level)
    canonical = levels.parse_seed_input(seed_decimal, "number", level)
    return generate(canonical, level, "number")
