"""用例级端到端测试：直接驱动 data/cases.json 中定义的每个场景。

这些测试是「业务契约」的可执行版本：cases.json 里的 expect 描述对应
assert_* 断言字段，行为一旦回退立即失败。
"""

from __future__ import annotations

import json

import pytest

from src.runner import load_cases, run_case


def _case_names() -> list[str]:
    return list(load_cases()["cases"])


@pytest.mark.parametrize("name", _case_names())
def test_case_outcome(name: str, config) -> None:
    """每个用例的最终结果必须与 cases.json 中的断言一致。"""
    item = run_case(name, config=config)
    case = load_cases(config)["cases"][name]
    final = item["final"]

    assert final["outcome"] == case["assert_outcome"], final.get("reasoning")
    assert final["draft_status"] == case["assert_draft_status"]
    assert final["published"] is False
    if "assert_suggested_slot" in case:
        assert final.get("suggested_slot") == case["assert_suggested_slot"]
    if "assert_draft_id" in case:
        assert final.get("draft_id") == case["assert_draft_id"]


@pytest.mark.parametrize("name", _case_names())
def test_case_never_publishes(name: str, config) -> None:
    """任何路径都不得标记为已发布。"""
    item = run_case(name, config=config)
    for run in item["runs"]:
        assert run["published"] is False
        assert run.get("outcome") != "published"


def test_conflict_case_does_not_write_draft(config) -> None:
    """时段冲突且未确认 -> 不落库。"""
    item = run_case("normal_conflict", config=config)
    assert item["draft_count"] == 0
    assert item["final"]["pending_confirmations"]
    confirm = item["final"]["pending_confirmations"][0]
    assert confirm["type"] == "change_publish_time"
    assert confirm["original"] == "18:00"
    assert confirm["proposed"] == "19:00"


def test_blocked_case_does_not_write_draft(config) -> None:
    """阻断场景一律不落库。"""
    for name in ("missing_info", "rule_risk"):
        item = run_case(name, config=config)
        assert item["final"]["outcome"] == "blocked"
        assert item["draft_count"] == 0


def test_missing_info_reports_unresolved_fields(config) -> None:
    """补查后仍缺失 -> 明确指出缺失字段，而不是含糊的阻断。"""
    item = run_case("missing_info", config=config)
    missing = item["final"].get("missing_fields") or []
    assert "duration_sec" in missing
    assert "authorization" in missing


def test_save_failure_is_reported_honestly(config) -> None:
    """保存失败 -> 返回 failed，且草稿库为空（不得谎报成功）。"""
    item = run_case("save_failure", config=config)
    final = item["final"]
    assert final["outcome"] == "failed"
    assert final["draft_status"] == "failed"
    assert final["draft_id"] is None
    assert item["draft_count"] == 0


def test_duplicate_submit_twice_creates_single_draft(config) -> None:
    """连续两次相同保存请求 -> 草稿表只有一行，第二次返回 duplicate。"""
    item = run_case("duplicate_submit_twice", config=config)
    assert item["repeat"] == 2
    assert item["draft_count"] == 1
    first, second = item["runs"]
    assert first["draft_status"] == "saved"
    assert second["draft_status"] == "duplicate"
    assert first["draft_id"] == second["draft_id"]


def test_draft_payload_never_marked_published(config) -> None:
    """落库的草稿 payload 内部也必须标记为未发布。"""
    from src.runner import AgentRunner

    r = AgentRunner(config)
    run_case("duplicate_submit", config=config, runner=r)
    drafts = r.list_drafts()
    assert drafts
    for d in drafts:
        assert d["status"] == "draft"
        assert json.loads(d["payload"])["published"] is False
