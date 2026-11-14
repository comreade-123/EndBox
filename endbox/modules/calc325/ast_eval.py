# -*- coding: utf-8 -*-
"""独立表达式解析 / 求值层（递归下降 + ``fractions.Fraction``）。

这个模块**不共享**求解器或随机生成器的中间结果：它只吃最终打印出来的
ASCII 式子字符串，重新分词、建 AST、精确求值。用途：

  · 求解模式：对 DP 产出的算式做「独立复算」，确认它真的等于目标；
  · 随机模式：对因子链的精确值表达式做独立复算，确认乘积严格等于 325；
  · LaTeX 输出：从同一棵 AST 渲染，避免字符串替换拼错。

支持语法：整数 / 小数、``+ − × ÷``、括号、后缀阶乘 ``!``、乘方 ``^``、
``sqrt()``、``gcd()``、``mod()``、``comb()``。求值全部用 Fraction，无浮点误差。
"""
from __future__ import annotations

import math
import re
from fractions import Fraction
from typing import Any

#: AST 节点是元组：
#:   ("num", Fraction, 原文)
#:   ("bin", "+"|"-"|"*"|"/"|"%"|"gcd"|"mod"|"comb", left, right)
#:   ("neg", node) / ("fact", node) / ("pow", base, exp)
#:   ("call", "sqrt", [arg])

_NORMALIZE = str.maketrans(
    {
        "×": "*",
        "✕": "*",
        "✖": "*",
        "÷": "/",
        "−": "-",
        "–": "-",
        "—": "-",
        "（": "(",
        "）": ")",
        "！": "!",
        "％": "%",
    }
)

_DIGITS_RE = re.compile(r"\d+(?:\.\d+)?")

PREC_ADD = 1
PREC_MUL = 2
PREC_ATOM = 3


class EvalError(ValueError):
    """表达式语法或数值错误；消息直接给界面显示。"""


def normalize(text: str) -> str:
    return str(text or "").translate(_NORMALIZE).replace(" ", "").replace("\n", "").replace("\r", "")


def digits_of(text: str) -> str:
    return "".join(ch for ch in normalize(text) if ch.isdigit())


def count_tokens(text: str) -> int:
    return len(_DIGITS_RE.findall(normalize(text)))


class _Parser:
    def __init__(self, text: str, *, fact_limit: int = 5000):
        self.source = normalize(text)
        self.pos = 0
        self.fact_limit = int(fact_limit)

    # -- 基础读取 -----------------------------------------------------------
    def _peek(self) -> str:
        return self.source[self.pos] if self.pos < len(self.source) else ""

    def _take(self) -> str:
        ch = self._peek()
        self.pos += 1
        return ch

    def _expect(self, ch: str) -> None:
        if self._peek() != ch:
            raise EvalError(f"缺少「{ch}」")
        self.pos += 1

    def _skip(self) -> None:
        while self._peek() == " ":
            self.pos += 1

    # -- 语法 ---------------------------------------------------------------
    def parse(self) -> tuple[Any, Fraction]:
        node = self._expr()
        if self.pos != len(self.source):
            raise EvalError(f"表达式结尾有多余字符：{self.source[self.pos:self.pos + 8]}")
        return node, evaluate(node, fact_limit=self.fact_limit)

    def _expr(self) -> Any:
        node = self._term()
        while self._peek() in ("+", "-"):
            op = self._take()
            node = ("bin", op, node, self._term())
        return node

    def _term(self) -> Any:
        node = self._unary()
        while self._peek() in ("*", "/", "%"):
            op = self._take()
            node = ("bin", op, node, self._unary())
        return node

    def _unary(self) -> Any:
        if self._peek() == "-":
            self._take()
            return ("neg", self._unary())
        if self._peek() == "+":
            self._take()
            return self._unary()
        return self._power()

    def _power(self) -> Any:
        node = self._postfix()
        if self._peek() == "^":
            self._take()
            node = ("pow", node, self._unary())  # 右结合
        return node

    def _postfix(self) -> Any:
        node = self._primary()
        while self._peek() == "!":
            self._take()
            node = ("fact", node)
        return node

    def _primary(self) -> Any:
        c = self._peek()
        if c == "(":
            self._take()
            node = self._expr()
            self._expect(")")
            return node
        if c.isdigit():
            start = self.pos
            while self._peek().isdigit():
                self.pos += 1
            if self._peek() == ".":
                self.pos += 1
                if not self._peek().isdigit():
                    raise EvalError("小数点后需要数字")
                while self._peek().isdigit():
                    self.pos += 1
            text = self.source[start:self.pos]
            return ("num", _decimal_to_fraction(text), text)
        if c.isalpha():
            name = ""
            while self._peek().isalpha():
                name += self._take()
            if self._peek() != "(":
                raise EvalError(f"无法识别的符号：{name}（函数需要括号）")
            self._take()
            args = []
            if self._peek() != ")":
                args.append(self._expr())
                while self._peek() == ",":
                    self._take()
                    args.append(self._expr())
            self._expect(")")
            return ("call", name.lower(), args)
        raise EvalError(f"无法识别的字符：{c or '(式尾)'}")

