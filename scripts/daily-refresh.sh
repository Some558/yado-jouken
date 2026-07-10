#!/usr/bin/env bash
# ヤドシボリ日次更新(決定的・LLMゼロ)。fail-closed: validate非0なら promote/commit しない。
# 用法: ./scripts/daily-refresh.sh [--skip-fetch] [--as-of YYYY-MM-DD]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/pipeline"
export PATH="${ROOT}/.venv/bin:/opt/homebrew/bin:$PATH"

SKIP_FETCH=0
AS_OF=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-fetch) SKIP_FETCH=1; shift ;;
    --as-of) AS_OF="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
AS_OF="${AS_OF:-$(date +%F)}"
PY="${ROOT}/.venv/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"

echo "[daily] as-of=${AS_OF} skip_fetch=${SKIP_FETCH}"

if [[ "$SKIP_FETCH" -eq 0 ]]; then
  "$PY" pipeline/fetch.py --mode daily --as-of "$AS_OF"
fi

"$PY" pipeline/transform.py --as-of "$AS_OF"
"$PY" pipeline/validate.py

# promote: staged → latest (atomic-ish replace)
rm -rf data/latest
cp -R data/staged data/latest
echo "[daily] promoted data/staged → data/latest"
