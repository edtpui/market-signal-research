"""The built-in signal catalog. Each module registers its families and signals; default_signal_registry() builds the
registry once per process. Bump a signal's `version` whenever its calculation changes: results record (id, version,
source sha256), and a reproduction reports a changed source instead of hiding it."""
from __future__ import annotations

import threading

from strategy_lab.signals.base import SignalRegistry

MODULES = ("trend", "momentum", "volatility", "volume", "price", "market")

_lock = threading.Lock()
_registry: SignalRegistry | None = None


def build_registry() -> SignalRegistry:
    import importlib
    reg = SignalRegistry()
    for m in MODULES:
        importlib.import_module(f"strategy_lab.signals.library.{m}").register(reg)
    return reg


def default_signal_registry() -> SignalRegistry:
    global _registry
    with _lock:
        if _registry is None:
            _registry = build_registry()
        return _registry
