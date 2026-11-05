"""Human-readable API errors. Every error response keeps `detail` as one readable sentence (the UI shows it as the
headline) and adds `problems` (one line per problem, with the field it concerns in plain words) and `hint` (what to
change). Pydantic's own messages are kept inside each line so nothing is lost."""
from __future__ import annotations

import re

LABELS = {
    "entries": "Entry signal", "entry_families": "Entry family", "entry_grids": "Entry parameter grid",
    "exits": "Exit plan", "exit_ids": "Exit", "exit_families": "Exit family", "exit_grids": "Exit sweep",
    "protective": "Protective stop", "components": "component", "params": "parameter", "grid": "values",
    "random_entries": "Random entries", "timing": "Timing benchmark", "calibration": "Calibration",
    "controls": "Random controls", "portfolio": "Portfolio", "sizing": "Sizing", "execution": "Costs",
    "commission": "commission", "spread": "spread", "eligibility": "Eligibility", "matching": "Matching",
    "walk_forward": "Walk-forward", "stats": "Statistics", "partitions": "Partition", "universe": "Universe",
    "universe_name": "Universe", "dataset": "Dataset", "start": "Start date", "end": "End date",
    "max_hold": "Max holding", "ref_atr_n": "Reference ATR", "ref_atr_mult": "Reference ATR multiple",
    "trade_notional": "Trade notional", "benchmarks": "Benchmark", "sampling": "Event sampling", "seed": "Seed",
    "signals": "Signal", "families": "Signal family", "grids": "Parameter grid", "horizons": "Horizons",
    "strategy": "Strategy", "initial_capital": "Initial capital", "spec": "Deployment",
}


def _field(loc) -> str:
    parts, prev = [], None
    for x in loc:
        if x in ("body", "query", "path"):
            continue
        if isinstance(x, int):
            parts.append(f"{parts.pop() if parts else 'item'} {x + 1}")
            prev = x
            continue
        if x in ("SignalSpec", "CombinationSpec", "signal", "combination", "plan", "function-after[_check(), "
                 "ExitPlanSpec]") or str(x).startswith(("function-", "tagged-union")):
            continue
        parts.append(LABELS.get(str(x), str(x).replace("_", " ")))
        prev = x
    return " › ".join(parts) or "Settings"


def _message(msg: str) -> str:
    msg = re.sub(r"^(Value|Assertion) error, ", "", msg)
    return msg[:1].upper() + msg[1:] if msg else msg


def validation_problems(errors: list[dict]) -> list[str]:
    """Pydantic / FastAPI validation errors as readable lines: 'Entry signal 2 › id: Field required'."""
    out = []
    for e in errors:
        msg = _message(str(e.get("msg", "")))
        typ = e.get("type", "")
        if typ == "missing":
            msg = "is required"
        elif typ == "extra_forbidden":
            msg = "is not a setting here (remove it or check its spelling)"
        field_ = _field(e.get("loc", ()))
        line = msg if field_ == "Settings" else f"{field_}: {msg}"
        if line not in out:
            out.append(line)
    return out[:25]


HINTS = (
    (r"ceiling|HOLDOUT|backtest floor", "Choose dates between 2019-01-02 and 2025-12-31 (2026 onward is HOLDOUT data "
                                        "and cannot be used here)."),
    (r"last ingested session|first session", "Pick dates inside the dataset's coverage (Market Data shows it), or "
                                             "re-ingest the dataset."),
    (r"start .* is after end|start is after end", "Set the start date before the end date."),
    (r"must be bullish|bearish", "Entries open long positions: choose a bullish signal (bearish signals can be used "
                                 "as exits through 'Any catalog signal')."),
    (r"no exit '|unknown exit|no exit family", "Pick the exit from the Exit Library list (ids are case-sensitive)."),
    (r"no signal '|signal family|version .* is not available", "Pick the signal from the catalog list; a version "
                                                               "that no longer exists cannot be reproduced."),
    (r"unknown parameters|below the minimum|above the maximum|is not one of|expected an integer|expected a number",
     "Fix the parameter in the component's editor: the allowed range is shown next to each field."),
    (r"at most \d+ (cells|entries|exit plans)", "Reduce the number of entries or exits (families and sweeps "
                                                 "multiply quickly)."),
    (r"universe|symbols", "Choose a named universe or enter at least two symbols that exist in the dataset."),
    (r"locked test partition|evaluate_test", "Move the end date before 2025, or turn on 'Evaluate the locked test "
                                             "partition' (it is recorded and can only be spent once)."),
    (r"k_of_n needs k|weighted needs a threshold", "Set k (for at least k of n) or the score threshold (for "
                                                   "weighted) in the plan or combination."),
    (r"select at least one", "Add at least one entry (or turn on random entries) and at least one exit."),
)


def hint_for(text: str) -> str | None:
    for pat, hint in HINTS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return hint
    return None


def friendly_body(exc: Exception, context: str = "") -> dict:
    """detail / problems / hint for any exception raised while validating or creating an experiment."""
    errs = getattr(exc, "errors", None)
    if callable(errs) and not isinstance(exc, (KeyError, LookupError)):
        problems = validation_problems(errs())
        n = len(problems)
        ctx = context or "The settings"
        verb = "have" if n != 1 or ctx.endswith("settings") else "has"
        detail = (f"{ctx} {verb} {n} problem{'s' if n != 1 else ''}: " + "; ".join(problems[:3])
                  + ("…" if n > 3 else ""))
        return dict(detail=detail, problems=problems, hint=hint_for(" ".join(problems)))
    text = str(exc).strip("'\"")
    if isinstance(exc, KeyError) and exc.args:
        text = str(exc.args[0])
    detail = _message(text)
    if context:
        detail = f"{context}: {detail[:1].lower() + detail[1:]}"
    return dict(detail=detail, problems=[_message(text)], hint=hint_for(text))


__all__ = ["validation_problems", "friendly_body", "hint_for"]
