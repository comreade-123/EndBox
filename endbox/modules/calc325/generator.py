# -*- coding: utf-8 -*-
"""325 计算器：种子 → 复杂算式 → 结果恒为 325。

梗来自 Zc 的"魔法目录"：输入任意种子，生成一条看起来毫无规律、
但每一步都成立的长算式，最后一定收敛到 325。同一个种子 + 同一组选项永远得到同一条式子。

种子类型：
  number  纯数字（0-9）
  zc      Zc 模式：用 z/Z/c/C 的大小写当二进制位，小写 = 0、大写 = 1（"ZcZ" → 101 = 5）

复杂度：
  1 简单 / 2 标准 / 3 复杂 / 4 极复杂 —— 控制项数、嵌套深度与运算符种类。
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

SEED_TYPES = ("number", "zc")
SEED_TYPE_LABELS = {"number": "纯数字", "zc": "Zc 模式"}
COMPLEXITY_LABELS = {1: "简单", 2: "标准", 3: "复杂", 4: "极复杂"}
COMPLEXITY_CONFIG = {
    1: {"terms": (3, 4), "depth": 1, "ops": ("add", "sub")},
    2: {"terms": (4, 6), "depth": 2, "ops": ("add", "sub", "mul", "div")},
    3: {"terms": (5, 8), "depth": 3, "ops": ("add", "sub", "mul", "div")},
    4: {"terms": (7, 10), "depth": 4, "ops": ("add", "sub", "mul", "div")},
}

ZC_MAP = {"z": "0", "c": "0", "Z": "1", "C": "1"}


@dataclass(frozen=True)
class Term:
    """算式的项：文字形式 + 真实数值（内部校验用）。"""

    text: str
    value: int


@dataclass
class Puzzle:
    seed: str
    seed_type: str = "number"
    complexity: int = 2
    raw_value: int = 0
    terms: list[Term] = field(default_factory=list)

    @property
    def result(self) -> int:
        return 325

    @property
    def expression(self) -> str:
        return " + ".join(f"({t.text})" if " " in t.text else t.text for t in self.terms)

    def expression_lines(self) -> list[str]:
        """按项换行，极复杂模式也读得下去。"""
        lines: list[str] = []
        for index, term in enumerate(self.terms):
            prefix = "  " if index == 0 else "+ "
            lines.append(prefix + (f"({term.text})" if " " in term.text else term.text))
        return lines

    def steps(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for index, term in enumerate(self.terms, start=1):
            rows.append((f"{index:02d}", f"{term.text} = {term.value}"))
        total = " + ".join(str(t.value) for t in self.terms)
        rows.append(("Σ", f"{total} = 325"))
        return rows


# ---------------------------------------------------------------- 种子解析
def parse_seed(seed: str, seed_type: str = "number") -> int:
    """把种子转成随机数种子。非法输入抛 ValueError（界面负责显示原因）。"""
    seed = (seed or "").strip()
    if not seed:
        raise ValueError("种子不能为空")
    if seed_type == "zc":
        chars = [ch for ch in seed if not ch.isspace()]
        invalid = [ch for ch in chars if ch not in ZC_MAP]
        if invalid:
            raise ValueError(f"Zc 模式只能输入 z / Z / c / C，发现：{''.join(invalid[:4])}")
        if len(chars) > 32:
            raise ValueError("Zc 种子最多 32 位")
        bits = "".join(ZC_MAP[ch] for ch in chars)
        return int(bits, 2) if bits else 0
    digits = [ch for ch in seed if not ch.isspace()]
    if not all(ch.isdigit() for ch in digits):
        raise ValueError("纯数字模式只能输入 0-9")
    if len(digits) > 24:
        raise ValueError("纯数字种子最多 24 位")
    return int("".join(digits))


def seed_bits(seed: str, seed_type: str = "number") -> str:
    """返回用于展示的种子位串（Zc 模式）或空串。"""
    if seed_type != "zc":
        return ""
    chars = [ch for ch in (seed or "") if not ch.isspace()]
    return "".join(ZC_MAP.get(ch, "?") for ch in chars)


def seed_int(seed: str, seed_type: str = "number") -> int:
    value = parse_seed(seed, seed_type)
    digest = hashlib.sha256(f"{seed_type}:{value}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


# ---------------------------------------------------------------- 生成算式
def _factor_pair(value: int, rng: random.Random) -> tuple[int, int] | None:
    pairs = [(a, value // a) for a in range(2, int(value ** 0.5) + 1) if value % a == 0]
    if not pairs:
        return None
    return rng.choice(pairs)


def _wrap(text: str) -> str:
    """子式是复合式时加括号，保证打印出来的算式与真实运算顺序一致。"""
    return f"({text})" if " " in text else text


def _decorate(value: int, rng: random.Random, depth: int, ops: tuple[str, ...]) -> Term:
    """把一个数写成算式。value 必须等于结果的数值。"""
    if depth <= 0 or value <= 6:
        return Term(str(value), value)
    options = [op for op in ops if op != "mul" or _factor_pair(value, random.Random(7)) is not None]
    if not options:
        options = ["add", "sub"]
    op = rng.choice(options)

    if op == "add":
        left = rng.randint(2, max(2, value - 2))
        right = value - left
        a = _decorate(left, rng, depth - 1, ops)
        b = _decorate(right, rng, depth - 1, ops)
        return Term(f"{_wrap(a.text)} + {_wrap(b.text)}", value)
    if op == "sub":
        extra = rng.randint(3, 23)
        a = _decorate(value + extra, rng, depth - 1, ops)
        b = _decorate(extra, rng, depth - 1, ops)
        return Term(f"{_wrap(a.text)} − {_wrap(b.text)}", value)
    if op == "mul":
        pair = _factor_pair(value, random.Random(11))
        if pair is None:
            return Term(str(value), value)
        a = _decorate(pair[0], rng, depth - 1, ops)
        b = _decorate(pair[1], rng, depth - 1, ops)
        return Term(f"{_wrap(a.text)} × {_wrap(b.text)}", value)
    # div
    divisor = rng.randint(2, 7)
    a = _decorate(value * divisor, rng, depth - 1, ops)
    return Term(f"{_wrap(a.text)} ÷ {divisor}", value)


def _partition(total: int, parts: int, rng: random.Random) -> list[int]:
    """把 total 随机切成 parts 段（每段 ≥ 4）。"""
    if parts <= 1:
        return [total]
    slots = max(1, min(parts - 1, total - 8))
    points = sorted(rng.sample(range(4, total - 4), slots))
    chunks = []
    prev = 0
    for point in points:
        chunks.append(point - prev)
        prev = point
    chunks.append(total - prev)
    return chunks


def make_puzzle(seed: str, complexity: int = 2, seed_type: str = "number") -> Puzzle:
    """确定性生成：同一个种子 + 复杂度 + 种子类型 → 同一条算式 → 325。"""
    complexity = max(1, min(4, int(complexity)))
    config = COMPLEXITY_CONFIG[complexity]
    rng = random.Random(seed_int(seed, seed_type))
    count = rng.randint(*config["terms"])
    values = _partition(325, count, rng)
    ops = config["ops"]
    terms = [_decorate(value, rng, config["depth"], ops) for value in values]
    puzzle = Puzzle(
        seed=(seed or "").strip() or "ENDBOX",
        seed_type=seed_type,
        complexity=complexity,
        raw_value=parse_seed(seed, seed_type) if (seed or "").strip() else 0,
        terms=terms,
    )
    assert sum(t.value for t in puzzle.terms) == 325, "325 计算器内部错误：结果不是 325"
    return puzzle


def random_seed(seed_type: str = "number", rng: random.Random | None = None) -> str:
    """生成一个符合当前类型的随机种子。"""
    moment = __import__("datetime").datetime.now()
    if seed_type == "zc":
        source = rng or random.Random(moment.timestamp())
        alphabet = "zZcC"
        return "".join(source.choice(alphabet) for _ in range(16))
    source = rng or random.Random(moment.timestamp())
    return f"{moment.strftime('%Y%m%d%H%M%S')}{source.randint(10, 99)}"


def default_seed(seed_type: str = "number") -> str:
    from datetime import date

    if seed_type == "zc":
        return "ZcZc"
    return date.today().isoformat().replace("-", "")
