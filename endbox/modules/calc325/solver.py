# -*- coding: utf-8 -*-
"""325 求解模式：区间 DP + Fraction 精确有理数。

参考 Calculate325-main/src/solver.js 的核心思路：

  · 数字串按块重复（325 / 799 / 325799 / 114514 / 自定义 1–8 位，不以 0 开头）；
  · fixed = 数字顺序固定；any = 数字位置可交换（只要求字符多重集相同）；
  · 数字之间可插 + − × ÷ 与括号；相邻不插运算符就拼成整数；可选小数点、
    后缀阶乘与高级运算（乘方 / 根号 / 组合数 / gcd·mod）；
  · 区间 DP：``dp(sub, t)`` = 用 sub 的数字、恰好 t 个"数"能算出的全部精确有理数，
    按 t 从小到大构建，命中即停 ⇒ 先得到最少块数 / 最少 token 的写法；
  · 输出前由 ``ast_eval`` 这个**独立解析器**重新解析打印出来的算式并复算，
    数字顺序与数值必须同时通过，防止生成器自说自话；
  · 时间预算、数值/分母上限、每轮容量上限防止搜索爆炸；超时 / 无解给人话提示。
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from endbox.modules.calc325 import ast_eval
from endbox.core.text import t

BUILTIN_PATTERNS = ("325", "799", "325799", "114514")
PREC_ADD = 1
PREC_MUL = 2
PREC_ATOM = 3
_DIGITS_RE = re.compile(r"\d+(?:\.\d+)?")


class SolveError(ValueError):
    """参数/输入错误。"""


# ---------------------------------------------------------------- 目标解析
def parse_target(text: Any) -> tuple[Fraction, str]:
    """解析目标：整数、小数、负数、逗号小数点。返回 (精确值, 规范化文本)。"""
    raw = str(text if text is not None else "").strip()
    if not raw:
        raise SolveError(t("calc325.solver.error.target_empty"))
    normalized = (
        raw.replace("，", ".")
        .replace(",", ".")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", "")
    )
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", normalized):
        raise SolveError(t("calc325.solver.error.target_invalid"))
    negative = normalized.startswith("-")
    body = normalized.lstrip("+-")
    if "." in body:
        int_part, frac_part = body.split(".", 1)
        value = Fraction(int(int_part or "0") * 10 ** len(frac_part) + int(frac_part or "0"), 10 ** len(frac_part))
        text = f"{'-' if negative else ''}{int_part or '0'}.{frac_part}"
    else:
        value = Fraction(int(body or "0"))
        text = f"{'-' if negative else ''}{int(body or '0')}"
    if negative:
        value = -value
    return value, text


# ---------------------------------------------------------------- 校验
@dataclass
class VerifyResult:
    expression: str
    digits: str
    blocks: int
    digits_ok: bool
    value: Fraction | None
    value_ok: bool
    tokens: int
    error: str | None = None
    order_mode: str = "fixed"
    pattern: str = "325"

    @property
    def ok(self) -> bool:
        return self.digits_ok and self.value_ok and self.error is None


def normalize_pattern(pattern: Any) -> str:
    text = str(pattern or "").strip()
    if not re.fullmatch(r"\d{1,8}", text) or text.startswith("0"):
        return ""
    return text


def digits_of(expression: str) -> str:
    return ast_eval.digits_of(expression)


def count_tokens(expression: str) -> int:
    return ast_eval.count_tokens(expression)


def _check_digits(digits: str, pattern: str, order_mode: str) -> tuple[bool, int]:
    if not digits or len(digits) % len(pattern) != 0:
        return False, 0
    blocks = len(digits) // len(pattern)
    if order_mode == "any":
        expected = ""
        for _ in range(blocks):
            expected += pattern
        if sorted(digits) != sorted(expected):
            return False, blocks
        return True, blocks
    expected = pattern * blocks
    return digits == expected, blocks


def verify_expression(
    expression: str,
    target: Fraction | int | str,
    *,
    pattern: str = "325",
    order_mode: str = "fixed",
    fact_limit: int = 5000,
) -> VerifyResult:
    """独立复核：数字顺序 + 独立解析求值，两者都必须通过。"""
    pattern = normalize_pattern(pattern) or "325"
    digits = digits_of(expression)
    digits_ok, blocks = _check_digits(digits, pattern, order_mode)
    result = VerifyResult(
        expression=expression,
        digits=digits,
        blocks=blocks,
        digits_ok=digits_ok,
        value=None,
        value_ok=False,
        tokens=count_tokens(expression),
        order_mode=order_mode,
        pattern=pattern,
    )
    try:
        expected = Fraction(target) if not isinstance(target, str) else parse_target(target)[0]
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        result.error = t("calc325.solver.error.target_invalid_value", error=exc)
        return result
    try:
        value = ast_eval.parse_and_evaluate(expression, fact_limit=fact_limit)
    except Exception as exc:  # noqa: BLE001 - 各种语法错误统一展示
        result.error = str(exc)
        return result
    result.value = value
    result.value_ok = value == expected
    if not digits_ok:
        result.error = t("calc325.solver.error.digits_rule")
    elif not result.value_ok:
        result.error = t("calc325.solver.error.value_mismatch", value=value, target=expected)
    return result


def pretty_expression(expression: str) -> str:
    return (
        expression.replace("*", "×")
        .replace("/", "÷")
        .replace("-", "−")
    )


def expression_latex(expression: str) -> str:
    return ast_eval.expression_to_latex(expression)


# ---------------------------------------------------------------- 选项 / 结果
@dataclass
class SolveOptions:
    pattern: str = "325"
    max_blocks: int = 2
    order_mode: str = "fixed"
    allow_factorial: bool = True
    fact_max: int = 12
    allow_decimal: bool = True
    allow_power: bool = False
    allow_sqrt: bool = False
    allow_comb: bool = False
    allow_gcd_mod: bool = False
    time_budget_ms: int = 5000
    solution_limit: int = 8
    max_abs_value: float = 1e12
    max_denominator: int = 10 ** 9
    class_caps: tuple[int, ...] = (
        0, 2000, 4000, 4000, 3000, 2000, 1200, 800, 500, 300, 200, 150, 100, 80, 60, 50, 40, 30, 25, 20, 20,
    )

    def normalized(self) -> "SolveOptions":
        options = SolveOptions(**self.__dict__)
        options.pattern = normalize_pattern(options.pattern) or "325"
        options.max_blocks = max(1, min(6, int(options.max_blocks)))
        options.order_mode = "any" if options.order_mode == "any" else "fixed"
        options.fact_max = max(0, min(20, int(options.fact_max)))
        options.time_budget_ms = max(0, min(60000, int(options.time_budget_ms)))
        options.solution_limit = max(1, min(16, int(options.solution_limit)))
        return options


@dataclass
class SolveResult:
    ok: bool
    target: Fraction | None = None
    target_text: str = ""
    pattern: str = "325"
    order_mode: str = "fixed"
    expression: str = ""
    pretty: str = ""
    latex: str = ""
    blocks: int = 0
    tokens: int = 0
    digits: str = ""
    elapsed_ms: float = 0.0
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    timeout: bool = False
    verified: bool = False
    error: str | None = None
    entries: int = 0
    searched_blocks: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target": str(self.target),
            "expression": self.expression,
            "pretty": self.pretty,
            "blocks": self.blocks,
            "tokens": self.tokens,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "verified": self.verified,
            "alternatives": self.alternatives,
            "error": self.error,
        }


# ---------------------------------------------------------------- DP 内部
@dataclass
class _Entry:
    value: Fraction
    text: str
    prec: int
    tokens: int
    magnitude: float = 0.0


class _State:
    def __init__(self, sub: str):
        self.sub = sub
        self.map: dict[Fraction, _Entry] = {}
        self.by_tokens: list[list[_Entry]] = [[] for _ in range(len(sub) + 1)]
        self.counts: list[int] = [0 for _ in range(len(sub) + 1)]
        self.built = 0


class _Context:
    def __init__(self, options: SolveOptions):
        self.options = options
        self.cache: dict[str, _State] = {}
        self.deadline = time.monotonic() + options.time_budget_ms / 1000.0
        self.aborted = False
        self.pruned = False
        self.limited = False
        self.steps = 0
        self.max_abs = options.max_abs_value
        self.max_den = options.max_denominator
        self.mul_limit = options.max_abs_value * 4

    def expired(self) -> bool:
        if self.aborted:
            return True
        if time.monotonic() > self.deadline:
            self.aborted = True
        return self.aborted

    def tick(self) -> None:
        self.steps += 1
        if (self.steps & 511) == 0:
            self.expired()

    def cap(self, tokens: int) -> int:
        caps = self.options.class_caps
        base = caps[tokens] if tokens < len(caps) else caps[-1]
        # any 模式可交换数字位置，取值空间大得多；放宽每轮容量，靠时间预算兜底。
        return base * 6 if self.options.order_mode == "any" else base


def _wrap_text(text: str, prec_needed: int) -> str:
    return f"({text})" if prec_needed else text


def _paren(text: str) -> str:
    return f"({text})"


def _put(state: _State, tokens: int, value: Fraction, text: str, prec: int, ctx: _Context) -> bool:
    key = value
    existing = state.map.get(key)
    if existing is not None:
        if len(text) < len(existing.text):
            existing.text = text
            existing.prec = prec
        return False
    try:
        magnitude = abs(float(value))
    except (OverflowError, ValueError):
        return False
    if magnitude > ctx.max_abs or value.denominator > ctx.max_den:
        ctx.limited = True
        return False
    if state.counts[tokens] >= ctx.cap(tokens):
        ctx.pruned = True
        return False
    entry = _Entry(value, text, prec, tokens, magnitude)
    state.map[key] = entry
    state.by_tokens[tokens].append(entry)
    state.counts[tokens] += 1
    return True


def _token_values(sub: str, ctx: _Context) -> list[tuple[Fraction, str]]:
    tokens: list[tuple[Fraction, str]] = [(Fraction(int(sub)), sub)]
    if ctx.options.allow_decimal:
        for cut in range(1, len(sub)):
            head, tail = sub[:cut], sub[cut:]
            denominator = 10 ** len(tail)
            tokens.append((Fraction(int(head) * denominator + int(tail), denominator), f"{head}.{tail}"))
    return tokens


def _combine(state: _State, tokens: int, left: _Entry, right: _Entry, ctx: _Context) -> None:
    A, B = left, right
    add_limit = ctx.mul_limit
    if A.magnitude + B.magnitude <= add_limit:
        _put(state, tokens, A.value + B.value, f"{A.text}+{_wrap_text(B.text, B.prec <= PREC_ADD)}", PREC_ADD, ctx)
        _put(state, tokens, A.value - B.value, f"{A.text}-{_wrap_text(B.text, B.prec <= PREC_ADD)}", PREC_ADD, ctx)
        if ctx.options.order_mode == "any":
            _put(state, tokens, B.value - A.value, f"{B.text}-{_wrap_text(A.text, A.prec <= PREC_ADD)}", PREC_ADD, ctx)
    if A.magnitude * B.magnitude <= add_limit:
        a_mul = _wrap_text(A.text, A.prec < PREC_MUL)
        b_mul = _wrap_text(B.text, B.prec < PREC_MUL)
        _put(state, tokens, A.value * B.value, f"{a_mul}*{b_mul}", PREC_MUL, ctx)
    if B.value != 0:
        a_mul2 = _wrap_text(A.text, A.prec < PREC_MUL)
        b_mul2 = _wrap_text(B.text, B.prec <= PREC_MUL)
        _put(state, tokens, A.value / B.value, f"{a_mul2}/{b_mul2}", PREC_MUL, ctx)
        if ctx.options.order_mode == "any" and A.value != 0:
            _put(state, tokens, B.value / A.value,
                 f"{_wrap_text(B.text, B.prec < PREC_MUL)}/{_wrap_text(A.text, A.prec <= PREC_MUL)}", PREC_MUL, ctx)
    if ctx.options.allow_power:
        if B.value.denominator == 1 and 2 <= B.value <= 4:
            base = _wrap_text(A.text, A.prec < PREC_ATOM)
            try:
                powered = A.value ** int(B.value)
            except (OverflowError, ZeroDivisionError):
                powered = None
            if powered is not None and abs(float(powered)) <= add_limit:
                _put(state, tokens, powered, f"{base}^{B.text}", PREC_ATOM, ctx)
    if ctx.options.allow_gcd_mod and A.value.denominator == 1 and B.value.denominator == 1:
        int_a, int_b = int(A.value), int(B.value)
        _put(state, tokens, Fraction(math.gcd(abs(int_a), abs(int_b))), f"gcd({A.text},{B.text})", PREC_ATOM, ctx)
        if int_b != 0:
            _put(state, tokens, Fraction(int_a % int_b), f"mod({A.text},{B.text})", PREC_ATOM, ctx)
    if ctx.options.allow_comb and A.value.denominator == 1 and B.value.denominator == 1:
        n, k = int(A.value), int(B.value)
        if 0 <= k <= n <= 200:
            _put(state, tokens, Fraction(math.comb(n, k)), f"comb({A.text},{B.text})", PREC_ATOM, ctx)


def _apply_unary(state: _State, tokens: int, ctx: _Context) -> None:
    """阶乘与根号不改变 token 个数，构建后做闭包。"""
    for _round in range(6):
        added = False
        snapshot = list(state.by_tokens[tokens])
        for entry in snapshot:
            if ctx.options.allow_factorial:
                value = entry.value
                if value.denominator == 1 and 0 <= value <= ctx.options.fact_max:
                    text = f"{_wrap_text(entry.text, entry.prec < PREC_ATOM)}!"
                    if _put(state, tokens, Fraction(math.factorial(int(value))), text, PREC_ATOM, ctx):
                        added = True
            if ctx.options.allow_sqrt and entry.value >= 0:
                num = math.isqrt(entry.value.numerator)
                den = math.isqrt(entry.value.denominator)
                if num * num == entry.value.numerator and den * den == entry.value.denominator:
                    text = f"sqrt({entry.text})"
                    if _put(state, tokens, Fraction(num, den), text, PREC_ATOM, ctx):
                        added = True
        if not added or ctx.expired():
            break


def _ensure(sub: str, need: int, ctx: _Context) -> _State:
    state = ctx.cache.get(sub)
    if state is None:
        state = _State(sub)
        ctx.cache[sub] = state
    need = min(need, len(sub))
    if state.built >= need:
        return state

    if state.built == 0:
        for value, text in _token_values(sub, ctx):
            _put(state, 1, value, text, PREC_ATOM, ctx)
        state.built = 1
        _apply_unary(state, 1, ctx)
        if need <= 1 or ctx.expired():
            return state

    for tokens in range(state.built + 1, need + 1):
        if ctx.expired():
            break
        for cut in range(1, len(sub)):
            left = _ensure(sub[:cut], tokens - 1, ctx)
            right = _ensure(sub[cut:], tokens - 1, ctx)
            if ctx.expired():
                break
            for left_tokens in range(1, tokens):
                right_tokens = tokens - left_tokens
                if left_tokens >= len(left.by_tokens) or right_tokens >= len(right.by_tokens):
                    continue
                bucket_a = left.by_tokens[left_tokens]
                bucket_b = right.by_tokens[right_tokens]
                if not bucket_a or not bucket_b:
                    continue
                for entry_a in bucket_a:
                    for entry_b in bucket_b:
                        ctx.tick()
                        if ctx.aborted:
                            break
                        _combine(state, tokens, entry_a, entry_b, ctx)
                    if ctx.aborted:
                        break
                if ctx.aborted:
                    break
            if ctx.aborted:
                break
        state.built = tokens
        if ctx.aborted:
            break
        _apply_unary(state, tokens, ctx)
    return state


# ---------------------------------------------------------------- 多种写法
def _collect_writings(
    digits: str, target: Fraction, ctx: _Context, limit: int
) -> list[str]:
    """在已建好的 DP 表上，从顶层运算回溯出同一个值的多种写法。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        if len(out) >= limit or text in seen:
            return
        seen.add(text)
        out.append(text)

    top = ctx.cache.get(digits)
    if top is not None and target in top.map:
        add(top.map[target].text)
    if len(out) >= limit:
        return out

    for cut in range(1, len(digits)):
        left = ctx.cache.get(digits[:cut])
        right = ctx.cache.get(digits[cut:])
        if left is None or right is None:
            continue
        for left_tokens in range(1, len(left.by_tokens)):
            bucket_a = left.by_tokens[left_tokens]
            if not bucket_a:
                continue
            for right_tokens in range(1, len(right.by_tokens)):
                bucket_b = right.by_tokens[right_tokens]
                if not bucket_b:
                    continue
                for entry_a in bucket_a:
                    for entry_b in bucket_b:
                        ctx.tick()
                        if ctx.expired() or len(out) >= limit:
                            return out
                        A, B = entry_a, entry_b
                        b_add = _wrap_text(B.text, B.prec <= PREC_ADD)
                        a_add = _wrap_text(A.text, A.prec <= PREC_ADD)
                        if A.value + B.value == target:
                            add(f"{A.text}+{b_add}")
                        if A.value - B.value == target:
                            add(f"{A.text}-{b_add}")
                        if A.value * B.value == target:
                            add(f"{_wrap_text(A.text, A.prec < PREC_MUL)}*{_wrap_text(B.text, B.prec < PREC_MUL)}")
                        if B.value != 0 and A.value / B.value == target:
                            add(f"{_wrap_text(A.text, A.prec < PREC_MUL)}/{_wrap_text(B.text, B.prec <= PREC_MUL)}")
                        if ctx.options.order_mode == "any":
                            if B.value - A.value == target:
                                add(f"{B.text}-{a_add}")
                            if A.value != 0 and B.value / A.value == target:
                                add(f"{_wrap_text(B.text, B.prec < PREC_MUL)}/{_wrap_text(A.text, A.prec <= PREC_MUL)}")
                        if len(out) >= limit:
                            return out
    return out


