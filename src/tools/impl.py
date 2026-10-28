"""工具实现层。

每个工具返回结构化 dict；若失败则抛出 `ToolError`（携带明确的失败原因）。
工具层不做任何决策，只做「事实获取」与「受控写入」。

补查（supplement）语义
----------------------
`read_material(fields=[...])` 会对素材执行一次「补查」，并把补查到的字段
**回写**到该素材的内存副本（overlay）中。后续 `check_rules` 在同一素材上
评估时能看到补查结果——否则补查只是空转，规则检查永远基于原始缺失数据。

补查来源优先级：
1. `simulated_failures[material_id]["supplement"]` 中显式注入的字段（测试/演示）
2. 素材自身 `notes` 字段中显式给出的补查结论
3. 无数据 -> 返回 `still_missing`，缺失项保持缺失，交由模型阻断处理
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..domain import rules as rules_mod
from ..domain import slots as slots_mod
from ..storage.db import DraftStore, build_request_key, content_hash


class ToolError(RuntimeError):
    """工具执行失败，携带结构化信息。"""

    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class ToolContext:
    """工具运行所需的全部依赖。"""

    def __init__(
        self,
        materials: dict,
        rules_cfg: dict,
        slots_cfg: dict,
        store: DraftStore,
        simulated_failures: dict | None = None,
    ):
        self.materials = materials
        self.rules_cfg = rules_cfg
        self.slots_cfg = slots_cfg
        self.store = store
        self.simulated_failures = simulated_failures or {}
        # 补查回写的 overlay：material_id -> {field: value}
        self.overlay: dict[str, dict[str, Any]] = {}
        # 记录本轮各素材的补查次数（用于幂等与一致性校验）
        self.supplement_calls: dict[str, int] = {}

    def effective_material(self, material_id: str) -> dict:
        """返回叠加了补查结果的素材副本（不修改原始数据）。"""
        base = self.materials[material_id]
        patch = self.overlay.get(material_id)
        if not patch:
            return base
        merged = dict(base)
        merged.update(patch)
        return merged

    def apply_supplement(self, material_id: str, fields: list[str]) -> tuple[dict, str]:
        """执行一次补查，返回 (补到的字段, 状态)。

        状态取值: "supplemented"（有数据）/ "still_missing"（补查后仍缺失）
        """
        supplied = self._supplement_source(material_id)
        patch: dict[str, Any] = {}
        for field in fields:
            if field in supplied and supplied[field] not in (None, "", [], {}):
                patch[field] = supplied[field]
        if patch:
            self.overlay.setdefault(material_id, {}).update(patch)
            return patch, "supplemented"
        return {}, "still_missing"

    def _supplement_source(self, material_id: str) -> dict:
        """补查数据源：注入的故障/补查配置优先，其次素材 notes。"""
        injected = self.simulated_failures.get(material_id, {}).get("supplement") or {}
        source = dict(injected)
        notes = self.materials.get(material_id, {}).get("notes")
        if isinstance(notes, dict):
            source.update(notes)
        return source


class ToolRegistry:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self._map = {
            "read_material": self.read_material,
            "check_rules": self.check_rules,
            "query_slots": self.query_slots,
            "save_draft": self.save_draft,
        }

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in self._map:
            raise ToolError(f"未知工具: {name}", {"available": list(self._map)})
        return self._map[name](args or {})

    # ---------- read_material ----------
    def read_material(self, args: dict[str, Any]) -> dict[str, Any]:
        material_id = args.get("material_id")
        if material_id not in self.ctx.materials:
            raise ToolError(f"素材不存在: {material_id}", {"material_id": material_id})

        material = self.ctx.effective_material(material_id)
        result: dict[str, Any] = {
            "material_id": material_id,
            "title": material.get("title"),
            "content_type": material.get("content_type"),
            "duration_sec": material.get("duration_sec"),
            "copy_text": material.get("copy_text"),
            "sensitivity_tags": material.get("sensitivity_tags") or [],
            "review_notes": material.get("review_notes"),
            "authorization": material.get("authorization"),
            "channels": material.get("channels") or [],
            "publish_requirement": material.get("publish_requirement"),
            "notes": material.get("notes"),
        }
        result["missing_fields"] = rules_mod.missing_required_fields(material)

        fields = args.get("fields")
        if fields:
            fields = list(fields)
            self.ctx.supplement_calls[material_id] = self.ctx.supplement_calls.get(material_id, 0) + 1
            patch, status = self.ctx.apply_supplement(material_id, fields)
            if status == "supplemented":
                material = self.ctx.effective_material(material_id)
                for field, value in patch.items():
                    result[field] = value
                result["supplement_result"] = {
                    "status": "supplemented",
                    "fields": fields,
                    "detail": f"已通过补查获取字段: {', '.join(patch)}",
                }
            else:
                result["supplement_result"] = {
                    "status": "still_missing",
                    "fields": fields,
                    "detail": "补查未获得任何可用数据，缺失项无法自动消解",
                }
            result["missing_fields"] = rules_mod.missing_required_fields(material)
        return result

    # ---------- check_rules ----------
    def check_rules(self, args: dict[str, Any]) -> dict[str, Any]:
        material_id = args.get("material_id")
        if material_id not in self.ctx.materials:
            raise ToolError(f"素材不存在: {material_id}", {"material_id": material_id})
        supplemented = bool(args.get("supplemented"))
        material = self.ctx.effective_material(material_id)
        evaluation = rules_mod.evaluate_rules(material, self.ctx.rules_cfg)
        evaluation["material_id"] = material_id
        evaluation["supplemented"] = supplemented
        evaluation["supplement_calls"] = self.ctx.supplement_calls.get(material_id, 0)
        if supplemented and evaluation["missing_fields"]:
            evaluation["next_hint"] = "补查后仍缺失，需人工补齐，不得排期"
        return evaluation

    # ---------- query_slots ----------
    def query_slots(self, args: dict[str, Any]) -> dict[str, Any]:
        date = args.get("date")
        if not date:
            raise ToolError("缺少参数 date", {"required": ["date"]})
        occupied = self.ctx.slots_cfg.get("occupied", [])
        window = self.ctx.rules_cfg.get("publish_window", {"start": "08:00", "end": "22:00"})
        granularity = self.ctx.slots_cfg.get("conflict_granularity", "hour")
        duration = int(args.get("duration_sec") or 0)
        view = slots_mod.build_slot_view(date, duration, occupied, window, granularity)

        start = args.get("start")
        if start:
            conflicts = slots_mod.find_conflicts(date, start, duration, occupied, granularity)
            view["requested_start"] = start
            view["requested_conflicts"] = conflicts
            view["requested_available"] = not conflicts
        return view

    # ---------- save_draft ----------
    def save_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        material_id = args.get("material_id")
        date = args.get("date")
        start = args.get("start")
        if not material_id or not date or not start:
            raise ToolError(
                "缺少必要参数: material_id / date / start",
                {"required": ["material_id", "date", "start"]},
            )
        if material_id not in self.ctx.materials:
            raise ToolError(f"素材不存在: {material_id}", {"material_id": material_id})

        # 故障注入：真实失败路径必须可复现
        injected = self.ctx.simulated_failures.get(material_id, {}).get("save_draft")
        if injected:
            raise ToolError(f"保存草稿失败: {injected}", {"material_id": material_id, "injected": True})

        material = self.ctx.effective_material(material_id)
        channel = args.get("channel")
        if not channel:
            channels = material.get("channels") or []
            channel = channels[0] if channels else None

        chash = content_hash(material)
        request_key = build_request_key(material_id, date, start, channel, chash)
        draft_id = f"D-{material_id}-{date.replace('-', '')}-{start.replace(':', '')}"
        record = {
            "draft_id": draft_id,
            "request_key": request_key,
            "material_id": material_id,
            "date": date,
            "start_time": start,
            "channel": channel,
            "risk_level": args.get("risk_level"),
            "status": "draft",
            "content_hash": chash,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "payload": {
                "note": args.get("note"),
                "published": False,
                "kind": "publish_schedule_draft",
            },
        }
        result = self.ctx.store.save_draft(record)
        result["published"] = False
        result["material_id"] = material_id
        result["date"] = date
        result["start"] = start
        return result
