#!/usr/bin/env bash
# 全仓一致性校验：Server 测试、Agent 测试、App 测试与构建
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Server pytest"
(cd server && python -m pytest tests)

echo "==> Agent pytest"
PYTHONPATH="$ROOT/agent${PYTHONPATH:+:$PYTHONPATH}" python -m pytest agent/tests

echo "==> App test + build"
cd app
[ -d node_modules ] || pnpm install
pnpm test
pnpm build

echo "全部校验通过 ✅"
