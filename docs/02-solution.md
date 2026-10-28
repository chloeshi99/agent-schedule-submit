# 02 方案设计（调用流程、动态决策、技术取舍）

## 1. 总体架构

```
user_request
     │
     v
┌──────────────────────────────────────────────────────┐
│  src/runner.py  装配层                                │
│  解析请求字面量 → 建工具上下文 → 建 Agent → 跑循环     │
└───────────────────────┬──────────────────────────────┘
                        │
                        v
┌──────────────────────────────────────────────────────┐
│  src/agent/loop.py  ReAct 循环                        │
│                                                      │
│   state ──> _build_messages ──> LLMClient.decide     │
│                                     │                │
│                        action=tool ──┤── action=finish│
│                                     v                v
│                          ToolRegistry.call()    finalize + 越权保护
│                                     │                │
│                          observation 回灌 ───────────┘
└───────────────────────┬──────────────────────────────┘
                        │
     ┌──────────────────┼──────────────────┐
     v                  v                  v
┌─────────┐      ┌──────────┐      ┌──────────────┐
│ llm/    │      │ tools/   │      │ domain/      │
│ 决策层   │      │ 工具层   │      │ 规则/时段    │
│         │      │ 只做事实  │      │ 确定性计算    │
└─────────┘      └────┬─────┘      └──────────────┘
                      v
              ┌──────────────┐
              │ storage/     │
              │ SQLite 草稿库 │
              └──────────────┘
```

### 分层职责

| 层 | 位置 | 职责 | 明确不做 |
|---|---|---|---|
| 循环层 | `src/agent/loop.py` | 编排模型与工具、步数上限、trace、越权保护 | 不理解业务语义 |
| 决策层 | `src/llm/` | **决定下一步做什么** | 不直接读写数据 |
| 工具层 | `src/tools/impl.py` | 提供事实、暴露失败、受控写入 | **不做任何决策** |
| 领域层 | `src/domain/` | 规则判定、时段冲突判定、请求解析 | 不涉及 I/O |
| 存储层 | `src/storage/db.py` | 草稿持久化、幂等保证 | 不含业务判断 |
| 装配层 | `src/runner.py` | 组装各层，统一入口 | 不含业务判断 |

**核心分工**：把**可验证的计算**（规则判定、时段冲突、幂等）交给确定性代码，
把**需要判断的部分**（何时补查、补查什么、冲突后建议哪个时段、失败后是否重试）
交给模型。这样业务规则可测试、可回归，模型幻觉不会污染数据。

## 2. ReAct 决策循环

### 2.1 循环骨架

```python
while True:
    if tool_calls >= max_tool_calls:
        return incomplete()                     # 步数上限 -> 明确未完成状态

    action = client.decide(messages, TOOL_SPECS)   # ① 模型选择动作

    if action.action == "finish":
        return finalize(action)                 # ③ 模型决定结束

    observation = registry.call(action.tool, action.args)   # ② 执行工具
    state = absorb(state, action, observation)   # 观察结果并更新状态
    messages.append(observation)                 # 观察结果回灌给模型
```

三个环节 —— **模型选择动作 → 执行工具 → 观察结果并调整下一步或结束** —— 全部由
`src/agent/loop.py` 实现，与决策层无关。更换决策层（离线 Mock ↔ 真实模型）
不改变循环结构。

### 2.2 状态传递

模型不直接持有对象，只能通过**消息**看到世界。每次决策时，`_build_messages`
把当前 `state` 序列化后作为最后一条 user 消息：

```python
{"step": 3, "last_tool": "check_rules", "observation": {...},
 "material_id": "M01", "target_date": "2026-11-02", "target_start": "18:00",
 "supplement_done": true, "check_rules_calls": 1, "save_attempts": 0,
 "risk_level": "none", "risks": [], "last_error": null, ...}
```

模型据此判断：上一步用了什么工具、拿到了什么观测、还缺什么、下一步该干什么。

### 2.3 步数上限

`MAX_TOOL_CALLS` 默认 8。达到上限时返回明确状态，**不静默截断**：

