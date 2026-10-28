"""生成 examples/outputs/ 下的实际输出样例。

用途：把「输入 -> 实际输出 -> 运行记录」固化成可提交、可复现的材料。
运行：
    python -m scripts.gen_outputs
生成：
    examples/outputs/<case>.json   该用例的完整运行结果（含最终结果与动作序列）
    examples/outputs/summary.md    汇总表（实际结果 vs 期望）
    examples/outputs/drafts.json   运行后的草稿库快照
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.runner import AgentRunner, load_cases, run_case  # noqa: E402
from src.storage.db import DraftStore  # noqa: E402

OUT_DIR = ROOT / "examples" / "outputs"
TRACE_DIR = ROOT / "examples" / "traces"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    config = Config.load()
    config.db_path = ROOT / "data" / "drafts.sqlite3"

    cases = load_cases(config)["cases"]
    rows = []

    for name, case in cases.items():
        # 每个用例使用独立的草稿库，保证结果可复现、不受用例执行顺序影响
        case_db = ROOT / "data" / f".tmp_{name}.sqlite3"
        if case_db.exists():
            case_db.unlink()
        config.db_path = case_db

        item = run_case(name, config=config, trace_dir=TRACE_DIR)
        final = item["final"]

        payload = {
            "case": name,
            "user_request": item["user_request"],
            "expect": item["expect"],
            "actual": {
                "outcome": final.get("outcome"),
                "draft_status": final.get("draft_status"),
                "draft_id": final.get("draft_id"),
                "risk_level": final.get("risk_level"),
                "suggested_date": final.get("suggested_date"),
                "suggested_slot": final.get("suggested_slot"),
                "pending_confirmations": final.get("pending_confirmations"),
                "published": final.get("published"),
                "tool_calls": final.get("tool_calls"),
                "reasoning": final.get("reasoning"),
            },
            "runs": [
                {
                    "outcome": r.get("outcome"),
                    "draft_status": r.get("draft_status"),
                    "draft_id": r.get("draft_id"),
                    "published": r.get("published"),
                    "tool_calls": r.get("tool_calls"),
                }
                for r in item["runs"]
            ],
            "draft_count_after": item["draft_count"],
            "trace_file": f"examples/traces/{name}.jsonl",
        }
        (OUT_DIR / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rows.append((name, case.get("expect", ""), payload["actual"], item["draft_count"]))

    # 汇总所有用例产生的草稿（各用例独立库，重复提交只在其自身库内计一份）
    drafts: list[dict] = []
    for name in cases:
        case_db = ROOT / "data" / f".tmp_{name}.sqlite3"
        if case_db.exists():
            store = DraftStore(case_db)
            drafts.extend(store.list_drafts())
    (OUT_DIR / "drafts.json").write_text(
        json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# 实际输出汇总",
        "",
        "> 由 `python -m scripts.gen_outputs` 自动生成，与代码保持同步。",
        "",
        "| 用例 | 期望 | 实际 outcome | 实际 draft_status | 建议时段 | published | 草稿数 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, expect, actual, count in rows:
        lines.append(
            f"| `{name}` | {expect} | `{actual['outcome']}` | `{actual['draft_status']}` "
            f"| {actual['suggested_date']} {actual['suggested_slot'] or '-'} "
            f"| `{actual['published']}` | {count} |"
        )
    lines += [
        "",
        "## 逐条验收点核对",
        "",
        "| 验收点 | 用例 | 判定 |",
        "|---|---|---|",
        "| 正常排期 | `normal_ok` | 建议 10:00，等待确认 |",
        "| 素材信息缺失 | `missing_info` | 补查后仍缺失 -> blocked，不落库 |",
        "| 规则风险 | `rule_risk` | 敏感+禁用词 -> blocked |",
        "| 时段冲突 | `normal_conflict` | 18:00 被占 -> 建议 19:00 + 待确认，不落库 |",
        "| 重复提交 | `duplicate_submit_twice` | 第二次 duplicate，草稿表 1 行 |",
        "| 保存失败 | `save_failure` | failed，draft_id=null |",
        "",
        "## 草稿库快照",
        "",
        f"全部用例跑完后草稿库共 **{len(drafts)}** 行：",
        "",
        "```json",
        json.dumps(drafts, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    (OUT_DIR / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"已生成 {len(rows)} 个用例输出 -> {OUT_DIR}")
    print(f"草稿库共 {len(drafts)} 行")

    # 清理临时库
    for name in cases:
        case_db = ROOT / "data" / f".tmp_{name}.sqlite3"
        if case_db.exists():
            case_db.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