def _decimal_to_fraction(text: str) -> Fraction:
    if "." not in text:
        return Fraction(int(text))
    int_part, frac_part = text.split(".", 1)
    den = 10 ** len(frac_part)
    return Fraction(int(int_part or "0") * den + int(frac_part or "0"), den)


def _as_int(value: Fraction, what: str) -> int:
    if value.denominator != 1:
        raise EvalError(f"{what}需要整数，实际是 {value}")
    return int(value)


def evaluate(node: Any, *, fact_limit: int = 5000) -> Fraction:
    """对 AST 精确求值。"""
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "neg":
        return -evaluate(node[1], fact_limit=fact_limit)
    if kind == "fact":
        value = _as_int(evaluate(node[1], fact_limit=fact_limit), "阶乘参数")
        if value < 0:
            raise EvalError("阶乘只能用于非负整数")
        if value > fact_limit:
            raise EvalError(f"阶乘参数过大（上限 {fact_limit}），无法校验")
        return Fraction(math.factorial(value))
    if kind == "pow":
        base = evaluate(node[1], fact_limit=fact_limit)
        exp = evaluate(node[2], fact_limit=fact_limit)
        if exp.denominator != 1 or abs(exp) > 64:
            raise EvalError("乘方指数必须是小整数")
        e = int(exp)
        if base == 0 and e < 0:
            raise EvalError("0 不能取负次幂")
        return base**e
    if kind == "call":
        name, args = node[1], node[2]
        if name == "sqrt":
            if len(args) != 1:
                raise EvalError("sqrt 只需要一个参数")
            value = evaluate(args[0], fact_limit=fact_limit)
            if value < 0:
                raise EvalError("负数不能开平方")
            num = math.isqrt(value.numerator)
            den = math.isqrt(value.denominator)
            if num * num != value.numerator or den * den != value.denominator:
                raise EvalError("sqrt 参数不是完全平方有理数，无法精确求值")
            return Fraction(num, den)
        if name == "gcd":
            if len(args) != 2:
                raise EvalError("gcd 需要两个参数")
            a = abs(_as_int(evaluate(args[0], fact_limit=fact_limit), "gcd 参数"))
            b = abs(_as_int(evaluate(args[1], fact_limit=fact_limit), "gcd 参数"))
            return Fraction(math.gcd(a, b))
        if name == "mod":
            if len(args) != 2:
                raise EvalError("mod 需要两个参数")
            a = _as_int(evaluate(args[0], fact_limit=fact_limit), "mod 参数")
            b = _as_int(evaluate(args[1], fact_limit=fact_limit), "mod 参数")
            if b == 0:
                raise EvalError("mod 的除数不能为 0")
            return Fraction(a % b)
        if name == "comb":
            if len(args) != 2:
                raise EvalError("comb 需要两个参数")
            n = _as_int(evaluate(args[0], fact_limit=fact_limit), "comb 参数")
            k = _as_int(evaluate(args[1], fact_limit=fact_limit), "comb 参数")
            if n < 0 or k < 0 or k > n or n > fact_limit:
                raise EvalError("comb 需要 0 ≤ k ≤ n（n 不宜过大）")
            return Fraction(math.comb(n, k))
        raise EvalError(f"不支持的函数：{name}")
    if kind == "bin":
        op = node[1]
        left = evaluate(node[2], fact_limit=fact_limit)
        right = evaluate(node[3], fact_limit=fact_limit)
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                raise EvalError("出现除以零")
            return left / right
        if op == "%":
            a = _as_int(left, "mod 参数")
            b = _as_int(right, "mod 参数")
            if b == 0:
                raise EvalError("mod 的除数不能为 0")
            return Fraction(a % b)
        if op == "gcd":
            a = abs(_as_int(left, "gcd 参数"))
            b = abs(_as_int(right, "gcd 参数"))
            return Fraction(math.gcd(a, b))
        if op == "mod":
            a = _as_int(left, "mod 参数")
            b = _as_int(right, "mod 参数")
            if b == 0:
                raise EvalError("mod 的除数不能为 0")
            return Fraction(a % b)
        if op == "comb":
            n = _as_int(left, "comb 参数")
            k = _as_int(right, "comb 参数")
            if n < 0 or k < 0 or k > n or n > fact_limit:
                raise EvalError("comb 需要 0 ≤ k ≤ n（n 不宜过大）")
            return Fraction(math.comb(n, k))
        raise EvalError(f"不支持的运算符：{op}")
    raise EvalError("AST 节点损坏")