```json
{
  "outcome": "incomplete",
  "draft_status": "not_saved",
  "reasoning": "执行未完成：max_tool_calls_exceeded（已用工具调用 8 次）",
  "incomplete_reason": "max_tool_calls_exceeded"
}
```

其他未完成原因：`model_error:<异常>`（模型调用失败）、`模型给出空工具名`。

### 2.4 越权保护（代码层，非提示词层）

```
final["published"] = False     # 无条件覆盖，模型说什么都无效
```

外加两道：工具集无发布工具；`save_draft` 写入的 `payload.published` 恒为 `false`。
另有一条：`draft_status == "saved"` 但缺少 `draft_id` → 改判为 `failed`
（保存结果不可信时不能报成功）。

## 3. 动态决策：模型如何根据观测调整

这是本项目的核心。下表是**观测 → 决策**的映射表，每一行都由上一次工具的真实返回值驱动：

| 上一步工具 | 关键观测 | 模型的下一步 | 不同观测下的分歧 |
|---|---|---|---|
| — | 无 | `read_material` | 起点 |
| `read_material` | `missing_fields=[]` | `check_rules` | 字段齐全 → 直接进规则检查 |
| `read_material` | `missing_fields=[duration_sec, authorization]` | `read_material(fields=[...])` | **有缺失 → 先补查** |
| `check_rules` | `need_supplement=true` | `read_material(fields=supplement_fields)` | **需补查 → 去补查** |
| `check_rules` | `need_supplement=false`, `unresolved_high=[]` | `query_slots` | 风险可接受 → 进时段查询 |
| `check_rules` | `unresolved_high=["R-AUTH"]` | **finish(blocked)** | **未消解 → 阻断** |
| `query_slots` | `requested_conflicts=[M02]` | **finish(needs_confirmation)** + 替代时段 | **冲突 → 给替代建议** |
| `query_slots` | `requested_conflicts=[]`, 未确认 | finish(needs_confirmation) | 空闲但未确认 → 只建议 |
| `query_slots` | `requested_conflicts=[]`, 已确认 | `save_draft` | 空闲且已确认 → 落库 |
| `save_draft` | `status="saved"` | finish(saved) | 成功 → 结束 |
| `save_draft` | `status="duplicate"` | finish(saved, draft_status=duplicate) | 重复 → 不再创建 |
| `save_draft` | 抛错 | `save_draft`（原参数重试一次） | 失败 → 重试 |
| `save_draft` | 再次抛错 | finish(failed) | 重试仍失败 → 如实失败 |

**同一条执行路径，不同的观测 → 不同的动作序列**。这是「动态决策」的可验证形式，
运行记录见 [03-evaluation.md](03-evaluation.md) 第 3 节。

### 3.1 冲突处理：给建议，不改要求

用户要求 18:00（`hard: true`），18:00 被 M02 占用。系统的处理：

1. `query_slots` 观测到 `requested_conflicts=[M02@18:00]`
2. 按**同一冲突粒度**检索可用时段，取最接近 19:00
3. 输出 `outcome=needs_confirmation`、`suggested_slot="19:00"`
4. 输出 `pending_confirmations=[{type: "change_publish_time", original: "18:00", proposed: "19:00", question: "..."}]`
5. **不调用 `save_draft`** —— 草稿库中无记录

用户明确要求「18:00 是硬性要求，先看看能不能排」，系统给出的是**替代建议 + 确认请求**，
而不是擅自改成 19:00 落库。这是「确认边界」的核心。

### 3.2 补查：先补查，再判定

`read_material(fields=[...])` 的补查结果会**回写**到该素材的内存 overlay
（`ToolContext.overlay`），后续 `check_rules` 在叠加后的素材上评估。

> 没有回写的话，补查就是空转 —— 规则检查永远基于原始缺失数据。
> 这是本项目开发中实际发现并修复的缺陷。

补查数据源优先级：`simulated_failures[material_id]["supplement"]` →
素材 `notes` 中的显式结论 → 无数据（`still_missing`，缺失项保持缺失，由模型阻断）。

### 3.3 失败：重试一次，然后如实失败

```
save_draft 抛错 → 模型用**相同参数**重试一次（排除瞬时故障）
                → 仍失败 → outcome=failed, draft_id=null, save_detail=错误信息
```

