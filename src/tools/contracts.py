"""四个业务工具的契约（schema）。

工具清单：
- read_material   读取素材元数据（可带 fields 过滤，用于「补查」）
- check_rules     规则检查，返回风险等级与原因
- query_slots     时段查询，返回占用与可用时段
- save_draft      保存排期草稿（幂等；失败抛出）

注意：工具集中**没有** publish / publish_now 之类真实发布能力。
"""

from __future__ import annotations

TOOL_SPECS: list[dict] = [
    {
        "name": "read_material",
        "description": "读取素材元数据。可传 fields 只返回指定字段（用于补查缺失信息）。",
        "arguments": {
            "material_id": "素材 ID，如 M01",
            "fields": "可选，字符串数组，指定需要补查的字段列表",
        },
    },
    {
        "name": "check_rules",
        "description": "对素材执行审核规则检查，返回风险等级、风险原因、缺失字段与补查建议。",
        "arguments": {
            "material_id": "素材 ID",
            "supplemented": "可选，布尔值，表示模型是否已完成补查",
        },
    },
    {
        "name": "query_slots",
        "description": "查询指定日期的时段占用情况与可用时段。",
        "arguments": {
            "date": "日期 YYYY-MM-DD",
            "start": "可选，目标开始时间 HH:MM",
            "duration_sec": "可选，素材时长（秒）",
        },
    },
    {
        "name": "save_draft",
        "description": "保存排期草稿（仅草稿，不发布）。相同请求幂等，只保留一份。",
        "arguments": {
            "material_id": "素材 ID",
            "date": "日期 YYYY-MM-DD",
            "start": "开始时间 HH:MM",
            "channel": "可选，投放渠道",
            "note": "可选，备注",
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_SPECS]
