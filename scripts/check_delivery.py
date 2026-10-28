"""交付自检：逐条核对题目要求。

对照题目「项目要求 / 交付结果 / 动态决策 / 必须验收 / 运行方式 / 模型与工具 /
提交方式 / Agent 决策要求 / GitHub 交付规范」逐条检查当前仓库状态。

用法：
    python -m scripts.check_delivery
退出码：0 = 全部满足；1 = 存在未满足项。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def has(rel: str) -> bool:
    return (ROOT / rel).exists()


# ---------------- 项目结构（题目「GitHub 交付规范」） ----------------
for rel, label in [
    ("README.md", "README.md"),
    ("src", "src/"),
    ("tests", "tests/"),
    ("examples", "examples/"),
    ("data", "data/"),
    ("docs", "docs/"),
    ("requirements.txt", "依赖文件 requirements.txt"),
    (".env.example", ".env.example"),
]:
    check(f"结构：{label}", has(rel))

for doc in ["01-requirements.md", "02-solution.md", "03-evaluation.md", "04-ai-usage.md", "05-retrospective.md"]:
    check(f"文档：docs/{doc}", has(f"docs/{doc}"))

# ---------------- 验证材料 ----------------
check("验证材料：examples/inputs（输入）", has("examples/inputs/cases.json"))
check("验证材料：examples/outputs（实际输出）", has("examples/outputs/summary.md"))
traces = list((ROOT / "examples" / "traces").glob("*.jsonl")) if has("examples/traces") else []
check("验证材料：examples/traces（运行记录）", len(traces) >= 3, f"{len(traces)} 个 trace 文件")

# ---------------- 6 项必须验收 ----------------
ACCEPT = {
    "normal_ok": ("正常排期", "needs_confirmation"),
    "missing_info": ("素材信息缺失", "blocked"),
    "rule_risk": ("规则风险", "blocked"),
    "normal_conflict": ("时段冲突", "needs_confirmation"),
    "duplicate_submit_twice": ("重复提交", "saved"),
    "save_failure": ("保存失败", "failed"),
}
for case, (label, expect) in ACCEPT.items():
    f = ROOT / "examples" / "outputs" / f"{case}.json"
    ok = False
    detail = "缺少输出文件"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        a = d["actual"]
        ok = a["outcome"] == expect and a["published"] is False
        detail = f"outcome={a['outcome']} draft={a['draft_status']} published={a['published']}"
    check(f"验收：{label}（{case}）", ok, detail)

# 冲突场景必须不落库
f = ROOT / "examples" / "outputs" / "normal_conflict.json"
if f.exists():
    d = json.loads(f.read_text(encoding="utf-8"))
    check("验收：时段冲突不落库", d["draft_count_after"] == 0, f"草稿数={d['draft_count_after']}")
    pc = d["actual"]["pending_confirmations"]
    check("验收：冲突标记待确认", any(c["type"] == "change_publish_time" for c in pc))

# 重复提交只产生一份
f = ROOT / "examples" / "outputs" / "duplicate_submit_twice.json"
if f.exists():
    d = json.loads(f.read_text(encoding="utf-8"))
    check("验收：重复提交只产生一份草稿", d["draft_count_after"] == 1, f"草稿数={d['draft_count_after']}")
    check("验收：重复提交第二次为 duplicate",
          d["runs"][-1]["draft_status"] == "duplicate", f"{d['runs'][-1]['draft_status']}")

# 保存失败不谎报
f = ROOT / "examples" / "outputs" / "save_failure.json"
if f.exists():
    d = json.loads(f.read_text(encoding="utf-8"))
    a = d["actual"]
    check("验收：保存失败返回 failed 且 draft_id 为空",
          a["outcome"] == "failed" and not a["draft_id"], f"outcome={a['outcome']}")

# ---------------- 全部用例都不声称已发布 ----------------
all_ok = True
detail = ""
for f in (ROOT / "examples" / "outputs").glob("*.json"):
    if f.name == "drafts.json":
        continue
    a = json.loads(f.read_text(encoding="utf-8"))["actual"]
    if a["published"] is not False:
        all_ok = False
        detail = f"{f.name} published={a['published']}"
check("安全：所有用例 published=false", all_ok, detail)

# ---------------- ReAct 决策要求 ----------------
tr = ROOT / "examples" / "traces" / "normal_conflict.jsonl"
if tr.exists():
    entries = [json.loads(x) for x in tr.read_text(encoding="utf-8").splitlines() if x.strip()]
    check("ReAct：运行记录含步骤/工具参数/返回摘要/后续动作/决策依据",
          all(all(k in e for k in ("step", "args", "result_summary", "next_action", "rationale", "decided_by"))
              for e in entries), f"{len(entries)} 步")
    decided = [e["decided_by"] for e in entries if e["action"] == "tool"]
    check("ReAct：各步决策依据不同（观测驱动）", len(set(decided)) > 1, f"{len(set(decided))} 种依据")

tr2 = ROOT / "examples" / "traces" / "missing_info.jsonl"
if tr and tr2.exists():
    a = [e.get("tool") for e in (json.loads(x) for x in tr.read_text(encoding="utf-8").splitlines() if x.strip())
         if e["action"] == "tool"]
    b = [e.get("tool") for e in (json.loads(x) for x in tr2.read_text(encoding="utf-8").splitlines() if x.strip())
         if e["action"] == "tool"]
    check("ReAct：至少两条执行路径且动作序列不同", a != b, f"{a} vs {b}")

check("ReAct：步数上限返回未完成状态", has("src/agent/loop.py") and "incomplete" in
      (ROOT / "src" / "agent" / "loop.py").read_text(encoding="utf-8"))
check("ReAct：专项测试存在", has("tests/test_react.py"))

# ---------------- 工具（4 个） ----------------
tools_src = (ROOT / "src" / "tools" / "impl.py").read_text(encoding="utf-8")
for t in ["read_material", "check_rules", "query_slots", "save_draft"]:
    check(f"工具：{t}", t in tools_src)
check("安全：工具集无发布工具",
      not any(p in tools_src for p in ["def publish", "def release", "def push_live"]))

# ---------------- Mock 边界声明 ----------------
eval_doc = (ROOT / "docs" / "03-evaluation.md").read_text(encoding="utf-8")
check("Mock：文档声明能力边界",
      "不证明真实模型" in eval_doc and "Mock" in eval_doc,
      "docs/03-evaluation.md 第 7 节")

# ---------------- 密钥 ----------------
try:
    r = subprocess.run([sys.executable, "-m", "scripts.check_secrets"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    check("安全：无真实密钥或隐私数据", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout else "")
except Exception as exc:  # pragma: no cover
    check("安全：无真实密钥或隐私数据", False, str(exc))

# ---------------- 输出 ----------------
passed = sum(1 for _, ok, _ in results if ok)
failed = [(n, d) for n, ok, d in results if not ok]
print(f"交付自检：{passed}/{len(results)} 项通过\n")
for name, ok, detail in results:
    mark = "OK  " if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line)
if failed:
    print(f"\n未满足 {len(failed)} 项：")
    for n, d in failed:
        print(f"  - {n} {d}")
    sys.exit(1)
print("\n全部满足。")
sys.exit(0)
