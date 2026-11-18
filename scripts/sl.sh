#!/usr/bin/env bash
# Run the CLI without installing anything: python -m strategy_lab "$@" with src/ on the path.
# Interpreter: $SL_PYTHON, else this project's .venv, else the shared research venv (../.venv-research) (verified on this host).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="${SL_PYTHON:-}"
if [[ -z "$py" ]]; then
  for c in "$here/.venv/Scripts/python.exe" "$here/.venv/bin/python" "$here/../.venv-research/Scripts/python.exe"; do
    if [[ -x "$c" ]]; then py="$c"; break; fi
  done
fi
[[ -n "$py" ]] || { echo "no interpreter found: set SL_PYTHON" >&2; exit 2; }
cd "$here"
PYTHONPATH="$here/src${PYTHONPATH:+:$PYTHONPATH}" PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 exec "$py" -m strategy_lab "$@"
