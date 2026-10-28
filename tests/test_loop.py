"""ReAct 循环单元测试：上限保护、越权保护、trace、异常暴露。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.loop import ReActAgent
from src.llm.base import Action
from src.storage.db import DraftStore
from src.tools.impl import ToolContext, ToolRegistry


class ScriptedClient:
    """按预设脚本依次返回动作的假客户端。"""

    name = "scripted"

    def __init__(self, actions: list[Action]):
        self.actions = actions
        self.calls = 0

    def decide(self, messages, tool_specs) -> Action:
        if self.calls < len(self.actions):
            action = self.actions[self.calls]
            self.calls += 1
            return action
        return Action(action="finish", rationale="脚本耗尽", final={"outcome": "saved"})


def _jsonl_writer(path: Path):
    """把 trace 逐行写入 JSONL 文件。"""
    def writer(entry: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return writer


def _registry(tmp_path, materials, rules, slots) -> ToolRegistry:
    store = DraftStore(tmp_path / "d.sqlite3")
    ctx = ToolContext(materials=materials["materials"], rules_cfg=rules, slots_cfg=slots, store=store)
    return ToolRegistry(ctx)


def test_max_tool_calls_stops_loop(tmp_path, materials, rules, slots) -> None:
    """达到工具调用上限 -> 返回 incomplete，而不是无限循环。"""
    client = ScriptedClient([
        Action(action="tool", tool="read_material", args={"material_id": "M01"}),
        Action(action="tool", tool="read_material", args={"material_id": "M01"}),
    ])
    agent = ReActAgent(client=client, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=2)
    result = agent.run("把 M01 排到 2026-11-02 18:00", "M01", target_date="2026-11-02", target_start="18:00")
    assert result["outcome"] == "incomplete"
    assert result["tool_calls"] == 2
    assert result["draft_status"] == "not_saved"


def test_model_error_is_reported_honestly(tmp_path, materials, rules, slots) -> None:
    """模型抛错 -> 返回 incomplete 且注明 model_error，不伪造成功。"""
    class Boom:
        name = "boom"

        def decide(self, messages, tool_specs):
            raise RuntimeError("模型不可用")

    agent = ReActAgent(client=Boom(), registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=3)
    result = agent.run("把 M01 排到 2026-11-02 18:00", "M01", target_date="2026-11-02", target_start="18:00")
    assert result["outcome"] == "incomplete"
    assert "model_error" in result["incomplete_reason"]


def test_empty_tool_name_is_rejected(tmp_path, materials, rules, slots) -> None:
    client = ScriptedClient([Action(action="tool", tool=None, args={})])
    agent = ReActAgent(client=client, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=3)
    result = agent.run("x", "M01", target_date="2026-11-02", target_start="18:00")
    assert result["outcome"] == "incomplete"
    assert "空工具名" in result["incomplete_reason"]


def test_finalize_cannot_mark_published(tmp_path, materials, rules, slots) -> None:
    """即使模型声称已发布，越权保护也会强制置为 false。"""
    client = ScriptedClient([
        Action(action="finish", rationale="声称发布", final={"outcome": "saved", "published": True, "draft_id": "D1"}),
    ])
    agent = ReActAgent(client=client, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=3)
    result = agent.run("x", "M01", target_date="2026-11-02", target_start="18:00")
    assert result["published"] is False


def test_saved_without_draft_id_becomes_failed(tmp_path, materials, rules, slots) -> None:
    """声称已保存但没有 draft_id -> 标记 failed（结果不可信）。"""
    client = ScriptedClient([
        Action(action="finish", rationale="x", final={"outcome": "saved", "draft_status": "saved"}),
    ])
    agent = ReActAgent(client=client, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=3)
    result = agent.run("x", "M01", target_date="2026-11-02", target_start="18:00")
    assert result["draft_status"] == "failed"


def test_tool_error_is_recorded_not_hidden(tmp_path, materials, rules, slots) -> None:
    client = ScriptedClient([
        Action(action="tool", tool="read_material", args={"material_id": "M99"}),
        Action(action="finish", rationale="结束", final={"outcome": "blocked"}),
    ])
    agent = ReActAgent(client=client, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=3)
    result = agent.run("x", "M01", target_date="2026-11-02", target_start="18:00")
    assert any(entry["tool_error"] for entry in agent.trace)


def test_trace_written_to_file(tmp_path, materials, rules, slots) -> None:
    trace_path = Path(tmp_path) / "traces" / "run.jsonl"
    client = ScriptedClient([
        Action(action="tool", tool="read_material", args={"material_id": "M01"}),
        Action(action="finish", rationale="结束", final={"outcome": "blocked"}),
    ])
    agent = ReActAgent(
        client=client,
        registry=_registry(tmp_path, materials, rules, slots),
        max_tool_calls=3,
        trace_writer=_jsonl_writer(trace_path),
    )
    agent.run("x", "M01", target_date="2026-11-02", target_start="18:00")
    assert trace_path.exists()
    lines = [json.loads(x) for x in trace_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines
    assert {"step", "action", "tool", "args", "result_summary", "rationale"} <= set(lines[0])


def test_material_metadata_flows_into_state(tmp_path, materials, rules, slots) -> None:
    """read_material 观测到的时长必须进入状态，供 query_slots 使用。"""
    client = ScriptedClient([
        Action(action="tool", tool="read_material", args={"material_id": "M01"}),
        Action(action="finish", rationale="结束", final={"outcome": "blocked"}),
    ])
    agent = ReActAgent(client=client, registry=_registry(tmp_path, materials, rules, slots), max_tool_calls=3)
    agent.run("x", "M01", target_date="2026-11-02", target_start="18:00")
    tool_entries = [e for e in agent.trace if e["action"] == "tool"]
    assert tool_entries
    assert tool_entries[0]["tool"] == "read_material"
    assert '"duration_sec": 60' in tool_entries[0]["result_summary"]
