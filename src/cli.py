"""命令行入口。

用法示例：
    # 运行具名用例
    python -m src.cli --case normal_conflict
    python -m src.cli --case all

    # 自由请求
    python -m src.cli --material M01 --request "把 M01 排到 2026-11-02 18:00"
    python -m src.cli --material M05 --request "把 M05 排到 2026-11-02 10:00" --confirm

    # 查看草稿库
    python -m src.cli --list-drafts

退出码：
    0  正常结束（saved / needs_confirmation / blocked 等均为预期结果）
    1  参数错误或运行失败
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .runner import AgentRunner, load_cases, run_case, summarize


def _print_result(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _run_single(args: argparse.Namespace, config: Config) -> int:
    runner = AgentRunner(config)
    trace_path = None
    if args.trace:
        trace_path = Path(args.trace)
    result = runner.run(
        user_request=args.request,
        material_id=args.material,
        confirmed=True if args.confirm else (False if args.no_confirm else None),
        target_date=args.date,
        target_start=args.time,
        trace_path=trace_path,
    )
    if args.json:
        _print_result(result)
    else:
        print(summarize(result))
        if result.get("reasoning"):
            print(f"说明: {result['reasoning']}")
        for c in result.get("pending_confirmations") or []:
            print(f"待确认: {c.get('question')}")
    return 0


def _run_case(args: argparse.Namespace, config: Config) -> int:
    trace_dir = Path(args.trace_dir) if args.trace_dir else None
    if args.case == "all":
        cases = load_cases(config)["cases"]
        results = []
        for name in cases:
            results.append(run_case(name, config=config, trace_dir=trace_dir))
        for item in results:
            final = item["final"]
            print(f"[{item['case']}] {summarize(final)}")
            print(f"    期望: {item['expect']}")
        print(f"\n草稿库总数: {results[-1]['draft_count']}")
        if args.json:
            _print_result(results)
        return 0

    item = run_case(args.case, config=config, trace_dir=trace_dir)
    if args.json:
        _print_result(item)
    else:
        print(f"用例: {item['case']}")
        print(f"请求: {item['user_request']}")
        print(f"期望: {item['expect']}")
        print(f"实际: {summarize(item['final'])}")
        if item["final"].get("reasoning"):
            print(f"说明: {item['final']['reasoning']}")
        for c in item["final"].get("pending_confirmations") or []:
            print(f"待确认: {c.get('question')}")
        print(f"草稿库总数: {item['draft_count']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="素材审核与发布排期 Agent（离线可运行）",
    )
    parser.add_argument("--case", help="运行具名用例，或 all 运行全部用例")
    parser.add_argument("--material", help="素材 ID，如 M01")
    parser.add_argument("--request", default="", help="用户请求原文")
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD")
    parser.add_argument("--time", help="目标时刻 HH:MM")
    parser.add_argument("--confirm", action="store_true", help="用户已确认排期")
    parser.add_argument("--no-confirm", action="store_true", help="用户未确认排期")
    parser.add_argument("--list-drafts", action="store_true", help="列出草稿库内容")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument("--trace", help="单次运行的 trace 输出文件（JSONL）")
    parser.add_argument("--trace-dir", help="用例运行的 trace 输出目录")
    parser.add_argument("--db", help="草稿库路径（默认 data/drafts.sqlite3）")
    args = parser.parse_args(argv)

    config = Config.load()
    if args.db:
        config.db_path = Path(args.db)

    if args.list_drafts:
        runner = AgentRunner(config)
        drafts = runner.list_drafts()
        if args.json:
            _print_result(drafts)
        else:
            print(f"草稿数: {len(drafts)}")
            for d in drafts:
                print(f"  {d['draft_id']} | {d['material_id']} | {d['date']} {d['start_time']}"
                      f" | {d['channel']} | {d['status']} | published={d['payload'] and False}")
        return 0

    if args.case:
        return _run_case(args, config)

    if not args.request:
        parser.error("需要 --case 或 --request")
    if not args.material and not args.request:
        parser.error("需要 --material")
    return _run_single(args, config)


if __name__ == "__main__":
    sys.exit(main())
