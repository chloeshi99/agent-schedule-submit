"""时段占用与冲突判定。

时段粒度由 data/slots.json 的 `conflict_granularity` 决定：
- "hour"：同一自然小时内已有投放即冲突（避免碎片化挤占）
- "exact"：仅当占用时段与目标时段重叠才冲突
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

SLOT_STEP_MIN = 30


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%H:%M")


def fmt_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def slot_end(start: str, duration_sec: int) -> datetime:
    return parse_time(start) + timedelta(seconds=max(0, duration_sec))


def _overlap(a_start: str, a_end: datetime, b_start: str, b_end: datetime) -> bool:
    return parse_time(a_start) < b_end and a_end > parse_time(b_start)


def find_conflicts(
    date: str,
    start: str,
    duration_sec: int,
    occupied: list[dict],
    granularity: str = "hour",
) -> list[dict]:
    """返回与目标时段冲突的占用列表。"""
    end = slot_end(start, duration_sec)
    conflicts: list[dict] = []
    for item in occupied:
        if item.get("date") != date:
            continue
        item_end = slot_end(item["start"], item.get("duration_sec", 0))
        if granularity == "hour":
            same_hour = parse_time(start).hour == parse_time(item["start"]).hour
            if same_hour or _overlap(start, end, item["start"], item_end):
                conflicts.append(item)
        else:
            if _overlap(start, end, item["start"], item_end):
                conflicts.append(item)
    return conflicts


def candidate_slots(date: str, occupied: list[dict], window: dict, granularity: str = "hour") -> list[str]:
    """生成当天在发布窗口内、且不与已有占用冲突的候选时段（升序）。"""
    start = parse_time(window["start"])
    end = parse_time(window["end"])
    step = timedelta(minutes=SLOT_STEP_MIN)
    result: list[str] = []
    cur = start
    while cur + step <= end:
        candidate = fmt_time(cur)
        if not find_conflicts(date, candidate, 0, occupied, granularity):
            result.append(candidate)
        cur += step
    return result


def build_slot_view(
    date: str,
    duration_sec: int,
    occupied: list[dict],
    window: dict,
    granularity: str = "hour",
) -> dict[str, Any]:
    """构造「时段查询」工具的返回结构。"""
    day_occupied = [o for o in occupied if o.get("date") == date]
    conflicts = find_conflicts(date, "00:00", 0, occupied, granularity)  # 仅用于结构占位
    del conflicts
    available = candidate_slots(date, occupied, window, granularity)
    return {
        "date": date,
        "granularity": granularity,
        "publish_window": window,
        "occupied": day_occupied,
        "available_slots": available,
        "occupied_hours": sorted({parse_time(o["start"]).strftime("%H:00") for o in day_occupied}),
    }
