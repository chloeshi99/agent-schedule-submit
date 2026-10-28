"""用户请求解析（确定性字段抽取）。

只抽取**用户明确给出的字面量**（日期、时刻、素材 ID、确认意图），
不涉及任何审核/排期决策——决策仍由模型（LLMClient）产生。

把请求解析独立出来的好处：
- CLI 与 Agent 循环共用同一套解析结果，避免两处正则漂移；
- 解析失败是可观测的（返回 None），而不是静默猜一个值。
"""

from __future__ import annotations

import re

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_TIME_RE = re.compile(r"\b([01]\d|2[0-3]):([0-5]\d)\b")
_MATERIAL_RE = re.compile(r"\b(M\d{2,})\b")

# 明确的确认意图表述（用于 CLI 的 --confirm 兜底判断）
_CONFIRM_WORDS = ("确认", "已确认", "确定用", "就按", "用这个时间", "我确认")
# 明确表示「先看看 / 未确认」的表述
_UNCONFIRMED_WORDS = ("先看看", "能不能排", "建议", "待确认", "尚未确认", "别存", "先别存")


def parse_date(text: str) -> str | None:
    """从请求文本中抽取日期（YYYY-MM-DD）。"""
    m = _DATE_RE.search(text or "")
    return m.group(1) if m else None


def parse_time(text: str) -> str | None:
    """从请求文本中抽取时刻（HH:MM）。"""
    m = _TIME_RE.search(text or "")
    return f"{m.group(1)}:{m.group(2)}" if m else None


def parse_material_id(text: str) -> str | None:
    """从请求文本中抽取素材 ID（如 M01）。"""
    m = _MATERIAL_RE.search(text or "")
    return m.group(1) if m else None


def infer_confirmed(text: str) -> bool | None:
    """推断用户是否已确认。无法判断时返回 None，交由显式参数决定。"""
    t = text or ""
    if any(w in t for w in _UNCONFIRMED_WORDS):
        return False
    if any(w in t for w in _CONFIRM_WORDS):
        return True
    return None


def parse_request(text: str) -> dict[str, str | bool | None]:
    """一次性抽取请求中的确定性字段。"""
    return {
        "material_id": parse_material_id(text),
        "date": parse_date(text),
        "start": parse_time(text),
        "confirmed": infer_confirmed(text),
    }
