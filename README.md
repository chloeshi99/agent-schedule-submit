# 素材审核与发布排期 Agent

一个基于 ReAct 循环的素材上线前审核与排期助手：读取素材 → 执行审核规则 → 检查时段占用 → 产出**排期草稿**（或阻断 / 请求确认）。

> **系统不具备真实发布能力。** 工具集中不存在发布工具，所有落库产物的 `published` 恒为 `false`，并在循环层有强制越权保护。

**题号**：`项目1` · 素材审核与发布排期 Agent

## 评审入口（建议从这里开始）

| 我想看什么 | 去哪里 |
|---|---|
| **一键跑通全部验收点** | `python -m src.cli --case all` |
| **实际输出汇总表** | [examples/outputs/summary.md](examples/outputs/summary.md) |
| **运行记录（步骤/参数/返回摘要/后续动作/决策依据）** | [examples/traces/](examples/traces) |
| **ReAct 决策要求的验证** | [docs/03-evaluation.md](docs/03-evaluation.md) 第 5 节 |
| **输入样例** | [examples/inputs/cases.json](examples/inputs/cases.json) |
| **需求与验收标准** | [docs/01-requirements.md](docs/01-requirements.md) |
| **设计取舍** | [docs/02-solution.md](docs/02-solution.md) |
| **已知限制（重要）** | [docs/03-evaluation.md](docs/03-evaluation.md) 第 7 节 |
| **AI 辅助开发说明** | [docs/04-ai-usage.md](docs/04-ai-usage.md) 第 8 节 |
| **我能否一键核对全部验收点？** | `python -m scripts.check_delivery`（40 项逐条自检，输出 OK/FAIL） |
| **复盘与下一步** | [docs/05-retrospective.md](docs/05-retrospective.md) |

## 快速开始

无需任何环境变量、无需安装第三方依赖，离线即可完整运行。

```bash
# 1. 环境（Python 3.10+）
pip install -r requirements.txt

# 2. 运行全部业务用例（覆盖 6 项必须验收）
python -m src.cli --case all

# 3. 运行测试套件（103 passed）
python -m pytest tests -q

# 4. 重新生成实际输出与运行记录
python -m scripts.gen_outputs

# 5. 提交前密钥/隐私自查
python -m scripts.check_secrets

# 6. 交付自检（逐条核对题目要求，40 项）
python -m scripts.check_delivery
```

自由请求：

```bash
python -m src.cli --material M01 --request "把 M01 排到 2026-11-02 18:00"
python -m src.cli --material M05 --request "把 M05 排到 2026-11-02 10:00" --confirm
python -m src.cli --list-drafts      # 查看草稿库
python -m src.cli --case all --trace-dir examples/traces   # 生成运行记录
```

> `.env` **无需配置**。不设置任何变量即可跑通全部流程（离线 Mock 通道）。
> 需要真实模型时再复制 `.env.example` 并填入密钥。

## 实际运行结果

```
[normal_conflict]         outcome=needs_confirmation  draft=not_saved  suggested=19:00
[normal_ok]               outcome=needs_confirmation  draft=not_saved  suggested=10:00
[missing_info]            outcome=blocked             draft=not_saved  risk=high
[rule_risk]               outcome=blocked             draft=not_saved  risk=high
[save_failure]            outcome=failed              draft=failed     draft_id=null
[duplicate_submit]        outcome=saved               draft=saved
[duplicate_submit_twice]  outcome=saved               draft=duplicate

草稿库总数: 1
```

对照题目要求的六项验收：

| 验收点 | 用例 | 结果 |
|---|---|---|
| 正常排期 | `normal_ok` | ✅ 建议 10:00，等待确认 |
| 素材信息缺失 | `missing_info` | ✅ 补查后仍缺失 → 阻断 |
| 规则风险 | `rule_risk` | ✅ 敏感+禁用词 → 阻断 |
| 时段冲突 | `normal_conflict` | ✅ 18:00 被占 → 建议 19:00 + 待确认，不落库 |
| 重复提交 | `duplicate_submit_twice` | ✅ 第二次 duplicate，草稿表 1 行 |
| 保存失败 | `save_failure` | ✅ failed，draft_id=null |

**所有用例 `published` 均为 `false`** —— 没有任何一个用例声称已发布。

## 它能做什么

| 能力 | 实现方式 |
|---|---|
| 读取素材、发现缺失字段 | `read_material` 工具 |
| 审核规则判定风险等级 | `check_rules` 工具 + 确定性规则引擎 |
| 补查缺失信息 | `read_material(fields=[...])`，结果回写生效 |
| 检查时段冲突、给替代时段 | `query_slots` 工具 + 时段引擎 |
| 保存排期草稿（幂等） | `save_draft` 工具 + SQLite 唯一约束 |
| 失败如实上报、失败后重试 | 工具抛错 → 模型决策 → 重试一次 → `failed` |
| 步数上限 | `MAX_TOOL_CALLS=8`，达到返回 `outcome=incomplete` |

## ReAct 决策（题目核心要求）

模型在运行时选择动作、执行工具、观察结果、调整下一步：

```
① 模型选择动作  action = client.decide(messages, tool_specs)
② 执行工具      observation = registry.call(action.tool, action.args)
③ 观察结果并调整 observation 回灌 messages → 下一轮 decide() 读新状态
                或 action.action == "finish" 时结束
```

每步决策都记录**它依据哪个观测字段**（trace 的 `decided_by` 列），可逐行核对：

