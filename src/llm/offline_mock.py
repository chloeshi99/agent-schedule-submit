"""离线决策引擎（Offline Mock Client）。

重要边界声明
------------
本模块**不是**预录日志，也不是固定分支脚本。它是一个**观测驱动的策略引擎**：
它只读取「上一次工具返回的真实观测值」（occupied / risks / missing_fields 等），
再据此选择下一步动作。因此当工具结果不同时（时段被占用 vs 空闲、素材信息缺失
vs 完整、写入成功 vs 失败），它给出的动作序列会真实不同。

但它仍然**不是真实大模型**：它只能证明「流程与工具契约正确」，
**不能证明真实模型的决策质量**。该边界在 docs/03-evaluation.md 与
docs/04-ai-usage.md 中均已明示。

真实模型通道见 `openai_compat.OpenAICompatClient`，两者共用同一套
Action 协议与同一段系统提示词约束（见 agent/prompts.py）。

状态机（由观测驱动，不依赖步号）
--------------------------------
    (无观测) --read_material--> 字段缺失? --是--> --read_material(fields)--> 补查
                                                                      |
                                                                      v
                                                            --check_rules--> 需补查?
                                                                 |是
                                                                 v
                                                            --read_material(fields)--> 补查
                                                                 |完成后
                                                                 v
                                                            --check_rules(supplemented)--> 未消解风险?
                                                                 |是 -> blocked
                                                                 |否
                                                                 v
                                                            --query_slots--> 冲突?
                                                                 |是 -> needs_confirmation（给替代时段）
                                                                 |否 -> 需确认? -> needs_confirmation
                                                                              |已确认
                                                                              v
                                                                     --save_draft--> saved/duplicate/failed
"""

from __future__ import annotations

import json
from typing import Any

from .base import Action


