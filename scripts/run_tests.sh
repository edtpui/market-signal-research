#!/usr/bin/env bash
# The test suite: synthetic fixtures in pytest tmp dirs, plus read-only real-data checks (marked realdata) that skip
# when the workspace data is absent.   bash scripts/run_tests.sh [pytest args]   e.g. -m "not realdata"
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="${SL_PYTHON:-}"
if [[ -z "$py" ]]; then
  for c in "$here/.venv/Scripts/python.exe" "$here/.venv/bin/python" "$here/../paper_trading/.venv/Scripts/python.exe"; do
    if [[ -x "$c" ]]; then py="$c"; break; fi
  done
fi
cd "$here"
PYTHONPATH="$here/src${PYTHONPATH:+:$PYTHONPATH}" PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 "$py" -m pytest tests -q -p no:cacheprovider "$@"