重试次数由 `state["save_attempts"]` 计数，不会无限重试（步数上限兜底）。

## 4. 技术选型与取舍

### 4.1 为什么原生实现，不用 LangGraph / ADK / AgentScope

题目明示「框架名称不加分，能解释取舍与修改结果才有价值」，且框架非必需。

| 考量 | 说明 |
|---|---|
| **决策循环只有一层** | 标准的 ReAct 单循环用 LangGraph 的 StateGraph 表达略显重（需定义状态、节点、边、条件路由） |
| **需要的核心是「观测驱动」** | 循环骨架本身就是数十行代码（`src/agent/loop.py`），换成框架反而要对抗框架抽象 |
| **离线 Mock 是硬需求** | 必须能无密钥跑通全流程。框架的状态图会引入额外的心智负担，却不带来离线能力 |
| **可解释性是评分项** | 「高分依据是冲突调整、确认边界与防重复」——这些都在工具契约与状态更新里，原生实现可直接读代码验证 |
| **无依赖负担** | 运行时**零第三方依赖**，评审者 `pip install -r requirements.txt` 后立即可跑，无需解决版本冲突 |

**代价与应对**：没有现成框架的 checkpoint / 持久化 / 可视化能力。
本项目用两个自制机制覆盖 —— SQLite 草稿库（状态持久化）与 JSONL Trace（执行回放）。

如果需求演进到多 Agent 协作、需要条件边可视化或断点续跑，我会迁移到 LangGraph；
当前规模迁移收益为负。

### 4.2 决策层双通道

| | 离线 Mock | 真实模型 |
|---|---|---|
| 文件 | `src/llm/offline_mock.py` | `src/llm/openai_compat.py` |
| 协议 | `LLMClient.decide(messages, tool_specs) -> Action` | 同左 |
| 启用条件 | 默认（无 API Key） | `.env` 中有 `LLM_API_KEY` |
| 用途 | CI / 回归测试 / 流程验证 | 验证真实决策质量 |

两者共用**同一套 Action 协议**与**同一段系统提示词约束**（`src/agent/prompts.py`），
切换通道不改变工具契约与最终字段结构。

### 4.3 幂等的两层保障

单靠应用层检查会有竞态；单靠数据库约束则无法表达「内容变了要重新排期」。所以两层都要：

| 层 | 键 | 作用 |
|---|---|---|
| 应用层 | `request_key = material_id\|date\|start\|channel\|content_hash` | 同内容重复提交 → 直接返回 duplicate，不发起写入 |
| 数据库层 | `UNIQUE(material_id, date, start_time)` | 最终防线：即使绕过应用层，也无法产生第二份同排期草稿 |

`DraftStore.save_draft` 在 `IntegrityError` 时**同时**按两个维度回查，两者都查不到
才判定为真实失败。（只按 `request_key` 查是开发中实际发现并修复的缺陷 —— 内容变更后
`content_hash` 变化、`request_key` 不同，但排期相同，此时数据库约束会冲突。）

### 4.4 确定性 vs 模型

**所有业务计算都在确定性代码里**：

- 规则判定 → `domain/rules.py.evaluate_rules()`（纯函数，可单测）
- 时段冲突 → `domain/slots.py.find_conflicts()`（纯函数，可单测）
- 缺失字段 → `domain/rules.py.missing_required_fields()`（**唯一口径**，工具层与规则层共用）
- 幂等 → `storage/db.py`（数据库约束）

模型只做「选下一步」。这样：
- 规则改动不会因模型漂移而失效
- 每条业务规则都能写单测（`tests/test_rules.py`）
- 模型的幻觉不会写入数据（模型无法直接调工具，必须经过 `ToolRegistry`）

### 4.5 提示词约束 vs 代码约束

`src/agent/prompts.py` 的 `SYSTEM_POLICY` 给了模型四条硬性约束，但**每条都有代码层对应**：

| 提示词约束 | 代码层强制 |
|---|---|
| 不得声称已发布 | `_finalize` 无条件 `published=False` |
| 不得擅自改时间 | 冲突路径不产出 `save_draft` 调用 |
| 不得臆测缺失值 | 缺失字段保持 None，`check_rules` 报 unresolved 风险 |
| 不得谎报成功 | `save_draft` 失败抛异常；缺 `draft_id` 改判 `failed` |

