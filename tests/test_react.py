"""ReAct 决策要求验证测试。

题目要求：
1. 至少实现一个「模型选择动作 → 执行工具 → 观察结果 → 模型调整下一步或结束」的循环
2. 至少两条执行路径的运行记录，体现模型根据**不同工具结果**调整动作
3. 仅有固定条件分支、自动重试或预设决策日志，**不视为完成** ReAct 决策要求

本文件用可执行断言证明上述三点。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.loop import ReActAgent
from src.llm.base import Action
from src.llm.offline_mock import OfflineMockClient
from src.runner import AgentRunner, run_case
from src.storage.db import DraftStore
from src.tools.impl import ToolContext, ToolRegistry


def _registry(tmp_path, materials, rules, slots, failures=None) -> ToolRegistry:
    store = DraftStore(tmp_path / "d.sqlite3")
    ctx = ToolContext(materials=materials["materials"], rules_cfg=rules, slots_cfg=slots,
                      store=store, simulated_failures=failures)
    return ToolRegistry(ctx)


# ----------------------------------------------------------------------
# 1. 循环结构：模型选择动作 -> 执行工具 -> 观察结果 -> 模型调整下一步或结束
# ----------------------------------------------------------------------
def test_loop_is_model_driven_not_scripted(tmp_path, materials, rules, slots) -> None:
    """决策必须来自 LLMClient，且每步的下一个动作依赖上一步的观测。"""
    client = OfflineMockClient()
    agent = ReActAgent(client=client, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=8)
    agent.run("把 M01 排到 2026-11-02 18:00", "M01", target_date="2026-11-02", target_start="18:00")

    tool_steps = [e for e in agent.trace if e["action"] == "tool"]
    assert len(tool_steps) >= 3

    # 每一步都记录了它依据哪个观测做出决策
    for entry in tool_steps[1:]:  # 第一步无观测
        assert entry["decided_by"], f"第 {entry['step']} 步未标注决策依据"

    # 决策确实随观测变化：后一步的 decided_by 不同
    decided_by = [e["decided_by"] for e in tool_steps[1:]]
    assert len(set(decided_by)) > 1, "每步决策依据相同，说明未随观测调整"


def test_observation_changes_next_action(tmp_path, materials, rules, slots) -> None:
    """同一素材，仅因观测不同（时段占用与否），模型的后续决策必须不同。

    关键差异不在「调了哪些工具」（两条路径都会走到 query_slots），
    而在于 **query_slots 的观测结果决定了模型的下一个动作**：
    - 观测到冲突 -> 结束并提交替代建议 + 待确认项（不落库）
    - 观测到空闲 -> 用户已确认 -> 调用 save_draft 落库
    """
    # 18:00 被占用（用户已确认）
    client_a = OfflineMockClient(confirmed=True)
    agent_a = ReActAgent(client=client_a, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=8)
    res_a = agent_a.run("排到 18:00", "M01", target_date="2026-11-02", target_start="18:00")
    seq_a = [e["tool"] for e in agent_a.trace if e["action"] == "tool"]

    # 20:00 空闲（用户已确认）
    client_b = OfflineMockClient(confirmed=True)
    agent_b = ReActAgent(client=client_b, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=8)
    res_b = agent_b.run("排到 20:00", "M01", target_date="2026-11-02", target_start="20:00")
    seq_b = [e["tool"] for e in agent_b.trace if e["action"] == "tool"]

    # 动作序列不同：冲突路径不落库，空闲路径落库
    assert seq_a != seq_b, "时段占用与否未影响动作序列"
    assert "save_draft" not in seq_a, "冲突路径不得保存草稿"
    assert "save_draft" in seq_b, "空闲且已确认应保存草稿"

    # 最终决策不同
    assert res_a["outcome"] == "needs_confirmation"
    assert res_b["draft_status"] == "saved"
    # 冲突路径给出的是「替代时段」19:00，而非用户原要求的 18:00
    assert res_a["suggested_slot"] == "19:00"
    assert res_b["suggested_slot"] == "20:00"
    assert res_a["published"] is False and res_b["published"] is False


def test_observation_changes_next_action_on_risk(tmp_path, materials, rules, slots) -> None:
    """信息缺失 vs 信息完整，决策序列必须不同（一个补查、一个直接进规则检查）。"""
    client_a = OfflineMockClient()
    agent_a = ReActAgent(client=client_a, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=8)
    agent_a.run("排 M03", "M03", target_date="2026-11-03", target_start="12:00")
    seq_a = [e["tool"] for e in agent_a.trace if e["action"] == "tool"]

    client_b = OfflineMockClient()
    agent_b = ReActAgent(client=client_b, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=8)
    agent_b.run("排 M05", "M05", target_date="2026-11-02", target_start="10:00")
    seq_b = [e["tool"] for e in agent_b.trace if e["action"] == "tool"]

    # M03 缺失字段 -> 触发补查（read_material 带 fields）
    assert len(seq_a) > len(seq_b), "信息缺失场景的步骤数应多于完整场景"
    supplements = [e for e in agent_a.trace if e["action"] == "tool" and e["args"].get("fields")]
    assert supplements, "缺失字段场景未触发补查"


def test_supplement_result_changes_rule_evaluation(tmp_path, materials, rules, slots) -> None:
    """补查的观测结果会改变规则检查结论 -> 决策随之改变。"""
    # 无补查数据：R-DUR 风险无法消解
    reg_a = _registry(tmp_path, materials, rules, slots)
    before = reg_a.call("check_rules", {"material_id": "M03"})
    assert "R-DUR" in before["unresolved_medium"]

    # 补查到时长后：风险消解
    reg_b = _registry(tmp_path, materials, rules, slots,
                      failures={"M03": {"supplement": {"duration_sec": 15}}})
    reg_b.call("read_material", {"material_id": "M03", "fields": ["duration_sec"]})
    after = reg_b.call("check_rules", {"material_id": "M03", "supplemented": True})
    assert "R-DUR" not in after["unresolved_medium"]


# ----------------------------------------------------------------------
# 2. 至少两条执行路径的运行记录
# ----------------------------------------------------------------------
def test_at_least_two_distinct_execution_paths(config) -> None:
    """至少两条不同执行路径，且建议结果不同。"""
    conflict = run_case("normal_conflict", config=config)
    ok = run_case("normal_ok", config=config)

    assert conflict["final"]["outcome"] == "needs_confirmation"
    assert ok["final"]["outcome"] == "needs_confirmation"
    # 冲突路径给出的建议时段与用户要求不同；正常路径与用户要求相同
    assert conflict["final"]["suggested_slot"] != ok["final"]["suggested_slot"]
    # 冲突路径必须带待确认项，正常路径不带改期确认
    assert any(c["type"] == "change_publish_time"
               for c in conflict["final"]["pending_confirmations"])
    assert all(c["type"] != "change_publish_time"
               for c in ok["final"]["pending_confirmations"])


def _tool_sequence(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("action") == "tool":
            out.append(entry.get("tool"))
    return out


@pytest.mark.parametrize("case_name", [
    "normal_conflict", "normal_ok", "missing_info", "rule_risk",
    "save_failure", "duplicate_submit", "duplicate_submit_twice",
])
def test_each_case_has_a_distinct_trace(case_name: str, config) -> None:
    """每个用例都产出包含步骤/工具参数/返回摘要/后续动作/决策依据的运行记录。"""
    trace_dir = Path(config.root) / "examples" / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    run_case(case_name, config=config, trace_dir=trace_dir)

    path = trace_dir / f"{case_name}.jsonl"
    assert path.exists(), f"{case_name} 缺少运行记录"

    entries = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert entries
    for e in entries:
        # 步骤、工具参数、返回摘要、后续动作、决策依据
        assert "step" in e
        assert "args" in e
        assert "result_summary" in e
        assert "next_action" in e
        assert "rationale" in e
        assert "decided_by" in e


def test_traces_show_different_paths() -> None:
    """不同用例的运行记录体现不同动作序列（满足「至少两条执行路径」）。"""
    base = Path(__file__).resolve().parents[1] / "examples" / "traces"
    conflict = _tool_sequence(base / "normal_conflict.jsonl")
    missing = _tool_sequence(base / "missing_info.jsonl")
    failure = _tool_sequence(base / "save_failure.jsonl")

    assert conflict and missing and failure
    assert len({tuple(conflict), tuple(missing), tuple(failure)}) >= 2


# ----------------------------------------------------------------------
# 3. 步数上限 -> 明确的未完成状态
# ----------------------------------------------------------------------
def test_tool_call_limit_returns_incomplete(tmp_path, materials, rules, slots) -> None:
    """步数上限触发时返回明确的未完成状态，不静默截断。"""
    class Looping:
        """永远要求调用工具的模型。"""

        name = "looping"

        def decide(self, messages, tool_specs):
            return Action(action="tool", tool="read_material",
                          args={"material_id": "M01"}, rationale="继续")

    agent = ReActAgent(client=Looping(), registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=3)
    result = agent.run("x", "M01", target_date="2026-11-02", target_start="18:00")
    assert result["outcome"] == "incomplete"
    assert result["incomplete_reason"] == "max_tool_calls_exceeded"
    assert result["tool_calls"] == 3
    assert "未完成" in result["reasoning"]


def test_limit_is_configurable(tmp_path, materials, rules, slots) -> None:
    """上限可配置。"""
    class Looping:
        name = "looping"

        def decide(self, messages, tool_specs):
            return Action(action="tool", tool="read_material", args={"material_id": "M01"})

    agent = ReActAgent(client=Looping(), registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=1)
    result = agent.run("x", "M01", target_date="2026-11-02", target_start="18:00")
    assert result["tool_calls"] == 1
    assert result["max_tool_calls"] == 1
