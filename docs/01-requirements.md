# 01 需求说明（用户、目标、范围与成功标准）

## 1. 用户与目标

**用户**：内容团队的素材运营人员。他们手上有一批待上线素材，每个素材带有明确的
发布时间要求，需要在上线前完成审核与排期。

**目标**：让用户**找出待处理素材**，形成**无冲突、可确认**的发布排期。

具体拆成四件可验收的事：

1. **看清素材状态** —— 每个素材的审核状态、风险原因、建议时段、待确认项、草稿保存状态。
2. **补齐信息缺口** —— 素材信息缺失时主动补查，而不是用臆测值排期。
3. **避开时段冲突** —— 时段被占用时给出替代建议，但**不擅自更改**用户的硬性要求。
4. **保证幂等与诚实** —— 相同请求重复保存只产生一份草稿；保存失败必须明确返回失败。

## 2. 输入与材料

| 材料 | 来源 | 内容 |
|---|---|---|
| 素材 ID 与元数据 | `data/materials.json` | 标题、类型、时长、文案、敏感标签、复核结论、授权凭证、投放渠道 |
| 发布时间要求 | 素材内 `publish_requirement` | 日期、时刻、是否硬性（`hard`） |
| 已有排期 | `data/slots.json` | 已占用的日期/时段/时长/渠道 |
| 审核规则 | `data/rules.json` | R-AUTH / R-DUR / R-SENS / R-WORD / R-CHAN 及级别、消息、补查提示 |
| 用户输入 | CLI `--request` | 自然语言排期请求 |

工具层提供四个工具：**素材读取、规则检查、时段查询、排期草稿保存**。

## 3. 范围

### 在范围内

- 单素材的审核与排期（CLI 入口）
- ReAct 决策循环：模型选择动作 → 执行工具 → 观察结果 → 模型调整下一步或结束
- 审核状态、风险原因、建议时段、待确认项、草稿保存状态的完整输出
- 离线 Mock 决策通道（无密钥可运行）与真实模型通道（OpenAI 兼容）
- SQLite 草稿库与幂等保证
- Trace 记录（步骤 / 工具参数 / 返回摘要 / 后续动作 / 决策依据）

### 明确不在范围内

- **真实发布**。系统只产出排期草稿，没有任何发布通道。
- 界面、线上部署、多 Agent、生产级 Runtime（题目明示非必需）。
- 多用户隔离与鉴权。
- 真实业务数据（素材为合成数据）。

## 4. 成功标准

### 4.1 必须验收的六项（题目明示）

| 验收点 | 判定标准 | 对应用例 |
|---|---|---|
| **正常排期** | 无风险无冲突 → 给出建议时段并等待确认 | `normal_ok` |
| **素材信息缺失** | 补查后仍缺失 → 阻断，且明确列出缺失字段 | `missing_info` |
| **规则风险** | 敏感标签/禁用词 → 阻断并说明风险原因 | `rule_risk` |
| **时段冲突** | 目标时段被占用 → 给替代时段 + 标记待确认，**不落库** | `normal_conflict` |
| **重复提交** | 相同请求重复保存 → 草稿表只有一份 | `duplicate_submit_twice` |
| **保存失败** | 写入故障 → 返回 `failed`，`draft_id` 为空，**不谎报成功** | `save_failure` |

### 4.2 参考实例的逐条对应

> 「素材 M01 无风险，用户要求 18:00，但该时段已占用、19:00 可用。模型建议 19:00
> 并标记待确认；不得称为已发布。相同请求重复保存只产生一份草稿；保存失败须明确返回失败。」

| 参考实例要求 | 本项目实现 | 验证方式 |
|---|---|---|
| M01 无风险 | `risk_level=none`，`risks=[]` | `test_case_outcome` |
| 18:00 已占用 | `query_slots` 返回 `requested_conflicts`（M02 占用） | `test_conflict_case_does_not_write_draft` |
| 19:00 可用 | `suggested_slot=19:00` | `test_case_outcome` |
| 标记待确认 | `pending_confirmations[0].type=change_publish_time` | `test_conflict_case_does_not_write_draft` |
| 不得称为已发布 | `published=false`，且 `payload.published=false` | `test_case_never_publishes` |
| 重复保存只产生一份 | 第二次返回 `duplicate`，草稿表 1 行 | `test_duplicate_submit_twice_creates_single_draft` |
| 保存失败明确返回失败 | `outcome=failed`、`draft_id=None` | `test_save_failure_is_reported_honestly` |

### 4.3 关键缺陷（题目明示为「关键缺陷」）

> 「越权发布或虚报保存成功属于关键缺陷。」

这两项在本项目中都有**代码层强制**，不依赖模型自觉：

| 关键缺陷 | 代码层防线 | 对应测试 |
|---|---|---|
| 越权发布 | ①工具集无发布工具 ②`payload.published` 恒为 false ③`_finalize` 强制 `published=False` | `test_case_never_publishes`、`test_finalize_cannot_mark_published` |
| 虚报保存成功 | ①`save_draft` 失败抛 `ToolError` ②缺 `draft_id` 时改判 `failed` | `test_save_failure_is_reported_honestly`、`test_saved_without_draft_id_becomes_failed` |

### 4.4 高分依据（题目明示：冲突调整、确认边界、防重复）

| 高分依据 | 实现 | 验证 |
|---|---|---|
| **冲突调整** | 冲突时按同一粒度检索最近可用时段作为建议 | `normal_conflict` → 19:00 |
| **确认边界** | 未经确认一律不落库；冲突时只产出待确认项 | 全部 `needs_confirmation` 用例草稿数为 0 |
| **防重复** | `request_key` 幂等键 + `UNIQUE(material_id, date, start_time)` 数据库约束 | `duplicate_submit_twice` 草稿表 1 行 |

### 4.5 工程要求

- 运行方式三选一 → **CLI**（HTTP API / Notebook 非必需）
- 步数上限 → `MAX_TOOL_CALLS` 默认 8，达到返回 `outcome=incomplete`
- Mock 边界 → 文档明示「只验证流程，不证明真实模型决策效果」
- 无真实密钥入库 → 仅提交 `.env.example`

## 5. 验收流程（可复制的命令）

```bash
# 1. 环境：Python 3.10+，运行时零第三方依赖
pip install -r requirements.txt

# 2. 跑全部业务用例（7 个覆盖全部验收点）
python -m src.cli --case all

# 3. 跑测试套件
python -m pytest tests -q

# 4. 查看草稿库（重复提交后应只有 1 行）
python -m src.cli --list-drafts

# 5. 生成运行记录（步骤/工具参数/返回摘要/后续动作/决策依据）
python -m src.cli --case all --trace-dir examples/traces
```

详细设计与实现见 [02-solution.md](02-solution.md)，评测结果见 [03-evaluation.md](03-evaluation.md)。