**提示词是引导，代码是保证。** 真实模型不完全遵守提示词时，系统依然不会越权。

## 5. 数据契约要点

### 5.1 工具清单

| 工具 | 作用 | 失败方式 |
|---|---|---|
| `read_material` | 读取素材元数据；带 `fields` 时执行补查 | 素材不存在 → `ToolError` |
| `check_rules` | 规则检查，返回风险等级/原因/缺失字段/补查建议 | 素材不存在 → `ToolError` |
| `query_slots` | 查询指定日期的占用与可用时段 | 缺 `date` → `ToolError` |
| `save_draft` | 保存排期草稿（幂等） | 缺参数 / 素材不存在 / 写入故障 → `ToolError` |

**没有 `publish`、`push`、`release` 之类工具。** 排期草稿与真实发布是两件事。

### 5.2 规则表

| 规则 | 级别 | 触发条件 | 可自动消解 |
|---|---|---|---|
| R-AUTH 授权凭证校验 | high | 授权缺失或不完整 | 可（补查授权） |
| R-DUR 时长/规格完整性 | medium | `duration_sec` 为 None 或负数 | 可（补查时长） |
| R-SENS 敏感标签复核 | high | 存在 `sensitivity_tags` | 可（补查复核结论） |
| R-WORD 禁用词检查 | medium | 文案命中禁用词 / 文案缺失 | **命中禁用词不可**（需人工改写） |
| R-CHAN 渠道匹配 | low | `channels` 为空 | 可（补查渠道） |

`unresolved_high` / `unresolved_medium`（补查后仍无法自动消解的规则）是**阻断的依据**。

### 5.3 输出契约

```json
{
  "material_id": "M01",
  "outcome": "saved" | "needs_confirmation" | "blocked" | "failed" | "incomplete",
  "draft_status": "saved" | "duplicate" | "failed" | "not_saved",
  "risk_level": "none" | "low" | "medium" | "high",
  "risks": [{"rule_id": "", "name": "", "severity": "", "reason": "", "detail": ""}],
  "suggested_slot": "19:00",
  "suggested_date": "2026-11-02",
  "pending_confirmations": [{"type": "", "field": "", "original": "", "proposed": "", "question": ""}],
  "draft_id": "D-M01-20261102-1900",
  "published": false,
  "reasoning": "..."
}
```

## 6. 运行方式

题目要求 CLI / HTTP API / Notebook 三选一 → **选择 CLI**（理由：无需服务进程，
评审者一条命令即可复现全部结果）。

```bash
# 业务用例
python -m src.cli --case all
python -m src.cli --case normal_conflict --json

# 自由请求
python -m src.cli --material M01 --request "把 M01 排到 2026-11-02 18:00"
python -m src.cli --material M05 --request "把 M05 排到 2026-11-02 10:00" --confirm

# 草稿库
python -m src.cli --list-drafts

# 运行记录（Trace）
python -m src.cli --case all --trace-dir examples/traces
```

## 7. 目录结构

```
项目1/
├── README.md
├── requirements.txt          # 运行时零依赖，pytest 仅测试用
├── .env.example              # 无变量时也能跑（说明见文件内）
├── data/                     # 合成素材、规则、时段占用、用例
├── src/
│   ├── agent/                # ReAct 循环 + 系统提示词
│   ├── domain/               # 规则引擎 / 时段引擎 / 请求解析
│   ├── llm/                  # 决策层：离线 Mock + OpenAI 兼容
│   ├── storage/              # SQLite 草稿库
│   ├── tools/                # 工具契约 + 实现
│   ├── cli.py                # 命令行入口
│   ├── config.py             # 配置加载
│   └── runner.py             # 装配层
├── tests/                    # 可执行测试
├── examples/
│   ├── traces/               # 运行记录（JSONL）
│   ├── inputs/               # 输入样例
│   └── outputs/              # 实际输出
└── docs/
    ├── 01-requirements.md
    ├── 02-solution.md
    ├── 03-evaluation.md
    ├── 04-ai-usage.md
    └── 05-retrospective.md
```
