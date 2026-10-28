"""工具层单元测试：补查回写、故障暴露、幂等。"""

from __future__ import annotations

import pytest

from src.storage.db import DraftStore
from src.tools.impl import ToolContext, ToolError, ToolRegistry


def _registry(tmp_path, materials, rules, slots, failures=None) -> ToolRegistry:
    store = DraftStore(tmp_path / "d.sqlite3")
    ctx = ToolContext(
        materials=materials["materials"],
        rules_cfg=rules,
        slots_cfg=slots,
        store=store,
        simulated_failures=failures or {},
    )
    return ToolRegistry(ctx)


def test_unknown_tool_raises(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    with pytest.raises(ToolError):
        reg.call("publish", {})


def test_missing_material_raises(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    with pytest.raises(ToolError):
        reg.call("read_material", {"material_id": "M99"})


def test_read_material_reports_missing_fields(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    result = reg.call("read_material", {"material_id": "M03"})
    assert "duration_sec" in result["missing_fields"]
    assert "authorization" in result["missing_fields"]


def test_supplement_rewrites_material_for_check_rules(tmp_path, materials, rules, slots) -> None:
    """补查结果必须回写，否则规则检查看不到补查数据（补查形同虚设）。"""
    failures = {"M03": {"supplement": {"duration_sec": 15, "authorization": {
        "licensed": True, "license_id": "LIC-X", "valid_until": "2026-12-31"}}}}
    reg = _registry(tmp_path, materials, rules, slots, failures)

    before = reg.call("check_rules", {"material_id": "M03"})
    assert "R-DUR" in [r["rule_id"] for r in before["risks"]]

    reg.call("read_material", {"material_id": "M03", "fields": ["duration_sec", "authorization"]})

    after = reg.call("check_rules", {"material_id": "M03", "supplemented": True})
    assert "R-DUR" not in [r["rule_id"] for r in after["risks"]]
    assert "R-AUTH" not in [r["rule_id"] for r in after["risks"]]


def test_supplement_without_data_still_missing(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    result = reg.call("read_material", {"material_id": "M03", "fields": ["duration_sec"]})
    assert result["supplement_result"]["status"] == "still_missing"
    assert "duration_sec" in result["missing_fields"]


def test_supplement_does_not_mutate_source_material(tmp_path, materials, rules, slots) -> None:
    """补查只写入 overlay，不污染原始素材数据。"""
    failures = {"M03": {"supplement": {"duration_sec": 15}}}
    reg = _registry(tmp_path, materials, rules, slots, failures)
    reg.call("read_material", {"material_id": "M03", "fields": ["duration_sec"]})
    # M03 原始数据里根本没有 duration_sec 键，补查不应把它写回源数据
    assert "duration_sec" not in materials["materials"]["M03"]
    # 但工具层应看到补查后的值
    result = reg.call("read_material", {"material_id": "M03"})
    assert result["duration_sec"] == 15


def test_check_rules_flags_unresolved_after_supplement(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    reg.call("read_material", {"material_id": "M03", "fields": ["duration_sec"]})
    result = reg.call("check_rules", {"material_id": "M03", "supplemented": True})
    assert result["missing_fields"]
    assert "next_hint" in result


def test_query_slots_requires_date(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    with pytest.raises(ToolError):
        reg.call("query_slots", {})


def test_query_slots_reports_requested_conflict(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    result = reg.call("query_slots", {"date": "2026-11-02", "start": "18:00", "duration_sec": 60})
    assert result["requested_available"] is False
    assert result["requested_conflicts"]


def test_query_slots_reports_free_slot(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    result = reg.call("query_slots", {"date": "2026-11-02", "start": "19:00", "duration_sec": 60})
    assert result["requested_available"] is True
    assert result["requested_conflicts"] == []


def test_save_draft_requires_parameters(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    with pytest.raises(ToolError):
        reg.call("save_draft", {"material_id": "M01"})


def test_save_draft_failure_is_raised_not_swallowed(tmp_path, materials, rules, slots) -> None:
    """注入的写入故障必须抛出，调用方据此返回 failed。"""
    failures = {"M06": {"save_draft": "disk I/O error"}}
    reg = _registry(tmp_path, materials, rules, slots, failures)
    with pytest.raises(ToolError):
        reg.call("save_draft", {"material_id": "M06", "date": "2026-11-02", "start": "09:00"})


def test_save_draft_failure_uses_configured_injection(tmp_path, materials, rules, slots) -> None:
    """仓库内置的 M06 故障注入必须生效。"""
    reg = _registry(tmp_path, materials, rules, slots,
                   failures=materials.get("simulated_failures"))
    with pytest.raises(ToolError):
        reg.call("save_draft", {"material_id": "M06", "date": "2026-11-02", "start": "09:00"})


def test_save_draft_is_idempotent(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    args = {"material_id": "M01", "date": "2026-11-02", "start": "19:00"}
    first = reg.call("save_draft", args)
    second = reg.call("save_draft", args)
    assert first["status"] == "saved"
    assert second["status"] == "duplicate"
    assert first["draft_id"] == second["draft_id"]


def test_save_draft_never_marks_published(tmp_path, materials, rules, slots) -> None:
    reg = _registry(tmp_path, materials, rules, slots)
    result = reg.call("save_draft", {"material_id": "M01", "date": "2026-11-02", "start": "19:00"})
    assert result["published"] is False
