# -*- coding: utf-8 -*-
"""输入 token 规范：常量表 + 归一化。

token 是排轴运行态与输入服务之间唯一的契约字符串。默认真实键位
（对齐游戏内设置，来源 MaaEnd ``assets/tasks/setting/Keymap.json``）：

    1 / 2 / 3 / 4              数字单按（战技，对应 4 个干员槽位）
    HOLD+1 ... HOLD+4          数字长按（≥ hold_ms，默认 280ms，终结技）
    E                          连携（全队共用；旧写法 E+1~4 保留解析）
    F1 / F2 / F3 / F4          切换干员（不是数字键）
    F6 / F7 / F8 / F9 / F10 / F11   运行控制 / 功能键
    Q+1~4 / E+1~4 / ALT+1~4    旧版组合键（仅保留解析/展示兼容，运行态已不再接受）
    H                          旧版重击键（仅兼容；真实重击 = 普攻末段/长按左键，没有独立键盘键）
    PAD_A/B/X/Y                手柄面键
    PAD_LB / PAD_RB            手柄肩键
    PAD_MOD+PAD_A/B/X/Y        LB 按住 + 面键（组合键）

旧 token（Q+n / E+n / ALT+n / H）继续出现在 ``TOKENS`` 里，保证旧设置文件、
旧插件与旧测试不炸；新代码请优先用数字短按 / HOLD+n / E / F1-F4。
"""
from __future__ import annotations

import re

DIGITS: tuple[str, ...] = ("1", "2", "3", "4")
MODIFIERS: tuple[str, ...] = ("ALT", "Q", "E")
MODIFIER_PRIORITY: tuple[str, ...] = ("ALT", "Q", "E")
SWITCH_TOKENS: tuple[str, ...] = ("F1", "F2", "F3", "F4")
FUNCTION_TOKENS: tuple[str, ...] = (
    ("H",) + tuple(f"F{index}" for index in range(1, 25))
)

DIGIT_TOKENS: tuple[str, ...] = DIGITS
PLAIN_MODIFIER_TOKENS: tuple[str, ...] = tuple(
    f"{modifier}+{digit}" for modifier in MODIFIER_PRIORITY for digit in DIGITS
)
HOLD_TOKENS: tuple[str, ...] = tuple(f"HOLD+{digit}" for digit in DIGITS)
PAD_FACE_TOKENS: tuple[str, ...] = ("PAD_A", "PAD_B", "PAD_X", "PAD_Y")
PAD_SHOULDER_TOKENS: tuple[str, ...] = ("PAD_LB", "PAD_RB")
PAD_MOD_TOKENS: tuple[str, ...] = tuple(f"PAD_MOD+{face}" for face in PAD_FACE_TOKENS)
PAD_TOKENS: tuple[str, ...] = PAD_FACE_TOKENS + PAD_SHOULDER_TOKENS + PAD_MOD_TOKENS

KEYBOARD_TOKENS: tuple[str, ...] = (
    DIGIT_TOKENS + PLAIN_MODIFIER_TOKENS + HOLD_TOKENS + FUNCTION_TOKENS
)
TOKENS: tuple[str, ...] = KEYBOARD_TOKENS + PAD_TOKENS

TOKEN_SET = frozenset(TOKENS)
PAD_TOKEN_SET = frozenset(PAD_TOKENS)

_MODIFIER_RE = re.compile(r"^(Q|E|ALT)\+?([1-4])$")
_HOLD_RE = re.compile(r"^HOLD\+?([1-4])$")
_FUNCTION_RE = re.compile(r"^F(?:[1-9]|1[0-9]|2[0-4])$")
_PAD_RE = re.compile(r"^PAD_?(A|B|X|Y|LB|RB)$")
_PAD_MOD_RE = re.compile(r"^PAD_?MOD\+?(?:PAD_?)?(A|B|X|Y)$")


def digit_token(digit: int | str, modifier: str = "") -> str:
    """构造数字 token；``modifier`` 只接受 ALT/Q/E（大小写不敏感）。"""
    value = str(digit).strip()
    if value not in DIGITS:
        raise ValueError(f"digit 必须是 1-4，收到 {digit!r}")
    mod = str(modifier or "").strip().upper()
    if not mod:
        return value
    if mod not in MODIFIERS:
        raise ValueError(f"modifier 必须是 {'/'.join(MODIFIERS)}，收到 {modifier!r}")
    return f"{mod}+{value}"


def hold_token(digit: int | str) -> str:
    value = str(digit).strip()
    if value not in DIGITS:
        raise ValueError(f"digit 必须是 1-4，收到 {digit!r}")
    return f"HOLD+{value}"


def modifier_for(digit_token_value: str) -> str:
    """从 ``Q+2`` 取修饰键；普通 token 返回空字符串。"""
    token = normalize_token(digit_token_value)
    for modifier in MODIFIER_PRIORITY:
        if token.startswith(modifier + "+"):
            return modifier
    return ""


def digit_of(token_value: str) -> str:
    """取 token 里的数字槽位（``Q+2`` / ``HOLD+2`` / ``2`` → ``"2"``）。"""
    token = normalize_token(token_value)
    if token in DIGITS:
        return token
    if token.startswith("HOLD+"):
        return token[-1]
    for modifier in MODIFIER_PRIORITY:
        if token.startswith(modifier + "+"):
            return token[-1]
    return ""


def normalize_token(value: object) -> str:
    """把用户配置 / 旧写法归一化成规范 token；无法识别返回空字符串。

    兼容：``q1`` / ``q+1`` / ``Q 1`` → ``Q+1``；``alt3`` → ``ALT+3``；
    ``hold1`` / ``hold+1`` → ``HOLD+1``；``f1`` / ``F1`` → ``F1``（切换干员）；
    ``pad-a`` → ``PAD_A``；``padmod+a`` → ``PAD_MOD+PAD_A``。
    """
    text = str(value or "").strip().upper().replace(" ", "").replace("-", "_")
    if not text:
        return ""
    if text in TOKEN_SET:
        return text
    match = _MODIFIER_RE.fullmatch(text)
    if match:
        return f"{match.group(1)}+{match.group(2)}"
    match = _HOLD_RE.fullmatch(text)
    if match:
        return f"HOLD+{match.group(1)}"
    if text in DIGITS:
        return text
    if text == "H" or _FUNCTION_RE.fullmatch(text):
        return text
    match = _PAD_RE.fullmatch(text)
    if match:
        return f"PAD_{match.group(1)}"
    match = _PAD_MOD_RE.fullmatch(text)
    if match:
        return f"PAD_MOD+PAD_{match.group(1)}"
    return ""


def is_known_token(value: object) -> bool:
    return normalize_token(value) in TOKEN_SET
