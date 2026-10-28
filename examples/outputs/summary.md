# 实际输出汇总

> 由 `python -m scripts.gen_outputs` 自动生成，与代码保持同步。

| 用例 | 期望 | 实际 outcome | 实际 draft_status | 建议时段 | published | 草稿数 |
|---|---|---|---|---|---|---|
| `normal_conflict` | 18:00 被 M02 占用，应给出 19:00 建议并标记待确认，不落库 | `needs_confirmation` | `not_saved` | 2026-11-02 19:00 | `False` | 0 |
| `normal_ok` | 无风险无冲突，建议 10:00，等待确认 | `needs_confirmation` | `not_saved` | 2026-11-02 10:00 | `False` | 0 |
| `missing_info` | 时长与授权缺失，补查后仍缺失，阻断且不落库 | `blocked` | `not_saved` | 2026-11-03 - | `False` | 0 |
| `rule_risk` | 敏感标签与禁用词风险，补查复核结论后仍为高风险，阻断 | `blocked` | `not_saved` | 2026-11-02 - | `False` | 0 |
| `save_failure` | 保存草稿失败，明确返回 failed，不得谎报成功 | `failed` | `failed` | 2026-11-02 09:00 | `False` | 0 |
| `duplicate_submit` | 第一次保存成功，重复提交同一请求只产生一份草稿 | `saved` | `saved` | 2026-11-02 19:00 | `False` | 1 |
| `duplicate_submit_twice` | 连续两次相同保存请求，草稿表只有一行 | `saved` | `duplicate` | 2026-11-02 19:00 | `False` | 1 |

## 逐条验收点核对

| 验收点 | 用例 | 判定 |
|---|---|---|
| 正常排期 | `normal_ok` | 建议 10:00，等待确认 |
| 素材信息缺失 | `missing_info` | 补查后仍缺失 -> blocked，不落库 |
| 规则风险 | `rule_risk` | 敏感+禁用词 -> blocked |
| 时段冲突 | `normal_conflict` | 18:00 被占 -> 建议 19:00 + 待确认，不落库 |
| 重复提交 | `duplicate_submit_twice` | 第二次 duplicate，草稿表 1 行 |
| 保存失败 | `save_failure` | failed，draft_id=null |

## 草稿库快照

全部用例跑完后草稿库共 **2** 行：

```json
[
  {
    "id": 1,
    "draft_id": "D-M01-20261102-1900",
    "request_key": "M01|2026-11-02|19:00|视频号|b0c3524006cc442b",
    "material_id": "M01",
    "date": "2026-11-02",
    "start_time": "19:00",
    "channel": "视频号",
    "risk_level": "none",
    "status": "draft",
    "payload": "{\"note\": null, \"published\": false, \"kind\": \"publish_schedule_draft\"}",
    "content_hash": "b0c3524006cc442b",
    "created_at": "2026-10-28T11:52:58+00:00"
  },
  {
    "id": 1,
    "draft_id": "D-M01-20261102-1900",
    "request_key": "M01|2026-11-02|19:00|视频号|b0c3524006cc442b",
    "material_id": "M01",
    "date": "2026-11-02",
    "start_time": "19:00",
    "channel": "视频号",
    "risk_level": "none",
    "status": "draft",
    "payload": "{\"note\": null, \"published\": false, \"kind\": \"publish_schedule_draft\"}",
    "content_hash": "b0c3524006cc442b",
    "created_at": "2026-10-28T11:52:58+00:00"
  }
]
```
