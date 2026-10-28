# 04 AI 使用说明

## 1. 模型在整个系统中的角色

模型（LLMClient）只负责一件事：**决定下一步做什么**。

```
模型决策：下一步调用哪个工具、传什么参数、还是结束并给出结论
工具执行：返回事实（素材内容、风险、占用时段、写入结果）
```

模型**不**负责：
- 计算规则判定结果（由 `domain/rules.py` 确定性执行）
- 计算时段冲突（由 `domain/slots.py` 确定性执行）
- 直接读写数据库（只能通过工具）

这个分工是刻意的：把**可验证的计算**交给确定性代码，把**需要判断的部分**
（何时补查、补查什么、冲突后是否建议替代时段、失败后是否重试）交给模型。
好处是业务规则可测试、可回归，模型幻觉不会污染数据。

## 2. 两种决策通道

| | 离线 Mock | 真实模型 |
|---|---|---|
| 文件 | `src/llm/offline_mock.py` | `src/llm/openai_compat.py` |
| 协议 | `LLMClient.decide(messages, tool_specs) -> Action` | 同左 |
| 启用条件 | 默认（无 API Key） | `.env` 中配置 `LLM_API_KEY` |
| 用途 | CI、回归测试、流程验证 | 验证真实决策质量 |

两者共用**同一套 Action 协议**与**同一段系统提示词约束**（`agent/prompts.py`），
因此切换通道不会改变工具契约与最终字段结构。

## 3. 输出契约

模型每次只输出一个 JSON 对象：

```json
{
  "action": "tool" | "finish",
  "tool": "read_material" | "check_rules" | "query_slots" | "save_draft",
  "args": { ... },
  "rationale": "选择该动作的依据（简要，不含内部思维过程）",
  "final": { ... }
}
```

`finish` 时的 `final` 结构：

