# -*- coding: utf-8 -*-
"""325 随机模式：等级、固定长度种子、Zc 编码。

参考 Calculate325-main（random-design.md / SeedDialog.cs）：
  · L1–L5，种子十进制长度固定为 12 / 20 / 24 / 32 / 64 位；
  · 十进制与 Zc 只是同一种子的两种写法：Zc 先把十进制整数转成二进制，
    再按位置交替写 Z、C（大写 = 1，小写 = 0）；
  · 显示时每 4 位一组、每 32 位换行。

旧版 `calc325.complexity` 1–4 的设置仍被读取：1→L1 … 4→L4。
"""
from __future__ import annotations

import hashlib
import random
import re
from datetime import date

from endbox.core.text import t

LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)
DEFAULT_LEVEL = 2
SEED_TYPES: tuple[str, ...] = ("number", "zc")
SEED_TYPE_LABELS = {
    "number": t("calc325.seed.type.number"),
    "zc": t("calc325.seed.type.zc"),
}

LEVEL_NAMES = {
    1: t("calc325.level.1.name"),
    2: t("calc325.level.2.name"),
    3: t("calc325.level.3.name"),
    4: t("calc325.level.4.name"),
    5: t("calc325.level.5.name"),
}
LEVEL_SHORT = {1: "L1", 2: "L2", 3: "L3", 4: "L4", 5: "L5"}
LEVEL_SEED_LENGTHS = {1: 12, 2: 20, 3: 24, 4: 32, 5: 64}
LEVEL_DEPTH_RANGES = {1: (0, 0), 2: (1, 1), 3: (4, 4), 4: (5, 9), 5: (10, 12)}
LEVEL_DESCRIPTIONS = {
    1: t("calc325.level.1.desc"),
    2: t("calc325.level.2.desc"),
    3: t("calc325.level.3.desc"),
    4: t("calc325.level.4.desc"),
    5: t("calc325.level.5.desc"),
}

_ZC_MAP = {"z": "0", "c": "0", "Z": "1", "C": "1"}
_ZC_RE = re.compile(r"^[ZzCc]+$")


def normalize_level(level: int | str | None) -> int:
    try:
        value = int(level)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = DEFAULT_LEVEL
    return max(1, min(5, value))


def level_from_complexity(complexity: int | str | None) -> int:
    """旧设置 calc325.complexity（1–4）→ 等级；非法值回到默认 L2。"""
    try:
        value = int(complexity)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_LEVEL
    return max(1, min(4, value))


def complexity_for_level(level: int) -> int:
    """写回旧键 calc325.complexity 时使用：L5 记为 4。"""
    return max(1, min(4, normalize_level(level)))


def resolve_level(level: int | str | None = None, complexity: int | str | None = None) -> int:
    """优先新设置 calc325.level，其次旧设置 calc325.complexity，最后默认 L2。"""
    if level is not None:
        try:
            return normalize_level(int(level))
        except (TypeError, ValueError):
            pass
    if complexity is not None:
        return level_from_complexity(complexity)
    return DEFAULT_LEVEL


def seed_length(level: int) -> int:
    return LEVEL_SEED_LENGTHS[normalize_level(level)]


def level_name(level: int) -> str:
    return LEVEL_NAMES[normalize_level(level)]


def depth_range(level: int) -> tuple[int, int]:
    return LEVEL_DEPTH_RANGES[normalize_level(level)]


# ---------------------------------------------------------------- 分组显示
def clean_seed(text: str, seed_type: str = "number") -> str:
    if seed_type == "zc":
        return "".join(ch for ch in str(text or "") if ch in _ZC_MAP)
    return "".join(ch for ch in str(text or "") if ch.isdigit())


def group_seed(text: str, *, group: int = 4, per_line: int = 32) -> str:
    """每 4 位一组，每 per_line 位换一行；用于预览与复制。"""
    body = str(text or "")
    if not body:
        return ""
    lines: list[str] = []
    for start in range(0, len(body), per_line):
        chunk = body[start:start + per_line]
        lines.append(" ".join(chunk[i:i + group] for i in range(0, len(chunk), group)))
    return "\n".join(lines)


# ---------------------------------------------------------------- Zc 编解码
def zc_bits(seed_decimal: str) -> str:
    value = int(str(seed_decimal or "0"))
    if value <= 0:
        return "0"
    return bin(value)[2:]


