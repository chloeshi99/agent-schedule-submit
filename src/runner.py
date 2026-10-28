"""Agent 运行器（装配层）。

把「配置 + 数据 + 工具 + 模型 + 循环」装配成一个可调用的运行入口，
CLI 与测试都走这里，保证二者行为一致。

装配过程刻意保持**零外部依赖**：不设置任何环境变量即可完整运行
（使用离线 Mock 决策引擎 + 本地 SQLite 草稿库）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent.loop import ReActAgent, write_trace
from .config import Config
from .domain import parsing
from .llm.base import LLMClient
from .llm.offline_mock import OfflineMockClient
from .llm.openai_compat import OpenAICompatClient
from .storage.db import DraftStore
from .tools.impl import ToolContext, ToolRegistry


class AgentRunner:
    """一次装配，多次运行。"""

    def __init__(self, config: Config | None = None, client: LLMClient | None = None,
                 store: DraftStore | None = None):
        self.config = config or Config.load()
        self.materials_cfg = self.config.load_json("materials.json")
        self.rules_cfg = self.config.load_json("rules.json")
        self.slots_cfg = self.config.load_json("slots.json")
        self.client = client or self._build_client()
        self.store = store or DraftStore(self.config.db_path)
        self._registry: ToolRegistry | None = None

    # ------------------------------------------------------------------
    def _build_client(self) -> LLMClient:
        if not self.config.offline and self.config.llm_api_key:
            return OpenAICompatClient(
                base_url=self.config.llm_base_url,
                api_key=self.config.llm_api_key,
                model=self.config.llm_model,
                temperature=self.config.temperature,
            )
        return OfflineMockClient()

    def _make_registry(self) -> ToolRegistry:
        ctx = ToolContext(
            materials=self.materials_cfg.get("materials", {}),
            rules_cfg=self.rules_cfg,
            slots_cfg=self.slots_cfg,
            store=self.store,
            simulated_failures=self.materials_cfg.get("simulated_failures", {}),
        )
        return ToolRegistry(ctx)

    # ------------------------------------------------------------------
    def run(
        self,
        user_request: str,
        material_id: str | None = None,
        confirmed: bool | None = None,
        target_date: str | None = None,
        target_start: str | None = None,
        trace_path: Path | None = None,
    ) -> dict[str, Any]:
        """执行一次排期请求。

        参数缺省时从 user_request 中做确定性抽取（日期/时刻/素材 ID/确认意图）。
        """
        parsed = parsing.parse_request(user_request)
        material_id = material_id or parsed["material_id"]
        if not material_id:
            return self._bad_request("未能识别素材 ID，请用 --material 指定")
        target_date = target_date or parsed["date"]
        target_start = target_start or parsed["start"]
        if confirmed is None:
            inferred = parsed["confirmed"]
            confirmed = bool(inferred) if inferred is not None else False
        if not target_date or not target_start:
            return self._bad_request("未能识别目标日期/时刻，请显式指定")

        # 每次运行使用独立的工具上下文（overlay 不跨运行泄漏）
        self._registry = self._make_registry()
        registry = self._registry

        # 模型侧的 confirmed 与运行参数保持一致
        if isinstance(self.client, OfflineMockClient):
            self.client.confirmed = confirmed

        writer = None
        if trace_path is not None:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            writer = lambda entry: write_trace(trace_path, entry)  # noqa: E731

        agent = ReActAgent(
            client=self.client,
            registry=registry,
            max_tool_calls=self.config.max_tool_calls,
            trace_writer=writer,
        )
        result = agent.run(
            user_request=user_request,
            material_id=material_id,
            confirmed=confirmed,
            target_date=target_date,
            target_start=target_start,
            extra_state={"confirmed": confirmed},
        )
        result["confirmed"] = confirmed
        result["user_request"] = user_request
        result["client"] = getattr(self.client, "name", type(self.client).__name__)
        return result

    # ------------------------------------------------------------------
    def _bad_request(self, message: str) -> dict[str, Any]:
        return {
            "material_id": None,
            "outcome": "failed",
            "draft_status": "not_saved",
            "risk_level": None,
            "risks": [],
            "suggested_slot": None,
            "suggested_date": None,
            "pending_confirmations": [],
            "draft_id": None,
            "published": False,
            "reasoning": f"请求参数不完整：{message}",
            "tool_calls": 0,
        }

    # ------------------------------------------------------------------
    def draft_count(self) -> int:
        return self.store.count()

    def list_drafts(self) -> list[dict]:
        return self.store.list_drafts()


def load_cases(config: Config | None = None) -> dict[str, Any]:
    """读取用例定义。"""
    config = config or Config.load()
    return config.load_json("cases.json")


def run_case(name: str, config: Config | None = None, runner: AgentRunner | None = None,
             trace_dir: Path | None = None) -> dict[str, Any]:
    """执行一个具名用例，支持 repeat 多次执行（用于验证幂等）。"""
    config = config or Config.load()
    cases = load_cases(config)["cases"]
    if name not in cases:
        raise KeyError(f"未知用例: {name}，可选: {', '.join(cases)}")
    case = cases[name]

    runner = runner or AgentRunner(config)
    repeat = int(case.get("repeat", 1))
    trace_path = None
    if trace_dir is not None:
        trace_path = Path(trace_dir) / f"{name}.jsonl"
        # 每次重跑都从空文件开始，避免历史记录与当前实现不一致
        if trace_path.exists():
            trace_path.unlink()

    runs: list[dict[str, Any]] = []
    for _ in range(repeat):
        runs.append(
            runner.run(
                user_request=case["user_request"],
                material_id=case.get("material_id"),
                confirmed=bool(case.get("confirm", False)),
                target_date=case.get("date"),
                target_start=case.get("time"),
                trace_path=trace_path,
            )
        )

    return {
        "case": name,
        "expect": case.get("expect"),
        "user_request": case["user_request"],
        "repeat": repeat,
        "runs": runs,
        "final": runs[-1],
        "draft_count": runner.draft_count(),
    }


def summarize(result: dict[str, Any]) -> str:
    """把运行结果压缩为一行可读摘要。"""
    return json.dumps(
        {
            "outcome": result.get("outcome"),
            "draft_status": result.get("draft_status"),
            "draft_id": result.get("draft_id"),
            "risk_level": result.get("risk_level"),
            "suggested": f"{result.get('suggested_date')} {result.get('suggested_slot')}",
            "tool_calls": result.get("tool_calls"),
            "published": result.get("published"),
        },
        ensure_ascii=False,
    )
