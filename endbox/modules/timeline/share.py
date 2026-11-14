# -*- coding: utf-8 -*-
"""分享码：``EB2:`` + base64url(JSON) 的排轴快照（含轴名 / 编队 / 事件 / 反复）。

只依赖标准库，不联网。导入时容错：
- 允许整段聊天记录里夹着码（自动截取 ``EB2:`` 后面那串 base64）；
- 允许直接粘贴 JSON（以 ``{`` 开头）；
- 失败抛 ``ShareCodeError``，message 是人话，直接显示给用户。

版本：v3（真实键位模型：战技=数字短按 / 终结=长按数字 / 连携=E / 切换干员=F1-F4）。
v1 / v2 旧分享码仍可导入：旧时间轴 ``events`` 按开始时间迁移成有序步骤（时间/覆盖丢弃），
步骤本身（战技/连携/终结/重击/注释）不丢；返回值 ``migrated=True`` 供界面提示。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from endbox.core.text import t as tr

SHARE_PREFIX = "EB2:"
SHARE_FORMAT = "EndBoxTimeline"
SHARE_VERSION = 3
ACCEPTED_VERSIONS = (1, 2, SHARE_VERSION)
MAX_CODE_LENGTH = 200_000
MAX_TEXT_LENGTH = 4096

_CODE_RE = re.compile(r"EB2:([A-Za-z0-9_\-=]{16,})", re.IGNORECASE)


class ShareCodeError(ValueError):
    """分享码无法解析（message 直接给用户看）。"""


def share_blurb(team_names, code: str) -> str:
    """分享时可以直接粘贴的一整段话：干员列表 + 分享码。"""
    names = [str(name).strip() for name in (team_names or ()) if str(name).strip()]
    team_text = " / ".join(names) if names else tr("timeline.share.blurb_no_team")
    return tr("timeline.share.blurb_template", team=team_text, code=str(code or "").strip())


def encode(payload: dict) -> str:
    """把 payload（轴名/队伍/model）编码成分享码。"""
    if not isinstance(payload, dict):
        raise ShareCodeError(tr("timeline.share.errors.empty_payload"))
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return SHARE_PREFIX + body


def extract_code(text: str) -> str:
    """从一整段文字里挑出分享码；找不到就原样返回（交给 decode 判断）。"""
    raw = (text or "").strip()
    if not raw:
        raise ShareCodeError(tr("timeline.share.errors.empty_code"))
    match = _CODE_RE.search(raw)
    if match:
        return SHARE_PREFIX + match.group(1)
    if raw.startswith("{"):
        return raw
    # 聊天软件会折行：去掉所有空白再找一次
    flattened = re.sub(r"\s+", "", raw)
    match = _CODE_RE.search(flattened)
    if match:
        return SHARE_PREFIX + match.group(1)
    if flattened.startswith("{"):
        return flattened
    raise ShareCodeError(tr("timeline.share.errors.no_code"))


def _decode_body(code: str) -> dict:
    text = code.strip()
    if len(text) > MAX_CODE_LENGTH:
        raise ShareCodeError(tr("timeline.share.errors.too_long"))
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ShareCodeError(
                tr("timeline.share.errors.bad_json", message=exc.msg, line=exc.lineno)
            ) from exc
    else:
        if text.upper().startswith(SHARE_PREFIX):
            text = text[len(SHARE_PREFIX):]
        body = re.sub(r"\s+", "", text)
        if not body:
            raise ShareCodeError(tr("timeline.share.errors.empty_code"))
        padding = "=" * (-len(body) % 4)
        try:
            raw = base64.urlsafe_b64decode(body + padding)
        except (binascii.Error, ValueError) as exc:
            raise ShareCodeError(tr("timeline.share.errors.corrupt")) from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ShareCodeError(tr("timeline.share.errors.unknown_content")) from exc
    if not isinstance(data, dict):
        raise ShareCodeError(tr("timeline.share.errors.bad_format"))
    if data.get("format") != SHARE_FORMAT:
        raise ShareCodeError(tr("timeline.share.errors.not_endbox"))
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    if version not in ACCEPTED_VERSIONS:
        raise ShareCodeError(tr("timeline.share.errors.version", version=version))
    return data


def _migrate_model(model: dict) -> tuple[dict, bool]:
    """旧版 model（v1/v2 时间轴 events）→ v3 steps；返回 ``(model, migrated)``。

    旧分享码里的步骤语义与新版一致（战技/连携/终结/重击），只是没有 steps 字段；
    迁移用 ``TimelineModel.from_legacy``（按 start 排序，时间/覆盖丢弃），步骤不丢。
    """
    if not isinstance(model, dict):
        return {}, False
    if isinstance(model.get("steps"), list):
        return model, False
    if isinstance(model.get("events"), list):
        try:
            from endbox.modules.timeline.model import TimelineModel

            return TimelineModel.from_legacy(model).to_dict(), True
        except Exception:  # noqa: BLE001 - 迁移失败保留原样，交给上层校验
            return model, False
    return model, False


def decode(text: str) -> dict:
    """解析分享码，返回规范化后的 ``{name, team, model, migrated, version}``。"""
    data = _decode_body(extract_code(text))
    team = data.get("team")
    if not isinstance(team, list):
        team = []
    model = data.get("model")
    if not isinstance(model, dict):
        raise ShareCodeError(tr("timeline.share.errors.no_model"))
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    model, model_migrated = _migrate_model(model)
    name = str(data.get("name") or "").strip()[:60]
    return {
        "name": name,
        "team": [str(n or "") for n in team][:4],
        "model": model,
        "migrated": bool(model_migrated or version < SHARE_VERSION),
        "version": version,
    }


def make_payload(name: str, team: list[str], model: dict) -> dict:
    return {
        "format": SHARE_FORMAT,
        "version": SHARE_VERSION,
        "name": str(name or "")[:60],
        "team": [str(n or "") for n in (team or [])][:4],
        "model": model,
    }
