#!/usr/bin/env bash
set -euo pipefail

cd /Users/qingyuan/Documents/zhangxinqi

GRAPHIFY_BIN="${GRAPHIFY_BIN:-graphify}"

if [ -f graphify-out/graph.json ]; then
  "$GRAPHIFY_BIN" update .
else
  "$GRAPHIFY_BIN" extract . --out .
fi

"$GRAPHIFY_BIN" benchmark graphify-out/graph.json || true
