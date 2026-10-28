"""LLM 客户端抽象层。

两种实现共用同一套协议（见 `Action`），保证离线 Mock 与真实模型驱动的
ReAct 循环结构完全一致：

    LLMClient.decide(messages, tool_specs) -> Action
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class Action:
    """模型在一步中做出的决策。"""

    action: Literal["tool", "finish"] = "tool"
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    # action == "finish" 时给出的最终结果
    final: dict[str, Any] = field(default_factory=dict)
    # 决策依据的观测字段（如 "observation.missing_fields"）。
    # 用于证明「下一步动作由上一步的真实观测驱动」，写入 trace 供评审核对。
    decided_by: str = ""

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(payload: str | dict) -> "Action":
        if isinstance(payload, str):
            payload = json.loads(payload)
        action = payload.get("action", "tool")
        if action not in ("tool", "finish"):
            raise ValueError(f"非法 action: {action}")
        return Action(
            action=action,
            tool=payload.get("tool"),
            args=payload.get("args") or {},
            rationale=payload.get("rationale", ""),
            final=payload.get("final") or {},
            decided_by=payload.get("decided_by", ""),
        )


class LLMClient(Protocol):
    """模型客户端协议。"""

    name: str

    def decide(self, messages: list[dict], tool_specs: list[dict]) -> Action:  # pragma: no cover - 协议声明
        ...
