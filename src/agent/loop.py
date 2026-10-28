"""ReAct 执行循环。

流程（每一步）：
    模型基于已有观测 -> 产出 action（tool + args）
        -> 执行工具 -> 得到观测 observation
        -> 把观测回灌给模型 -> 模型决定下一步或结束

与固定分支脚本的区别：
- 「下一步做什么」完全由模型（LLMClient）产出，工具层只负责提供事实；
- 工具的真实返回值（冲突、缺失、失败）会改变模型后续的动作序列；
- 步数/工具调用数达到上限时，返回明确的未完成状态并保留全部 trace。

Trace 记录格式（每行一个 JSON）：
    step, action, tool, args, result_summary, next_action, rationale, elapsed_ms
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..llm.base import Action, LLMClient
from ..tools.contracts import TOOL_SPECS
from ..tools.impl import ToolError, ToolRegistry
from .prompts import SYSTEM_POLICY


def summarize(result: Any, limit: int = 400) -> str:
    """把工具返回压缩为可读的摘要（写入 trace）。"""
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ReActAgent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        max_tool_calls: int = 8,
        trace_writer: Callable[[dict], None] | None = None,
    ):
        self.client = client
        self.registry = registry
        self.max_tool_calls = max_tool_calls
        self.trace_writer = trace_writer
        self.messages: list[dict] = []
        self.trace: list[dict] = []

    # ------------------------------------------------------------------
    def run(self, user_request: str, material_id: str, confirmed: bool = False,
            target_date: str | None = None, target_start: str | None = None,
            extra_state: dict | None = None) -> dict[str, Any]:
        state: dict[str, Any] = {
            "material_id": material_id,
            "target_date": target_date,
            "target_start": target_start,
            "confirmed": confirmed,
            "supplement_done": False,
            "retried": False,
            "step": 0,
            "last_tool": None,
            "observation": None,
            "risk_level": None,
            "risks": [],
            "channel": None,
            "note": None,
            "duration_sec": None,
            "content_type": None,
            "check_rules_calls": 0,
            "save_attempts": 0,
            "last_error": None,
        }
        if extra_state:
            state.update(extra_state)

        self.messages = [{"role": "user", "content": user_request}]

        tool_calls = 0
        final: dict[str, Any] | None = None

        while True:
            state["step"] += 1
            if tool_calls >= self.max_tool_calls:
                final = self._incomplete(state, tool_calls)
                self._record_trace(state, action="incomplete", rationale="达到工具调用上限")
                break

            # --- 模型决策 ---
            try:
                action = self.client.decide(self._build_messages(state), TOOL_SPECS)
            except Exception as exc:  # 模型侧失败：如实返回
                final = self._incomplete(state, tool_calls, reason=f"model_error: {exc}")
                self._record_trace(state, action="incomplete", rationale=f"模型调用失败: {exc}")
                break

            if action.action == "finish":
                final = self._finalize(action, state, tool_calls)
                self._record_trace(state, action="finish", rationale=action.rationale,
                                  tool=action.tool, args=action.args, observation=final,
                                  decided_by=action.decided_by)
                break

            if not action.tool:
                final = self._incomplete(state, tool_calls, reason="模型给出空工具名")
                self._record_trace(state, action="invalid", rationale=action.rationale,
                                  decided_by=action.decided_by)
                break

            # --- 执行工具 ---
            tool_calls += 1
            try:
                observation = self.registry.call(action.tool, action.args)
                tool_error = None
            except ToolError as exc:
                observation = {
                    "error": True,
                    "message": exc.message,
                    "detail": exc.detail,
                }
                tool_error = exc.message
            except Exception as exc:  # 未预期异常同样如实暴露
                observation = {"error": True, "message": f"{type(exc).__name__}: {exc}"}
                tool_error = observation["message"]

            # --- 观测回灌 + 状态更新 ---
            self._absorb(state, action, observation, tool_error)
            self.messages.append({"role": "assistant", "content": json.dumps(
                {"tool": action.tool, "args": action.args}, ensure_ascii=False)})
            self.messages.append({"role": "user", "content": json.dumps(
                {"step": state["step"], "last_tool": action.tool, "observation": observation, **state},
                ensure_ascii=False, default=str)})

            self._record_trace(state, action="tool", rationale=action.rationale,
                              tool=action.tool, args=action.args, observation=observation,
                              tool_error=tool_error, decided_by=action.decided_by)

        assert final is not None
        final["tool_calls"] = tool_calls
        final["max_tool_calls"] = self.max_tool_calls
        final["published"] = False
        return final

    # ------------------------------------------------------------------
    def _build_messages(self, state: dict) -> list[dict]:
        """构造送给模型的对话：原始请求 + 逐步观测。"""
        payload = json.dumps(
            {"step": state["step"], "last_tool": state["last_tool"],
             "observation": state["observation"], **state},
            ensure_ascii=False, default=str,
        )
        msgs = list(self.messages)
        msgs.append({"role": "user", "content": payload})
        return msgs

    def _absorb(self, state: dict, action: Action, observation: dict, tool_error: str | None) -> None:
        state["last_tool"] = action.tool
        state["observation"] = observation

        if action.tool == "read_material":
            # 素材元信息同步到状态：供 query_slots 计算占用时段、save_draft 选渠道
            if not observation.get("error"):
                if observation.get("duration_sec") is not None:
                    state["duration_sec"] = observation.get("duration_sec")
                if observation.get("content_type"):
                    state["content_type"] = observation.get("content_type")
                if observation.get("channels"):
                    state["channel"] = observation.get("channels")[0]
            if action.args.get("fields"):
                state["supplement_done"] = True  # 补查已发起
        elif action.tool == "check_rules":
            state["check_rules_calls"] = int(state.get("check_rules_calls") or 0) + 1
            if not observation.get("error"):
                state["risk_level"] = observation.get("risk_level")
                state["risks"] = observation.get("risks") or []
        elif action.tool == "save_draft":
            state["save_attempts"] = int(state.get("save_attempts") or 0) + 1

        if tool_error:
            state["last_error"] = tool_error

    def _finalize(self, action: Action, state: dict, tool_calls: int) -> dict[str, Any]:
        final = dict(action.final or {})
        final.setdefault("material_id", state["material_id"])
        final.setdefault("published", False)
        final.setdefault("outcome", "saved")
        final.setdefault("draft_status", "not_saved")
        final.setdefault("draft_id", None)
        final.setdefault("pending_confirmations", [])
        final.setdefault("risk_level", state.get("risk_level"))
        final.setdefault("risks", state.get("risks") or [])
        final.setdefault("suggested_slot", None)
        final.setdefault("suggested_date", state.get("target_date"))
        final.setdefault("reasoning", action.rationale)
        # 越权保护：任何情况下都不允许标记为已发布
        final["published"] = False
        if final.get("draft_status") == "saved" and not final.get("draft_id"):
            final["draft_status"] = "failed"
            final["reasoning"] = "缺少 draft_id，判定保存结果不可信，标记为 failed"
        return final

    def _incomplete(self, state: dict, tool_calls: int, reason: str = "max_tool_calls_exceeded") -> dict[str, Any]:
        return {
            "material_id": state["material_id"],
            "outcome": "incomplete",
            "draft_status": "not_saved",
            "risk_level": state.get("risk_level"),
            "risks": state.get("risks") or [],
            "suggested_slot": None,
            "suggested_date": None,
            "pending_confirmations": [],
            "draft_id": None,
            "published": False,
            "reasoning": f"执行未完成：{reason}（已用工具调用 {tool_calls} 次）",
            "incomplete_reason": reason,
        }

    def _record_trace(self, state: dict, action: str, rationale: str = "", tool: str | None = None,
                      args: dict | None = None, observation: Any = None, tool_error: str | None = None,
                      decided_by: str = "") -> None:
        entry = {
            "step": state["step"],
            "action": action,
            "tool": tool,
            "args": args or {},
            "result_summary": summarize(observation) if observation is not None else "",
            "next_action": "由模型根据观测决定下一步" if action == "tool" else "结束",
            "rationale": rationale,
            "decided_by": decided_by,
            "tool_error": tool_error,
            "elapsed_ms": 0,
        }
        self.trace.append(entry)
        if self.trace_writer:
            self.trace_writer(entry)


def write_trace(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