def parse_ast(text: str, *, fact_limit: int = 5000) -> Any:
    node, _ = _Parser(text, fact_limit=fact_limit).parse()
    return node


def parse_and_evaluate(text: str, *, fact_limit: int = 5000) -> Fraction:
    node, value = _Parser(text, fact_limit=fact_limit).parse()
    return value


# ---------------------------------------------------------------- LaTeX 输出
def to_latex(node: Any) -> str:
    """把 AST 渲染成 LaTeX（与求值共用 AST，避免字符串替换错位）。"""
    kind = node[0]
    if kind == "num":
        text = node[2]
        frac = node[1]
        if frac.denominator == 1:
            return text
        if "." in text:
            return text
        return _latex_frac(frac.numerator, frac.denominator)
    if kind == "neg":
        return "-" + _wrap(node[1], PREC_MUL)
    if kind == "fact":
        return _wrap(node[1], PREC_ATOM) + "!"
    if kind == "pow":
        base = _wrap(node[1], PREC_ATOM)
        exp_node = node[2]
        exp = evaluate(exp_node)
        if exp.denominator == 1 and 0 <= exp <= 9:
            return f"{base}^{{{int(exp)}}}"
        return f"{base}^{{{to_latex(exp_node)}}}"
    if kind == "call":
        name, args = node[1], node[2]
        if name == "sqrt":
            return f"\\sqrt{{{to_latex(args[0])}}}"
        if name == "gcd":
            return f"\\gcd\\left({to_latex(args[0])},\\,{to_latex(args[1])}\\right)"
        if name == "mod":
            return f"\\operatorname{{mod}}\\left({to_latex(args[0])},\\,{to_latex(args[1])}\\right)"
        if name == "comb":
            return f"\\binom{{{to_latex(args[0])}}}{{{to_latex(args[1])}}}"
        return name
    if kind == "bin":
        op = node[1]
        if op == "/":
            return _latex_frac_node(node[2], node[3])
        if op == "%":
            return f"\\operatorname{{mod}}\\left({to_latex(node[2])},\\,{to_latex(node[3])}\\right)"
        if op in ("gcd", "comb"):
            if op == "gcd":
                return f"\\gcd\\left({to_latex(node[2])},\\,{to_latex(node[3])}\\right)"
            return f"\\binom{{{to_latex(node[2])}}}{{{to_latex(node[3])}}}"
        if op == "*":
            return _wrap(node[2], PREC_MUL) + " \\times " + _wrap(node[3], PREC_MUL)
        if op == "+":
            return _wrap(node[2], PREC_ADD) + " + " + _wrap(node[3], PREC_ADD)
        if op == "-":
            return _wrap(node[2], PREC_ADD) + " - " + _wrap(node[3], PREC_ADD)
    return ""


def _latex_frac(num: int, den: int) -> str:
    sign = "-" if (num < 0) != (den < 0) else ""
    num, den = abs(num), abs(den)
    if den == 1:
        return f"{sign}{num}"
    return f"{sign}\\frac{{{num}}}{{{den}}}"


def _latex_frac_node(left: Any, right: Any) -> str:
    return f"\\frac{{{to_latex(left)}}}{{{to_latex(right)}}}"


def _node_prec(node: Any) -> int:
    kind = node[0]
    if kind == "num":
        return PREC_ATOM
    if kind in ("neg", "fact", "pow", "call"):
        return PREC_ATOM
    if kind == "bin":
        return PREC_MUL if node[1] in ("*", "/", "%") else PREC_ADD
    return PREC_ATOM


def _wrap(node: Any, min_prec: int) -> str:
    text = to_latex(node)
    if _node_prec(node) < min_prec:
        return f"\\left({text}\\right)"
    return text


def expression_to_latex(expression: str) -> str:
    """ASCII 表达式 → LaTeX；解析失败时退回简单替换（不抛异常）。"""
    try:
        return to_latex(parse_ast(expression))
    except Exception:  # noqa: BLE001 - 展示层不应因排版失败而崩溃
        return (
            normalize(expression)
            .replace("*", " \\times ")
            .replace("/", "/")
        )
