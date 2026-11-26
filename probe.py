"""临时探针：快速检查检索主线（开发用，可删）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nds.api import NdsApp  # noqa: E402

CASES = [
    ("她说她从来没有恨过我，只是不想再见到我", "all"),
    ("林昭", "novel"),
    ("重生 复仇", "novel"),
    ("闪婚", "drama"),
    ("念念，你跑什么？我又不会吃了你", "drama"),
    ("侯府嫡女", "all"),
]

app = NdsApp()
print("corpus", json.dumps(app.corpus.stats(), ensure_ascii=False))
for query, tab in CASES:
    result = app.agent.search(query, tab)
    print(f"\nQ: {query!r} tab={tab} intent={result['understanding']['intent']} "
          f"degraded={result['degraded']} expanded={result['expanded']} stage={result['fallback_stage']} "
          f"elapsed={result['elapsed_ms']}ms")
    for kind, group in result["groups"].items():
        rows = [(x["work"]["title"], x["layer"], x["layer_label"], x["confidence"]) for x in group["results"][:3]]
        print(f"   {kind}: total={group['total']} {rows}")
    for reason in result["degraded_reasons"]:
        print("   ·", reason)
    for warning in result["warnings"]:
        print("   ⚠", warning)
    print("   platforms:", [(p["platform"], p["state"], p["used"]) for p in result["platforms"]])
app.close()
