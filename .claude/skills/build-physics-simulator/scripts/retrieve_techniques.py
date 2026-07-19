#!/usr/bin/env python3
"""Retrieve conditional high-school physics shortcuts without network access."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DEFAULT_DB = Path(__file__).resolve().parent.parent / "references" / "secondary-conclusions.json"


def tokens(text: str) -> set[str]:
    latin = re.findall(r"[a-zA-Z0-9_+-]+", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(chinese[i:i + 2]) for i in range(max(0, len(chinese) - 1))]
    return set(latin + chinese + bigrams)


def score(item: dict, query: str) -> float:
    q = tokens(query)
    fields = {
        "triggers": " ".join(item.get("triggers", [])),
        "module": item.get("module", ""),
        "conclusion": item.get("conclusion", ""),
        "conditions": " ".join(item.get("conditions", [])),
    }
    weights = {"triggers": 4.0, "module": 2.0, "conclusion": 1.5, "conditions": 0.5}
    return sum(weights[name] * len(q & tokens(value)) for name, value in fields.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ids", nargs="*", default=[])
    args = parser.parse_args()
    items = json.loads(args.db.read_text(encoding="utf-8"))
    wanted = set(args.ids)
    ranked = []
    for item in items:
        value = 1000.0 if item["id"] in wanted else score(item, args.query)
        if value > 0:
            ranked.append({"score": value, **item})
    ranked.sort(key=lambda x: (-x["score"], x["id"]))
    print(json.dumps(ranked[:max(1, args.top_k)], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