```json
{
  "material_id": "M01",
  "outcome": "saved" | "needs_confirmation" | "blocked" | "failed",
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

## 4. 提示词中的硬性约束

`src/agent/prompts.py` 的 `SYSTEM_POLICY` 给模型四条硬性约束：

1. **只能输出排期草稿**。绝对不存在发布动作，禁止声称「已发布」「已上线」「已推送」。
2. **不可自行更改用户的硬性时间要求**。时段冲突时只能「提出替代时段 + 请求确认」。
3. **信息不足先补查**；补查后仍缺失则阻断，不得臆测缺失字段的值。
4. **工具失败如实返回失败**，不得谎报成功；不重复创建同一草稿。

约束 1、2、3 不只是提示——它们在代码层也有对应的强制机制：

| 提示词约束 | 代码层强制 |
|---|---|
| 不得声称已发布 | `_finalize` 强制 `published=False`；工具集无发布能力 |
| 不得擅自改时间 | 冲突时只产出 `pending_confirmations`，不写库 |
| 不得臆测缺失值 | 缺失字段保持 None，`check_rules` 报 unresolved 风险 |
| 不得谎报成功 | `save_draft` 失败抛异常；缺 `draft_id` 改判为 `failed` |

**提示词是引导，代码是保证。** 真实模型不完全遵守提示词时，系统仍不会越权。

## 5. 离线 Mock 的定位（重要边界）

`offline_mock.py` **不是**预录日志，也**不是**固定分支脚本。它是一个
**观测驱动的策略引擎**：只读取上一次工具返回的真实观测值，据此选择下一步动作。

因此当工具结果不同时（时段被占用 vs 空闲、素材完整 vs 缺失、写入成功 vs 失败），
它给出的动作序列**真实不同**。但要明确：

> 它能证明**流程与工具契约正确**，**不能证明真实模型的决策质量**。

「这是离线 Mock 的输出」不能作为「真实模型也能做对」的证据。
真实模型的行为差异与失败模式，需要在接入真实模型后单独评测
（见 [03-evaluation.md](03-evaluation.md) 第 7 节）。

## 6. 接入真实模型

```bash
# .env
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxxxxxxx
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=0
MAX_TOOL_CALLS=8
```

只要设置了 `LLM_API_KEY`，`Config.load()` 就自动切换到真实模型通道；
不设置则一律离线运行（`AGENT_OFFLINE=1` 可强制离线）。

```bash
python -m src.cli --material M01 --request "把 M01 排到 2026-11-02 18:00" --json
```

`OpenAICompatClient` 使用标准 `/chat/completions` 接口，`response_format=json_object`
约束输出为 JSON，`temperature=0` 保证可复现。

**注意**：`temperature=0` 只能保证同输入大概率同输出，不保证完全确定性
（服务端实现差异、浮点计算顺序都可能引入抖动）。需要严格可复现的场景
应使用离线 Mock。

## 7. 决策质量观察点

接入真实模型后，重点观察这几类失败：

| 失败模式 | 症状 | 应对 |
|---|---|---|
| 擅自改时间 | 冲突时直接用替代时段落库 | 检查 `pending_confirmations` 是否生成且未落库 |
| 谎报成功 | `draft_status=saved` 但草稿库无记录 | 已有断言覆盖（`draft_id` 缺失改判 failed） |
| 无限重试 | 反复调用同一工具 | 步数上限会兜底为 `incomplete`，但需看 trace 判断 |
| 提前结束 | 还没查时段就 finish | 看 trace 中 `check_rules` / `query_slots` 是否被跳过 |
| 臆测缺失值 | 用不存在的时长算占用 | 对比 `read_material` 观测与 `query_slots` 的 `duration_sec` |

Trace 文件（`examples/traces/*.jsonl`）记录了每步的动作、参数、观测摘要与理由，
是排查上述问题的第一入口。

## 8. AI 辅助开发说明（题目要求披露）

本项目的代码、文档、测试与验证材料**由 AI 辅助生成**
（Cursor / Claude 系列模型），具体说明见 [05-retrospective.md](05-retrospective.md) 第 2 节。
此处集中列出「AI 生成了什么、人改了什么、如何验证」三类信息：

### 8.1 AI 主要产出

| 类别 | 说明 |
|---|---|
| 目录骨架与模块划分 | 分层设计（agent / domain / llm / storage / tools）与文件组织 |
| 领域层实现 | `domain/rules.py` 规则引擎、`domain/slots.py` 时段引擎、`domain/parsing.py` 请求解析 |
| 工具层实现 | 四个工具的契约与实现、错误暴露、补查回写机制 |
| 决策层实现 | `llm/offline_mock.py` 观测驱动策略、`llm/openai_compat.py` 真实模型通道 |
| 循环层实现 | `agent/loop.py` ReAct 循环、步数上限、越权保护、trace 记录 |
| 存储层实现 | `storage/db.py` SQLite 草稿库与幂等约束 |
| 测试 | 8 个测试文件、102 个用例，含端到端断言 |
| 文档 | `docs/` 五份文档 + README |
| 验证材料生成 | `scripts/gen_outputs.py`（输入/输出/trace 自动化） |

### 8.2 人工（本人）的主要修改与判断

以下改动是**人做出判断后落到代码的**，AI 起初的实现并不正确或不完整：

| # | 问题 | 发现方式 | 修改 |
|---|---|---|---|
| 1 | **补查结果不回写素材**，导致规则检查永远基于原始缺失数据，补查沦为空转 | 人工阅读补查链路时发现 | 引入 `ToolContext.overlay`，补查字段回写内存副本，`check_rules` 在叠加后的素材上评估 |
| 2 | **幂等回查只看 `request_key`**，内容变更后 `content_hash` 变化、`request_key` 不同，但排期相同，会撞数据库唯一约束被误判为失败 | 人工构造「内容变更但排期相同」的重复提交场景 | `IntegrityError` 时**同时**按 `request_key` 与 `(material_id, date, start_time)` 两个维度回查，两者都查不到才判失败 |
| 3 | **`.gitignore` 忽略了 `examples/traces/*.jsonl`**，而题目明确要求运行记录存放于 `examples/` | 人工核对交付规范时发现 | 改为只忽略临时调试 trace，保留正式 trace 入库 |
| 4 | **决策来源不可验证**，无法证明「模型根据不同工具结果调整动作」 | 人工对照题目对 ReAct 的判定口径 | 增加 `Action.decided_by` 字段与 trace 的 `decided_by` 列，并在 `tests/test_react.py` 中用断言证明 |
| 5 | **trace 文件累加**，重跑后混入旧记录，与当前实现不一致 | 人工用断言检查 trace 字段完整性时暴露 | `run_case` 每次运行前清空目标 trace 文件 |
| 6 | **离线 Mock 与「固定分支」的界限模糊**，存在被判为未完成 ReAct 的风险 | 人工审阅题目判定口径 | 补充 `decided_by` 标注、观测驱动的分支依据说明，以及「相同请求不同观测 → 不同决策」的专项测试 |
| 7 | **请求解析为规则式**，无法处理自由表述 | 人工评估真实使用场景 | 明确写入限制，并让 CLI 在解析失败时返回明确错误而非猜测 |
| 8 | **SQLite 连接泄漏** —— `with sqlite3.connect(...) as conn` 只管理事务、**不关闭连接**，每次工具调用都泄漏一个句柄（Windows 上导致草稿库文件无法删除） | 人工在 Windows 上删除临时库文件时遇到 `PermissionError` | 引入 `@contextmanager _cursor()`，在 `finally` 中显式 `conn.close()`；并补回归测试 `test_connections_are_closed_not_leaked` |

### 8.3 验证方式（AI 产出如何被验证）

所有 AI 产出的代码都**未被无条件接受**，全部经过以下验证：

```bash
python -m pytest tests -q          # 102 个测试全绿
python -m src.cli --case all        # 6 项必须验收全部通过
python -m scripts.gen_outputs       # 生成实际输出与 trace，与文档数字核对
python -m scripts.check_secrets     # 提交前密钥/隐私自查
```

文档中引用的每一个数字（102 个测试、6 项验收、7 组用例、1 行草稿）
都来自上述命令的**实际输出**，而非 AI 估计。

### 8.4 未使用真实模型的部分（如实说明）

本次交付**未配置真实模型 API Key**，因此：

- `docs/03-evaluation.md` 中的全部结果来自**离线 Mock 通道**
- 离线 Mock **只能证明流程与工具契约正确，不能证明真实模型的决策效果**
  （题目本身也明确这一点）
- 真实模型通道的代码已实现并可切换，但**其决策质量未经本次评测验证**

这一点在 `docs/03-evaluation.md` 第 7 节与本文件第 5 节均已明示，
避免用 Mock 结果冒充真实模型效果。
