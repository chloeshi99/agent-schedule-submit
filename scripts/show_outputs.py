"""打印 examples/outputs 下各用例的关键结果，便于快速核对。"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "examples" / "outputs"

for path in sorted(OUT.glob("*.json")):
    if path.name == "drafts.json":
        continue
    d = json.loads(path.read_text(encoding="utf-8"))
    a = d["actual"]
    print(f"{d['case']:26} outcome={a['outcome']:18} draft={a['draft_status']:10} "
          f"slot={str(a['suggested_slot']):6} published={a['published']} drafts={d['draft_count_after']}")
    if len(d["runs"]) > 1:
        print("    runs:", [(r["outcome"], r["draft_status"]) for r in d["runs"]])