| 步骤 | 工具 | 观测 | 决策依据 |
|---|---|---|---|
| 1 | `read_material` | `missing_fields=[]` | `state.step:1` |
| 2 | `check_rules` | `risk_level=none` | `observation.missing_fields:empty` |
| 3 | `query_slots` | `requested_conflicts=[M02@18:00]` | `observation.unresolved_high:empty` |
| 4 | **finish** | — | **`observation.requested_conflicts`** → 建议 19:00 + 待确认 |

**最强证据**：相同请求连跑两次，前 4 步动作序列完全相同，仅因 `save_draft` 返回
`saved` vs `duplicate`，第 5 步决策就不同（见
[docs/03-evaluation.md](docs/03-evaluation.md) 第 5.3.1 节）。

## 它明确不做的事

- **不发布**。只产出草稿，工具集里没有发布能力。
- **不擅自更改**用户给出的硬性时间要求。冲突时只给替代建议 + 请求确认，**不落库**。
- **不臆测**缺失字段的值。补查后仍缺失就阻断。
- **不谎报成功**。写入失败返回 `failed`；返回 `saved` 但缺 `draft_id` 也改判 `failed`。

## 架构

```
user_request ──> ReActAgent(loop) ──> LLMClient ──> ToolRegistry ──> domain / storage
                   决策循环+越权保护     决定下一步    只做事实与写入    规则/时段/草稿库
```

模型只负责**决定下一步做什么**；规则判定、时段计算、数据库写入全部由确定性代码执行。
这样业务规则可测试、可回归，模型幻觉不会污染数据。

**技术选择：原生实现，未使用 LangGraph / ADK / AgentScope。**
理由与代价见 [docs/02-solution.md](docs/02-solution.md) 第 4.1 节。

## 项目结构

```
├── data/                # 合成数据：素材 / 规则 / 时段占用 / 用例
├── src/
│   ├── agent/           # ReAct 循环 + 系统提示词
│   ├── domain/          # 规则引擎 / 时段引擎 / 请求解析
│   ├── llm/             # 决策层：离线 Mock + OpenAI 兼容客户端
│   ├── storage/         # SQLite 草稿库（唯一约束保证幂等）
│   ├── tools/           # 工具契约 + 实现
│   ├── cli.py           # 命令行入口
│   ├── config.py        # 配置加载
│   └── runner.py        # 装配层
├── tests/               # pytest 套件（103 个测试，8 个文件）
├── scripts/             # 验证材料生成 / 密钥自查
├── examples/
│   ├── inputs/          # 输入样例
│   ├── outputs/         # 实际输出（含 summary.md 汇总表）
│   └── traces/          # 运行记录（JSONL，含 decided_by 决策依据）
└── docs/                # 文档
```

## 文档

| 文档 | 内容 |
|---|---|
| [01-requirements.md](docs/01-requirements.md) | 用户、目标、范围、成功标准（含验收点逐条对照） |
| [02-solution.md](docs/02-solution.md) | 调用流程、动态决策、技术取舍 |
| [03-evaluation.md](docs/03-evaluation.md) | 测试预期、实际结果、失败与限制 |
| [04-ai-usage.md](docs/04-ai-usage.md) | 模型角色边界、接入真实模型、**AI 辅助开发说明** |
| [05-retrospective.md](docs/05-retrospective.md) | 最难问题、学习收获、下一步 |
| [06-tools-and-data.md](docs/06-tools-and-data.md) | 工具契约、规则表、数据库结构（补充参考） |

## 接入真实模型（可选）

```bash
cp .env.example .env
# 填入 LLM_API_KEY 等
python -m src.cli --material M01 --request "把 M01 排到 2026-11-02 18:00" --json
```

设置 `LLM_API_KEY` 后自动切换到 OpenAI 兼容接口；不设置则一律离线运行。

> **重要边界**：默认的离线 Mock 是**观测驱动的策略引擎**，能证明「流程与工具契约正确」，
> **不能证明真实模型的决策质量**。本次交付的全部评测结果均来自离线 Mock 通道
> （环境无密钥），详见 [docs/03-evaluation.md](docs/03-evaluation.md) 第 7 节。

## 环境

- Python 3.10+（开发环境 3.12.7）
- 运行时代**零第三方依赖**（仅标准库）；测试需 `pytest`
- `.env` 无需配置

## 提交信息

| 字段 | 值 |
|---|---|
| **题号** | `项目1` —— 素材审核与发布排期 Agent |
| **GitHub URL** | https://github.com/chloeshi99/agent-schedule-submit |
| **最终 commit SHA** | 本仓库 `main` 分支的最新提交 —— 取 `git rev-parse HEAD`，或见 [commits/main](https://github.com/chloeshi99/agent-schedule-submit/commits/main) |
| **仓库可见性** | Public（公开可读，评审账号无需授权） |
| **默认分支** | `main` |

> 说明：commit SHA 无法预写入自身所在的提交（写入即改变 SHA）。
> 上面的链接会直接显示当前 SHA；提交表单中填写 `git rev-parse HEAD` 的输出即可。

### 从零复现

```bash
git clone https://github.com/chloeshi99/agent-schedule-submit.git
cd agent-schedule-submit
pip install -r requirements.txt

python -m src.cli --case all            # 7 个用例（含全部验收点）
python -m pytest tests -q               # 测试套件
python -m scripts.check_delivery         # 交付自检
python -m scripts.check_secrets          # 密钥/隐私自查
```

无需任何环境变量、无需 API Key、无需联网 —— 默认走离线 Mock 决策通道。

> **项目边界**：本项目只产出**排期草稿**，系统不具备真实发布能力（`published` 恒为 `false`）。
