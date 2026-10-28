# 03 评测报告（测试预期、实际结果、失败与限制）

> 本文档中所有「实际结果」均由 `python -m scripts.gen_outputs` 与
> `python -m pytest tests -q` 自动生成，与仓库代码保持同步。
> 对应文件：[examples/outputs/summary.md](../examples/outputs/summary.md)、
> [examples/outputs/*.json](../examples/outputs)、
> [examples/traces/*.jsonl](../examples/traces)。

## 1. 评测目标

本项目要验证的不是「模型能不能排期」，而是**流程与工具契约在各种边界条件下是否正确**。
具体回答五个问题：

1. 信息不足时，系统会不会**臆测**缺失字段的值去排期？
2. 时段冲突时，系统会不会**擅自更改**用户的硬性时间要求？
3. 写入失败时，系统会不会**谎报成功**？
4. 重复提交时，系统会不会**产生多份草稿**？
5. 模型的**下一步动作**是否真的由上一步的**工具观测结果**驱动？

第 5 点对应题目对 ReAct 的硬性要求，单独由 `tests/test_react.py` 覆盖（见第 5 节）。

## 2. 测试执行结果

```
$ python -m pytest tests -q
........................................................................ [ 69%]
...............................                                          [100%]
```

**103 个测试全部通过，0 失败。**

| 测试文件 | 数量 | 覆盖内容 |
|---|---|---|
| `tests/test_rules.py` | 18 | 规则引擎、缺失字段口径、风险等级、消解判定 |
| `tests/test_slots.py` | 11 | 时段冲突判定、可用时段检索、发布窗口 |
| `tests/test_storage.py` | 9 | 幂等、唯一约束、失败回滚、草稿读写、**连接不泄漏** |
| `tests/test_tools.py` | 15 | 工具契约、错误暴露、补查回写、故障注入 |
| `tests/test_cases.py` | 20 | 端到端用例断言（对应 6 项必须验收） |
| `tests/test_loop.py` | 8 | 循环结构、步数上限、越权保护、trace 完整性 |
| `tests/test_react.py` | 15 | **观测驱动决策证明**、多条执行路径、上限未完成状态 |
| `tests/test_cli.py` | 7 | CLI 参数、退出码、输出格式 |

## 3. 必须验收的六项 —— 预期 vs 实际

数据来源：[examples/outputs/summary.md](../examples/outputs/summary.md)（自动生成）。

| # | 验收点 | 用例 | 预期 | **实际结果** | 判定 |
|---|---|---|---|---|---|
| 1 | **正常排期** | `normal_ok` | 无风险无冲突 → 建议时段并等待确认 | `outcome=needs_confirmation`，`draft_status=not_saved`，建议 `2026-11-02 10:00`，`published=false` | ✅ |
| 2 | **素材信息缺失** | `missing_info` | 补查后仍缺失 → 阻断并列明缺失字段 | `outcome=blocked`，`draft_status=not_saved`，`risk_level=high`，列出缺失字段 | ✅ |
| 3 | **规则风险** | `rule_risk` | 敏感标签/禁用词 → 阻断并说明原因 | `outcome=blocked`，`risk_level=high`，risks 含 R-SENS / R-WORD | ✅ |
| 4 | **时段冲突** | `normal_conflict` | 18:00 被占 → 建议 19:00 + 待确认，**不落库** | `outcome=needs_confirmation`，`suggested_slot=19:00`，`pending_confirmations=[change_publish_time]`，草稿数 **0** | ✅ |
| 5 | **重复提交** | `duplicate_submit_twice` | 重复保存只产生一份草稿 | 第 1 次 `saved`，第 2 次 `duplicate`，草稿表 **1 行** | ✅ |
| 6 | **保存失败** | `save_failure` | 返回 `failed`，`draft_id` 为空 | `outcome=failed`，`draft_status=failed`，`draft_id=null`，草稿数 **0** | ✅ |

全部用例的 `published` 均为 `false` —— **没有任何一个用例声称已发布**。

### 3.1 参考实例逐条对照

> 「素材 M01 无风险，用户要求 18:00，但该时段已占用、19:00 可用。模型建议 19:00
> 并标记待确认；不得称为已发布。相同请求重复保存只产生一份草稿；保存失败须明确返回失败。」

| 参考实例要求 | 实际输出 | 验证文件 |
|---|---|---|
| M01 无风险 | `risk_level=none`，`risks=[]` | `examples/outputs/normal_conflict.json` |
| 18:00 已占用 | 观测 `requested_conflicts` 命中 M02 | `examples/traces/normal_conflict.jsonl` 第 3 步 |
| 19:00 可用并被建议 | `suggested_slot="19:00"` | `examples/outputs/normal_conflict.json` |
| 标记待确认 | `pending_confirmations[0].type="change_publish_time"` | 同上 |
| 不得称为已发布 | `published=false` | 所有 outputs |
| 重复保存只产生一份 | 第二次 `draft_status=duplicate`，草稿表 1 行 | `examples/outputs/duplicate_submit_twice.json` |
| 保存失败明确返回失败 | `outcome=failed`，`draft_id=null` | `examples/outputs/save_failure.json` |

## 4. 三组以上输入 / 实际输出 / 运行记录

题目要求「至少提交 3 组输入、实际输出及运行记录，覆盖正常、信息或资料不足、工具失败场景」。
本项目提交 **7 组**，三类场景全覆盖。

| 场景类别 | 用例 | 输入 | 实际输出 | 运行记录 |
|---|---|---|---|---|
| **正常** | `normal_ok` | `examples/inputs/cases.json` | `examples/outputs/normal_ok.json` | `examples/traces/normal_ok.jsonl` |
| **正常+冲突** | `normal_conflict` | 同上 | `examples/outputs/normal_conflict.json` | `examples/traces/normal_conflict.jsonl` |
| **信息不足** | `missing_info` | 同上 | `examples/outputs/missing_info.json` | `examples/traces/missing_info.jsonl` |
| **规则风险** | `rule_risk` | 同上 | `examples/outputs/rule_risk.json` | `examples/traces/rule_risk.jsonl` |
| **工具失败** | `save_failure` | 同上 | `examples/outputs/save_failure.json` | `examples/traces/save_failure.jsonl` |
| **重复提交** | `duplicate_submit_twice` | 同上 | `examples/outputs/duplicate_submit_twice.json` | `examples/traces/duplicate_submit_twice.jsonl` |
| **重复提交** | `duplicate_submit` | 同上 | `examples/outputs/duplicate_submit.json` | `examples/traces/duplicate_submit.jsonl` |

输入统一在 [examples/inputs/cases.json](../examples/inputs/cases.json)，
每条含 `command` 字段，可直接复制复现。

### 4.1 运行记录格式

每条 trace 记录含题目要求的五个字段：

```jsonc
{
  "step": 3,                    // 步骤
  "action": "tool",
  "tool": "query_slots",
  "args": {"date": "2026-11-02", "start": "18:00", "duration_sec": 60},  // 工具参数
  "result_summary": "{...}",    // 返回摘要
  "next_action": "由模型根据观测决定下一步",  // 后续动作
  "rationale": "...",           // 简要决策依据
  "decided_by": "observation.unresolved_high:empty",  // 决策所依据的观测字段
  "tool_error": null
}
```

`decided_by` 是本项目额外添加的字段，用来**显式标注「这一步的决策由哪个观测驱动」**，
让「动态决策」可被直接核对，而不只是一句声明。

## 5. ReAct 决策要求的验证（关键）

题目明示：「仅有固定条件分支、自动重试或预设决策日志，不视为完成 ReAct 决策要求。」

### 5.1 循环结构

`src/agent/loop.py` 实现的是完整的三段式循环：

```
① 模型选择动作  action = client.decide(messages, tool_specs)
② 执行工具      observation = registry.call(action.tool, action.args)
③ 观察结果并调整 observation 回灌 messages -> 下一轮 decide() 读新状态
                或 action.action == "finish" 时结束
```

模型不持有对象，只能通过消息看到世界；每次决策时 `_build_messages` 把当前 `state`
（上次用了什么工具、拿到什么观测、还缺什么）序列化后交给模型。
**下一步动作由模型依据观测产出，不是脚本按步号推进。**

### 5.2 「不同工具结果 → 不同动作」的可执行证明

`tests/test_react.py` 用断言而非叙述来证明：

| 测试 | 证明内容 |
|---|---|
| `test_loop_is_model_driven_not_scripted` | 每步决策都标注了 `decided_by`，且后续步骤的 `decided_by` **互不相同** —— 证明决策随观测变化，而非固定分支 |
| `test_observation_changes_next_action` | 同一素材 M01，18:00 被占 vs 20:00 空闲 → **动作序列不同**：冲突路径不含 `save_draft`，空闲路径含 `save_draft` |
| `test_observation_changes_next_action_on_risk` | M03（缺失字段）步骤数 **>** M05（字段完整），且缺失场景触发了带 `fields` 的补查调用 |
| `test_supplement_result_changes_rule_evaluation` | 补查前后 `check_rules` 的结论**确实改变**：补到时长后 `R-DUR` 从 `unresolved_medium` 消失 |
| `test_tool_call_limit_returns_incomplete` | 上限触发返回 `outcome=incomplete` + `incomplete_reason=max_tool_calls_exceeded`，**不静默截断** |

### 5.3 两条执行路径的实际对照

**路径 A —— 时段冲突**（`examples/traces/normal_conflict.jsonl`）：

| 步骤 | 工具 | 观测 | 决策依据 `decided_by` | 后续动作 |
|---|---|---|---|---|
| 1 | `read_material` | 素材 M01，`missing_fields=[]` | `state.step:1` | 读素材 |
| 2 | `check_rules` | `risk_level=none`，`unresolved_high=[]` | `observation.missing_fields:empty` | 转规则检查 |
| 3 | `query_slots` | `requested_conflicts=[M02@18:00]`，`available=[19:00]` | `observation.unresolved_high:empty` | 进时段查询 |
| 4 | **finish** | — | **`observation.requested_conflicts`** | 建议 19:00 + 待确认，**不落库** |

**路径 B —— 素材信息缺失**（`examples/traces/missing_info.jsonl`）：

| 步骤 | 工具 | 观测 | 决策依据 `decided_by` | 后续动作 |
|---|---|---|---|---|
| 1 | `read_material` | `missing_fields=[duration_sec, authorization]` | `state.step:1(无观测...)` | 读素材 |
| 2 | `read_material(fields=[...])` | `supplement_result=still_missing` | **`observation.missing_fields`** | **补查**（路径 A 在此处已进入 check_rules） |
| 3 | `check_rules` | `need_supplement=true` | `observation.missing_fields:empty` | 规则检查 |
| 4 | `check_rules(supplemented=True)` | `unresolved_high` 仍存在 | `observation.need_supplement+state.supplement_done` | 重新评估 |
| 5 | **finish** | — | **`observation.unresolved_high`** | 阻断，**不落库** |

两条路径在**第 2 步就分叉**，且分叉原因都是**工具的真实返回值**。

### 5.3.1 最强证据：完全相同的请求，不同的工具结果 → 不同的决策

`examples/traces/duplicate_submit_twice.jsonl` 记录了**同一套请求连续执行两次**，
前 4 步的动作序列**完全相同**，但第 4 步 `save_draft` 的观测不同，
导致第 5 步的决策随之不同：

| | 第 1 次执行 | 第 2 次执行 |
|---|---|---|
| 步骤 1-3 | `read_material` → `check_rules` → `query_slots` | 完全相同 |
| 步骤 4 | `save_draft` | `save_draft`（相同参数） |
| 步骤 4 观测 | `status="saved"`，返回 `draft_id` | `status="duplicate"`，返回已有 `draft_id` |
| 步骤 5 决策依据 | `observation.status:saved` | `observation.status:duplicate` |
| 步骤 5 决策 | `draft_status="saved"` | `draft_status="duplicate"`（**不重复创建**） |

这直接证明了：**同样的输入、同样的动作序列，仅因工具返回的观测不同，
模型给出的下一步决策就不同。** 这正是题目要求的「模型根据不同工具结果调整动作」。

对照表见 [examples/outputs/duplicate_submit_twice.json](../examples/outputs/duplicate_submit_twice.json)：
两次运行的 `outcome` 均为 `saved`，但 `draft_status` 分别是 `saved` 与 `duplicate`，
草稿表最终只有 **1 行**。

### 5.4 步数上限

`MAX_TOOL_CALLS` 默认 8（`src/config.py`，可用 `.env` 覆盖）。
达到上限返回：

```json
{
  "outcome": "incomplete",
  "draft_status": "not_saved",
  "incomplete_reason": "max_tool_calls_exceeded",
  "reasoning": "执行未完成：max_tool_calls_exceeded（已用工具调用 8 次）",
  "tool_calls": 8,
  "max_tool_calls": 8
}
```

其他未完成原因：`model_error:<异常>`、`模型给出空工具名`。

## 6. 关键缺陷的防护验证

题目明示：「越权发布或虚报保存成功属于关键缺陷。」这两项都有**代码层强制**：

| 关键缺陷 | 防护机制 | 测试 |
|---|---|---|
| **越权发布** | ① 工具集无发布工具 ② `payload.published` 恒 false ③ `_finalize` 无条件 `final["published"] = False` | `test_case_never_publishes`、`test_finalize_cannot_mark_published` |
| **虚报保存成功** | ① `save_draft` 失败抛 `ToolError` ② 缺 `draft_id` 时改判 `failed` | `test_save_failure_is_reported_honestly`、`test_saved_without_draft_id_becomes_failed` |
| **擅自改硬性要求** | 冲突路径不产出 `save_draft` 调用，只产出待确认项 | `test_conflict_case_does_not_write_draft` |
| **重复草稿** | 应用层 `request_key` + 数据库 `UNIQUE(material_id, date, start_time)` | `test_duplicate_submit_twice_creates_single_draft` |

其中「模型试图声称已发布但被代码改写」这一条，测试直接构造
`final={"draft_status": "saved", "published": True}` 输入，断言输出 `published` 仍为 `false`。

## 7. 失败与限制（诚实披露）

### 7.1 离线 Mock 的能力边界（**最重要的一条**）

> **离线 Mock 只验证流程与工具契约，不证明真实模型的决策效果。**

`src/llm/offline_mock.py` 是**观测驱动的确定性策略引擎**：它读取上一步工具的真实
返回值（`occupied` / `risks` / `missing_fields` / `status`）来决定下一步，
因此当工具结果不同时给出的动作序列确实不同。

但它**不是大模型**：
- 不做自然语言理解，请求解析用的是确定性正则（`domain/parsing.py`）
- 决策空间被预先枚举，不具备真实模型的泛化与涌现能力
- 它的「决策依据」是硬编码的字段名，而非对语义的理解

**因此本报告第 3 节的 6 项验收结论，应理解为「流程正确、契约正确」，
而非「模型决策质量达标」。** 真实模型的决策质量需要用真实模型通道另行评测（见第 7.3 节）。

### 7.2 其他限制

| 限制 | 说明 | 影响 |
|---|---|---|
| **请求解析是规则式** | 日期/时刻/素材 ID/确认意图由正则抽取 | 自由表述的请求可能解析不到，CLI 会返回明确错误而非猜测 |
| **补查数据是模拟的** | 补查源为 `simulated_failures` 或素材 `notes` | 真实场景需接入素材库/审核系统的补查接口 |
| **时段冲突按小时粒度** | 同一自然小时内有投放即视为冲突 | 比真实投放系统的判定略粗，配置在 `data/slots.json` 的 `conflict_granularity` |
| **无鉴权与多用户隔离** | 单用户本地工具 | 不适用于多人并发使用 |
| **SQLite 单机存储** | 无并发写优化 | 不适用于高并发写入 |
| **保存失败靠故障注入** | 用 `simulated_failures` 注入写失败 | 真实故障（磁盘满、锁）行为应一致，因为都走 `ToolError` 路径 |
| **没有真实模型的效果评测** | 未跑真实模型通道的对比实验 | 见 7.3 |

### 7.3 关于真实模型通道

`src/llm/openai_compat.py` 实现了 OpenAI 兼容的真实模型通道，
需在 `.env` 中配置 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。

**本次交付的评测结果全部来自离线 Mock 通道**（环境无密钥，且题目明确 Mock
「不证明真实模型决策效果」）。切换到真实模型后：
- 工具契约、输出字段结构、越权保护、幂等保证**完全不变**（这些都在代码层）
- 变化的是**决策质量**：是否总能选到正确的下一步、理由是否贴切

复现真实模型通道：

```bash
# .env 中配置密钥后
python -m src.cli --case all --trace-dir examples/traces
```

## 8. 复现命令

```bash
pip install -r requirements.txt

# 全部业务用例（含 6 项验收点）
python -m src.cli --case all

# 重新生成实际输出与运行记录
python -m scripts.gen_outputs

# 全部测试
python -m pytest tests -q

# 检查草稿库（重复提交后应为 1 行）
python -m src.cli --list-drafts
```
