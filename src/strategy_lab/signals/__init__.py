"""Signal Research: independently testable signal definitions (signals/library), a registry keyed by (id, version),
and a vectorized, causal indicator cache. Signals know nothing about portfolios or execution; the event-study engine
(strategy_lab.research) and the imported-signal strategy (strategies/signal_rule.py) both evaluate them through
signals.compose.evaluate()."""
