# 02 工具与数据契约

## 1. 工具清单

工具层**只做事实获取与受控写入**，不含任何决策逻辑。决策全部由模型给出。

| 工具 | 作用 | 失败方式 |
|---|---|---|
| `read_material` | 读取素材元数据；带 `fields` 时执行补查 | 素材不存在 → `ToolError` |
| `check_rules` | 规则检查，返回风险等级/原因/缺失字段/补查建议 | 素材不存在 → `ToolError` |
| `query_slots` | 查询指定日期的占用与可用时段 | 缺 `date` → `ToolError` |
| `save_draft` | 保存排期草稿（幂等） | 缺参数/素材不存在/写入故障 → `ToolError` |

**没有 `publish`、`push`、`release` 之类的工具。** 排期草稿与真实发布是两件事。

## 2. 契约详情

### read_material

```jsonc
// 请求
{"material_id": "M03", "fields": ["duration_sec", "authorization"]}  // fields 可选

// 响应
{
  "material_id": "M03",
  "title": "社团纳新海报",
  "content_type": "image",
  "duration_sec": null,
  "copy_text": "社团纳新，欢迎加入。",
  "sensitivity_tags": [],
  "review_notes": null,
  "authorization": null,
  "channels": ["公众号"],
  "publish_requirement": {...},
  "notes": "...",
  "missing_fields": ["duration_sec", "authorization"],   // 统一口径
  "supplement_result": {                                // 仅在传了 fields 时出现
    "status": "supplemented" | "still_missing",
    "fields": [...],
    "detail": "..."
  }
}
```

### check_rules

```jsonc
// 请求
{"material_id": "M04", "supplemented": false}

// 响应
{
  "risk_level": "none" | "low" | "medium" | "high",
  "risks": [
    {"rule_id": "R-AUTH", "name": "授权凭证校验", "severity": "high",
     "reason": "...", "detail": "...", "supplement_hint": "..."}
  ],
  "missing_fields": [...],       // 素材缺失字段（统一口径）
  "supplement_fields": [...],    // 可补查的字段
  "banned_words_hit": ["国家级"],
  "unresolved_high": ["R-AUTH"],   // 补查后仍无法消解 -> 必须阻断
  "unresolved_medium": [],
  "need_supplement": true,
  "auto_clearable": false,
  "supplemented": false,
  "supplement_calls": 0,
  "next_hint": "补查后仍缺失，需人工补齐，不得排期"   // 仅在仍缺失时出现
}
```

### query_slots

```jsonc
// 请求
{"date": "2026-11-02", "start": "18:00", "duration_sec": 60}

// 响应
{
  "date": "2026-11-02",
  "granularity": "hour",
  "publish_window": {"start": "08:00", "end": "22:00"},
  "occupied": [{"id": "S-M02-EVENING", "material_id": "M02", "start": "18:00", ...}],
  "available_slots": ["08:00", "08:30", ..., "19:00", ...],
  "occupied_hours": ["12:00", "18:00"],
  "requested_start": "18:00",        // 仅在传了 start 时出现
  "requested_conflicts": [...],      // 仅在传了 start 时出现
  "requested_available": false       // 仅在传了 start 时出现
}
```

### save_draft

```jsonc
// 请求
{"material_id": "M01", "date": "2026-11-02", "start": "19:00",
 "channel": "视频号", "risk_level": "none", "note": null}

// 响应
{"status": "saved" | "duplicate", "draft_id": "D-M01-20261102-1900",
 "request_key": "...", "detail": "...", "published": false,
 "material_id": "M01", "date": "2026-11-02", "start": "19:00"}
```

失败时抛 `ToolError`，携带 `message` 与 `detail`。

## 3. 规则表

配置在 `data/rules.json`。

| 规则 ID | 名称 | 级别 | 触发条件 | 可自动消解 |
|---|---|---|---|---|
| R-AUTH | 授权凭证校验 | high | 授权缺失或 licensed/license_id/valid_until 不全 | 可（补查授权） |
| R-DUR | 时长/规格完整性 | medium | `duration_sec` 为 None 或负数 | 可（补查时长） |
| R-SENS | 敏感标签复核 | high | 存在 `sensitivity_tags` | 可（补查复核结论） |
| R-WORD | 禁用词检查 | medium | 文案命中禁用词，或文案缺失 | **命中禁用词不可**（需人工改写） |
| R-CHAN | 渠道匹配 | low | `channels` 为空 | 可（补查渠道） |

风险等级取所有命中规则的最高级别。`unresolved_high` / `unresolved_medium`
是**补查后仍无法自动消解**的规则——它们是阻断的依据。

## 4. 幂等键

```
request_key = material_id | date | start | channel | content_hash
```

- `content_hash` = 素材内容 JSON（键序无关）的 SHA256 前 16 位
- 素材内容变化 → content_hash 变化 → request_key 变化
- 但 `UNIQUE(material_id, date, start_time)` 仍会拦截同一排期的重复写入

两者结合，保证「同一素材同一排期只存在一份草稿」，同时不阻止内容更新后的合法重排。

## 5. 数据库结构

```sql
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id      TEXT UNIQUE NOT NULL,
    request_key   TEXT UNIQUE NOT NULL,
    material_id   TEXT NOT NULL,
    date          TEXT NOT NULL,
    start_time    TEXT NOT NULL,
    channel       TEXT,
    risk_level    TEXT,
    status        TEXT NOT NULL,
    payload       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (material_id, date, start_time)
);
```

`status` 取值 `draft`（本项目唯一会写入的值）；`payload.published` 恒为 `false`。

## 6. 故障注入

`data/materials.json` 的 `simulated_failures` 段用于复现失败路径：

```jsonc
{
  "simulated_failures": {
    "M06": {
      "save_draft": "数据库写入失败：disk I/O error（注入故障，用于验证保存失败场景）"
    }
  }
}
```

| 键 | 效果 |
|---|---|
| `save_draft` | `save_draft` 工具抛 `ToolError` |
| `supplement` | 提供补查字段（dict），使补查成功 |

故障注入的意义：失败路径必须**可复现、可测试**，而不是靠运气碰到。
