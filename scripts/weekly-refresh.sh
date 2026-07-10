#!/usr/bin/env bash
# ヤドシボリ週次更新: squeeze条件集合 + 設備キャッシュ更新 → 日次と同じ transform/validate/promote。
# 用法: ./scripts/weekly-refresh.sh [--as-of YYYY-MM-DD]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/pipeline"
export PATH="${ROOT}/.venv/bin:/opt/homebrew/bin:$PATH"

AS_OF=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --as-of) AS_OF="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
AS_OF="${AS_OF:-$(date +%F)}"
PY="${ROOT}/.venv/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"

echo "[weekly] as-of=${AS_OF}"
# facilities の対象抽出に daily 一覧が必要なので先に daily
"$PY" pipeline/fetch.py --mode daily --as-of "$AS_OF"
"$PY" pipeline/fetch.py --mode weekly --as-of "$AS_OF"
"$PY" pipeline/transform.py --as-of "$AS_OF"
"$PY" pipeline/validate.py
rm -rf data/latest
cp -R data/staged data/latest
echo "[weekly] promoted data/staged → data/latest (+ squeeze/facilities updated)"
