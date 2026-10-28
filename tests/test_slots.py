"""时段冲突判定单元测试。"""

from __future__ import annotations

from src.domain import slots as slots_mod

OCCUPIED = [
    {"id": "S1", "material_id": "M02", "date": "2026-11-02", "start": "18:00", "duration_sec": 30},
    {"id": "S2", "material_id": "M02", "date": "2026-11-02", "start": "12:00", "duration_sec": 30},
]
WINDOW = {"start": "08:00", "end": "22:00"}


def test_same_hour_is_conflict() -> None:
    """同一自然小时内已有投放即冲突。"""
    conflicts = slots_mod.find_conflicts("2026-11-02", "18:30", 0, OCCUPIED, "hour")
    assert len(conflicts) == 1
    assert conflicts[0]["id"] == "S1"


def test_different_hour_no_conflict() -> None:
    assert slots_mod.find_conflicts("2026-11-02", "19:00", 0, OCCUPIED, "hour") == []


def test_other_date_is_ignored() -> None:
    assert slots_mod.find_conflicts("2026-11-03", "18:00", 0, OCCUPIED, "hour") == []


def test_exact_granularity_allows_adjacent_slot() -> None:
    """exact 粒度下 18:30 与 18:00-18:30 不重叠，不算冲突。"""
    conflicts = slots_mod.find_conflicts("2026-11-02", "18:30", 30, OCCUPIED, "exact")
    assert conflicts == []


def test_exact_granularity_detects_overlap() -> None:
    """exact 粒度下真实时间区间重叠才冲突（占用 30 分钟，目标落在其中）。"""
    occupied = [{"id": "S1", "material_id": "M02", "date": "2026-11-02",
                 "start": "18:00", "duration_sec": 1800}]
    conflicts = slots_mod.find_conflicts("2026-11-02", "18:15", 30, occupied, "exact")
    assert len(conflicts) == 1
    assert conflicts[0]["id"] == "S1"


def test_exact_granularity_ignores_non_overlapping_short_slot() -> None:
    """占用 18:00 起 30 秒，18:30 起始的目标不与其重叠。"""
    occupied = [{"id": "S1", "material_id": "M02", "date": "2026-11-02",
                 "start": "18:00", "duration_sec": 30}]
    assert slots_mod.find_conflicts("2026-11-02", "18:30", 30, occupied, "exact") == []


def test_hour_granularity_conflicts_even_without_overlap() -> None:
    """hour 粒度下 18:00 起 30 秒的占用仍会与 18:30 冲突（同一自然小时）。"""
    occupied = [{"id": "S1", "material_id": "M02", "date": "2026-11-02",
                 "start": "18:00", "duration_sec": 30}]
    assert len(slots_mod.find_conflicts("2026-11-02", "18:30", 30, occupied, "hour")) == 1


def test_candidate_slots_excludes_occupied_hours() -> None:
    result = slots_mod.candidate_slots("2026-11-02", OCCUPIED, WINDOW, "hour")
    assert "12:00" not in result
    assert "18:00" not in result
    assert "19:00" in result
    assert result == sorted(result)


def test_candidate_slots_respects_publish_window() -> None:
    result = slots_mod.candidate_slots("2026-11-02", OCCUPIED, WINDOW, "hour")
    assert result[0] == "08:00"
    assert result[-1] <= "21:30"


def test_build_slot_view_reports_occupied_and_available() -> None:
    view = slots_mod.build_slot_view("2026-11-02", 60, OCCUPIED, WINDOW, "hour")
    assert len(view["occupied"]) == 2
    assert view["occupied_hours"] == ["12:00", "18:00"]
    assert "19:00" in view["available_slots"]


def test_build_slot_view_empty_day() -> None:
    view = slots_mod.build_slot_view("2026-11-05", 60, OCCUPIED, WINDOW, "hour")
    assert view["occupied"] == []
    assert view["occupied_hours"] == []