def zc_encode(seed_decimal: str) -> str:
    """十进制种子 → Zc 字母串（位置 0 用 Z、位置 1 用 C，大写=1 小写=0）。"""
    bits = zc_bits(seed_decimal)
    out: list[str] = []
    for index, bit in enumerate(bits):
        letter = "Z" if index % 2 == 0 else "C"
        out.append(letter if bit == "1" else letter.lower())
    return "".join(out)


def zc_decode_int(text: str) -> int:
    """Zc 字母串 → 十进制整数；非法（含非 Z/C 字符或大小写位置不对）抛 ValueError。"""
    body = clean_seed(text, "zc")
    original = "".join(ch for ch in str(text or "") if not ch.isspace())
    if not body:
        raise ValueError(t("calc325.levels.error.zc_empty"))
    if len(original) != len(body):
        raise ValueError(t("calc325.levels.error.zc_chars"))
    if not _ZC_RE.match(body):
        raise ValueError(t("calc325.levels.error.zc_chars_only"))
    bits: list[str] = []
    for index, ch in enumerate(body):
        want = "Z" if index % 2 == 0 else "C"
        if ch.upper() != want:
            raise ValueError(
                t("calc325.levels.error.zc_alternate", index=index + 1, want=want, lower=want.lower(), char=ch)
            )
        bits.append("1" if ch.isupper() else "0")
    return int("".join(bits), 2)


# ---------------------------------------------------------------- 种子解析
def parse_seed_input(text: str, seed_type: str = "number", level: int = DEFAULT_LEVEL) -> str:
    """把输入框内容校验并规范化为「固定长度的十进制种子字符串」。

    非法输入抛 ValueError，消息直接展示给用户。
    """
    level = normalize_level(level)
    need = seed_length(level)
    seed_type = "zc" if seed_type == "zc" else "number"
    raw = str(text or "").strip()
    if not raw:
        raise ValueError(t("calc325.levels.error.need_seed", need=need))

    if seed_type == "zc":
        try:
            value = zc_decode_int(raw)
        except ValueError:
            raise
        if value >= 10 ** need:
            raise ValueError(
                t("calc325.levels.error.zc_range", level=level, need=need)
            )
        return str(value).zfill(need)

    digits = clean_seed(raw, "number")
    if not digits:
        raise ValueError(t("calc325.levels.error.need_length", level=level, need=need))
    if len(digits) != need:
        raise ValueError(t("calc325.levels.error.need_length_current", level=level, need=need, current=len(digits)))
    return digits


def seed_value(seed_decimal: str) -> int:
    return int(str(seed_decimal or "0"))


def seed_display(seed_decimal: str, seed_type: str = "number") -> str:
    """输入框 / 复制用的种子文本（未分组）。"""
    if seed_type == "zc":
        return zc_encode(seed_decimal)
    return str(seed_decimal)


def seed_preview(seed_decimal: str, seed_type: str = "number") -> str:
    """预览用的分组文本：每 4 位一组、每 32 位换行。"""
    return group_seed(seed_display(seed_decimal, seed_type))


def validate_seed_text(text: str, seed_type: str, level: int) -> tuple[bool, str]:
    """返回 (是否合法, 错误消息)；供输入框实时预览使用。"""
    try:
        parse_seed_input(text, seed_type, level)
    except ValueError as exc:
        return False, str(exc)
    return True, ""


# ---------------------------------------------------------------- 随机 / 默认
def make_seed(level: int, rng: random.Random | None = None) -> str:
    """按等级生成固定长度的十进制随机种子（首位 1–9）。"""
    need = seed_length(level)
    source = rng or random.Random()
    digits = [str(source.randint(1, 9))] + [str(source.randint(0, 9)) for _ in range(need - 1)]
    return "".join(digits)


def default_seed(level: int) -> str:
    """确定性默认种子：日期打头、长度符合当前等级。"""
    need = seed_length(level)
    base = date.today().strftime("%Y%m%d")
    return (base + "0" * need)[:need]


def rng_for(seed_decimal: str, level: int, *, salt: str = "") -> random.Random:
    """同一个种子 + 等级永远得到同一序列。"""
    payload = f"EndBox325:{normalize_level(level)}:{seed_decimal}:{salt}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return random.Random(int.from_bytes(digest[:16], "big"))


def seed_bits_for_display(seed_decimal: str, seed_type: str = "number") -> str:
    return zc_bits(seed_decimal) if seed_type == "zc" else ""