class OfflineMockClient:
    """确定性的观测驱动策略客户端。"""

    name = "offline-mock"

    def __init__(self, confirmed: bool = False):
        self.confirmed = confirmed

    # ------------------------------------------------------------------
    def decide(self, messages: list[dict], tool_specs: list[dict]) -> Action:
        state = self._extract_state(messages)
        last_tool = state.get("last_tool")
        obs = state.get("observation") or {}

        # 首次决策：尚未调用任何工具
        if last_tool is None:
            return self._act(
                "read_material",
                {"material_id": state["material_id"]},
                "第一步读取素材元数据，确认素材存在并获取发布时间要求与审核所需字段。",
                decided_by="state.step:1(无观测，从读取素材开始)",
            )

        if last_tool == "read_material":
            return self._after_read(state, obs)
        if last_tool == "check_rules":
            return self._after_check_rules(state, obs)
        if last_tool == "query_slots":
            return self._after_query_slots(state, obs)
        if last_tool == "save_draft":
            return self._after_save(state, obs)
        return self._finish(state, "无可用动作，结束执行。")

    # ------------------------------------------------------------------
    def _act(self, tool: str, args: dict, rationale: str, decided_by: str = "") -> Action:
        """构造一次工具调用决策。

        `decided_by` 标注本次决策所依据的观测字段路径，例如
        "observation.missing_fields"。它写入 trace，用于证明
        「下一步动作由上一步的真实观测驱动」，而非按固定步号推进。
        """
        return Action(action="tool", tool=tool, args=args, rationale=rationale, decided_by=decided_by)

    def _finish(self, state: dict, rationale: str, decided_by: str = "", **extra: Any) -> Action:
        final = {
            "material_id": state["material_id"],
            "published": False,
            "draft_status": "not_saved",
            "reasoning": rationale,
        }
        final.update(extra)
        return Action(action="finish", rationale=rationale, final=final, decided_by=decided_by)

    # ------------------------------------------------------------------
    def _after_read(self, state: dict, obs: dict) -> Action:
        missing = obs.get("missing_fields") or []
        supplement_done = bool(state.get("supplement_done"))

        if missing and not supplement_done:
            return self._act(
                "read_material",
                {"material_id": state["material_id"], "fields": missing},
                f"read_material 观测到字段缺失 {missing}，先补查再判断，避免用臆测值排期。",
                decided_by="observation.missing_fields",
            )

        return self._act(
            "check_rules",
            {"material_id": state["material_id"], "supplemented": supplement_done},
            "素材信息已获取，转入规则检查以确定审核状态与风险原因。",
            decided_by="observation.missing_fields:empty",
        )

    # ------------------------------------------------------------------
    def _after_check_rules(self, state: dict, obs: dict) -> Action:
        risk_level = obs.get("risk_level")
        risks = obs.get("risks") or []
        need_supplement = bool(obs.get("need_supplement"))
        supplement_done = bool(state.get("supplement_done"))
        check_calls = int(state.get("check_rules_calls") or 0)

        # 规则提示仍需补查，且尚未补查 -> 补查
        if need_supplement and not supplement_done:
            fields = obs.get("supplement_fields") or []
            return self._act(
                "read_material",
                {"material_id": state["material_id"], "fields": fields},
                f"规则检查提示需补查 {fields}（当前风险 {risk_level}），补查后再判定是否可排期。",
                decided_by="observation.need_supplement",
            )

        # 已补查，需要重新评估一次
        if need_supplement and check_calls < 2:
            return self._act(
                "check_rules",
                {"material_id": state["material_id"], "supplemented": True},
                "已补查，重新执行规则检查以确认风险是否消解。",
                decided_by="observation.need_supplement+state.supplement_done",
            )

        unresolved_high = obs.get("unresolved_high") or []
        unresolved_medium = obs.get("unresolved_medium") or []
        if unresolved_high:
            return self._finish(
                state,
                f"补查后仍存在高风险规则 {unresolved_high}，无法自动消解，阻断排期并请求人工复核。",
                decided_by="observation.unresolved_high",
                outcome="blocked",
                risk_level=risk_level,
                risks=risks,
                missing_fields=obs.get("missing_fields") or [],
                pending_confirmations=[],
                suggested_slot=None,
                suggested_date=state.get("target_date"),
            )
        if unresolved_medium:
            return self._finish(
                state,
                f"补查后仍存在中风险规则 {unresolved_medium}，需人工处理后再排期。",
                decided_by="observation.unresolved_medium",
                outcome="blocked",
                risk_level=risk_level,
                risks=risks,
                missing_fields=obs.get("missing_fields") or [],
                pending_confirmations=[],
                suggested_slot=None,
                suggested_date=state.get("target_date"),
            )
        if need_supplement:
            # 风险等级为 low（仅提示）时可继续
            if risk_level != "low":
                return self._finish(
                    state,
                    "补查后仍有未消解风险，阻断排期。",
                    decided_by="observation.need_supplement+unresolved",
                    outcome="blocked",
                    risk_level=risk_level,
                    risks=risks,
                    missing_fields=obs.get("missing_fields") or [],
                    pending_confirmations=[],
                    suggested_slot=None,
                    suggested_date=state.get("target_date"),
                )

        return self._act(
            "query_slots",
            {
                "date": state.get("target_date"),
                "start": state.get("target_start"),
                "duration_sec": state.get("duration_sec") or 0,
            },
            "风险可接受，进入时段查询，检查目标时段是否被占用并获取可用替代时段。",
            decided_by="observation.unresolved_high:empty",
        )

    # ------------------------------------------------------------------
    def _after_query_slots(self, state: dict, obs: dict) -> Action:
        target_date = obs.get("date") or state.get("target_date")
        target_start = state.get("target_start")
        conflicts = obs.get("requested_conflicts")
        available = obs.get("available_slots") or []

        if conflicts:
            alternative = self._pick_alternative(target_start, available)
            if alternative is None:
                return self._finish(
                    state,
                    "目标时段冲突且当日无可用替代时段，阻断排期，请求人工协调。",
                    decided_by="observation.requested_conflicts+available_slots:empty",
                    outcome="blocked",
                    risk_level=state.get("risk_level"),
                    risks=state.get("risks") or [],
                    conflicts=conflicts,
                    suggested_slot=None,
                    suggested_date=target_date,
                    pending_confirmations=[],
                )
            return self._finish(
                state,
                (
                    f"query_slots 观测到 {target_start} 已被 "
                    f"{', '.join(c['material_id'] for c in conflicts)} 占用；"
                    f"按同一冲突粒度检索到最近可用时段 {alternative}。"
                    "由于用户对该时间是硬性要求，模型不得擅自更改，改为提交替代建议并请求确认。"
                ),
                decided_by="observation.requested_conflicts",
                outcome="needs_confirmation",
                risk_level=state.get("risk_level"),
                risks=state.get("risks") or [],
                conflicts=conflicts,
                suggested_slot=alternative,
                suggested_date=target_date,
                pending_confirmations=[
                    {
                        "type": "change_publish_time",
                        "field": "publish_requirement.time",
                        "original": target_start,
                        "proposed": alternative,
                        "question": f"{target_start} 已被占用，是否改用 {alternative}？未确认前不会保存草稿。",
                    }
                ],
            )

        # 无冲突：目标时段可用
        if not self.confirmed:
            return self._finish(
                state,
                "目标时段空闲且无阻断风险，但用户尚未确认，故只输出建议不落库。",
                decided_by="observation.requested_available+state.confirmed:false",
                outcome="needs_confirmation",
                risk_level=state.get("risk_level"),
                risks=state.get("risks") or [],
                conflicts=[],
                suggested_slot=target_start,
                suggested_date=target_date,
                pending_confirmations=[
                    {
                        "type": "confirm_schedule",
                        "field": "schedule",
                        "original": target_start,
                        "proposed": target_start,
                        "question": f"确认按 {target_date} {target_start} 保存排期草稿吗？",
                    }
                ],
            )

        return self._act(
            "save_draft",
            {
                "material_id": state["material_id"],
                "date": target_date,
                "start": target_start,
                "channel": state.get("channel"),
                "risk_level": state.get("risk_level"),
                "note": state.get("note"),
            },
            "用户已确认且时段无冲突、无阻断风险，保存排期草稿（仅草稿，不发布）。",
            decided_by="observation.requested_available+state.confirmed:true",
        )

    # ------------------------------------------------------------------
    def _after_save(self, state: dict, obs: dict) -> Action:
        status = obs.get("status")
        if status == "saved":
            return self._finish(
                state,
                "save_draft 返回 saved，草稿已写入草稿库（仅为草稿，未发布）。",
                decided_by="observation.status:saved",
                outcome="saved",
                draft_status="saved",
                draft_id=obs.get("draft_id"),
                risk_level=state.get("risk_level"),
                risks=state.get("risks") or [],
                suggested_slot=state.get("target_start"),
                suggested_date=state.get("target_date"),
                pending_confirmations=[],
                save_detail=obs.get("detail"),
            )
        if status == "duplicate":
            return self._finish(
                state,
                "save_draft 返回 duplicate：相同请求已存在草稿，未重复创建，草稿总数不变。",
                decided_by="observation.status:duplicate",
                outcome="saved",
                draft_status="duplicate",
                draft_id=obs.get("draft_id"),
                risk_level=state.get("risk_level"),
                risks=state.get("risks") or [],
                suggested_slot=state.get("target_start"),
                suggested_date=state.get("target_date"),
                pending_confirmations=[],
                save_detail=obs.get("detail"),
            )

        # 失败：原参数重试一次以排除瞬时故障；仍然失败则如实返回 failed
        attempts = int(state.get("save_attempts") or 0)
        if attempts < 1:
            return self._act(
                "save_draft",
                {
                    "material_id": state["material_id"],
                    "date": state.get("target_date"),
                    "start": state.get("target_start"),
                    "channel": state.get("channel"),
                    "risk_level": state.get("risk_level"),
                    "note": state.get("note"),
                },
                "save_draft 返回失败，先原参数重试一次以排除瞬时故障。",
                decided_by="observation.error+state.save_attempts",
            )
        return self._finish(
            state,
            "重试后保存仍失败，如实返回 failed，草稿未写入。",
            decided_by="observation.error+state.save_attempts:exhausted",
            outcome="failed",
            draft_status="failed",
            risk_level=state.get("risk_level"),
            risks=state.get("risks") or [],
            suggested_slot=state.get("target_start"),
            suggested_date=state.get("target_date"),
            pending_confirmations=[],
            save_detail=state.get("last_error"),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _pick_alternative(target_start: str | None, available: list[str]) -> str | None:
        if not available:
            return None
        if target_start:
            later = [s for s in available if s > target_start]
            if later:
                return later[0]
            earlier = [s for s in available if s < target_start]
            if earlier:
                return earlier[-1]
        return available[0]

    # ------------------------------------------------------------------
    def _extract_state(self, messages: list[dict]) -> dict[str, Any]:
        """从消息历史中恢复运行状态（最后一条含 step 的 user 消息为当前状态）。"""
        state: dict[str, Any] = {}
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip().startswith("{"):
                continue
            try:
                payload = json.loads(content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and "step" in payload:
                state = payload
        return state
