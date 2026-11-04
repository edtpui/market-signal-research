"""Run the CLI with src/ on the path, for launchers that cannot run scripts/sl.sh (no bash):
    <python> scripts/serve.py serve --port 8070
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_lab.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