def _rank_alternatives(
    expressions: list[str], target: Fraction, options: SolveOptions, ctx: _Context
) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    seen: set[str] = set()
    for expression in expressions:
        if expression in seen:
            continue
        seen.add(expression)
        check = verify_expression(
            expression,
            target,
            pattern=options.pattern,
            order_mode=options.order_mode,
            fact_limit=max(5000, options.fact_max + 5),
        )
        if not check.ok:
            continue
        verified.append({
            "expression": expression,
            "pretty": pretty_expression(expression),
            "latex": expression_latex(expression),
            "length": len(expression),
            "blocks": check.blocks,
            "tokens": check.tokens,
        })
        if len(verified) >= options.solution_limit:
            break
    verified.sort(key=lambda item: (item["length"], item["expression"]))
    return verified


# ---------------------------------------------------------------- 对外求解
def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def solve(target_input: Fraction | int | str, options: SolveOptions | None = None) -> SolveResult:
    """求解单个目标。所有返回路径都不抛异常（参数错误放在 ``result.error``）。"""
    options = (options or SolveOptions()).normalized()
    started = time.perf_counter()
    if not normalize_pattern(options.pattern):
        options.pattern = "325"
    try:
        if isinstance(target_input, Fraction):
            target, target_text = target_input, _fraction_text(target_input)
        else:
            target, target_text = parse_target(target_input)
    except (SolveError, ValueError) as exc:
        return SolveResult(ok=False, error=str(exc), pattern=options.pattern, order_mode=options.order_mode)

    ctx = _Context(options)
    result = SolveResult(
        ok=False,
        target=target,
        target_text=target_text,
        pattern=options.pattern,
        order_mode=options.order_mode,
    )
    hit: dict[str, Any] | None = None

    for blocks in range(1, options.max_blocks + 1):
        if ctx.expired():
            break
        digits = options.pattern * blocks
        result.searched_blocks.append(blocks)
        for tokens in range(1, len(digits) + 1):
            if ctx.expired():
                break
            state = _ensure(digits, tokens, ctx)
            entry = state.map.get(target)
            if entry is not None:
                hit = {
                    "blocks": blocks,
                    "digits": digits,
                    "text": entry.text,
                    "tokens": entry.tokens,
                }
                break
            if ctx.aborted:
                break
        if hit is not None or ctx.aborted:
            break

    result.elapsed_ms = (time.perf_counter() - started) * 1000.0
    result.entries = sum(len(state.map) for state in ctx.cache.values())
    result.timeout = ctx.aborted

    if hit is None:
        if ctx.aborted:
            result.error = (
                t("calc325.solver.error.timeout", budget=options.time_budget_ms)
            )
            result.notes.append(t("calc325.solver.note.timeout"))
        else:
            result.error = (
                t("calc325.solver.error.no_solution", blocks=options.max_blocks, pattern=options.pattern, target=target_text)
            )
        return result

    expression = str(hit["text"])
    check = verify_expression(
        expression,
        target,
        pattern=options.pattern,
        order_mode=options.order_mode,
        fact_limit=max(5000, options.fact_max + 5),
    )
    result.digits = check.digits
    result.blocks = check.blocks
    result.tokens = check.tokens
    if check.ok:
        raw_writings = [expression]
        try:
            raw_writings.extend(
                _collect_writings(str(hit["digits"]), target, ctx, options.solution_limit * 3)
            )
        except Exception:  # noqa: BLE001 - 收集更多写法失败不影响主结果
            pass
        result.alternatives = _rank_alternatives(raw_writings, target, options, ctx)
        if not result.alternatives:
            result.alternatives = [{
                "expression": expression,
                "pretty": pretty_expression(expression),
                "latex": expression_latex(expression),
                "length": len(expression),
                "blocks": result.blocks,
                "tokens": result.tokens,
            }]
        primary = result.alternatives[0]
        result.ok = True
        result.expression = primary["expression"]
        result.pretty = primary["pretty"]
        result.latex = primary["latex"]
        result.verified = True
        result.elapsed_ms = (time.perf_counter() - started) * 1000.0
        if ctx.aborted:
            result.notes.append(t("calc325.solver.note.after_timeout"))
    else:
        result.error = t("calc325.solver.error.internal_verify", error=check.error or t("calc325.solver.error.verify_default"))
        result.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return result


def solve_all(target_input: Fraction | int | str, options: SolveOptions | None = None) -> list[SolveResult]:
    """「我全都要」：依次尝试内置数字串，返回第一个成功的结果（失败时返回全部失败）。"""
    options = (options or SolveOptions()).normalized()
    results: list[SolveResult] = []
    remaining = max(1, options.time_budget_ms)
    for index, pattern in enumerate(BUILTIN_PATTERNS):
        per_pattern = max(800, remaining // (len(BUILTIN_PATTERNS) - index))
        attempt = SolveOptions(**{**options.__dict__, "pattern": pattern, "time_budget_ms": per_pattern})
        result = solve(target_input, attempt)
        results.append(result)
        remaining = max(0, remaining - int(result.elapsed_ms))
        if result.ok:
            return results
    return results
