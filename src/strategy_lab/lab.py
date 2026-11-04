"""The Lab: settings, data store, strategy registry, experiment store, forward service and the Signal Research Lab
wired together once. The API, the CLI and the tests all go through it."""
from __future__ import annotations

import threading
from pathlib import Path

from strategy_lab import settings as settings_mod
from strategy_lab.data.providers import DatasetSpec
from strategy_lab.data.store import DataStore
from strategy_lab.experiments.backtest import BacktestResult, Universes, resolve, run_backtest
from strategy_lab.experiments.config import ExperimentConfig
from strategy_lab.experiments.store import ExperimentStore, JobRunner
from strategy_lab.strategies.registry import default_registry


class Lab:
    def __init__(self, settings: settings_mod.Settings | None = None, workers: int = 2):
        self.settings = settings or settings_mod.load()
        s = self.settings
        self.policy = s.policy
        self.store = DataStore(s.cache_dir, s.policy, [DatasetSpec.from_settings(d, s.resolve) for d in s.datasets])
        self.registry = default_registry(s.user_strategies_dir)
        u = s.section("universes")
        sp = u.pop("sp1500_file", None)
        self.universes = Universes(named={k: list(v) for k, v in u.items() if isinstance(v, list)},
                                   sp1500_file=s.resolve(sp) if sp else None)
        self.experiments = ExperimentStore(s.experiments_dir, s.catalog_path, self)
        self.jobs = JobRunner(self.experiments, workers=workers)
        from strategy_lab.paper.service import ForwardService
        self.forward = ForwardService(self)

        # Signal Research Lab: the signal catalog, reference data, the research store, and researched signals
        # imported into the Strategy Library (generated modules, loaded like strategies_user/)
        from strategy_lab.research.reference import ReferenceData
        from strategy_lab.research.store import SignalResearchStore
        from strategy_lab.signals.library import default_signal_registry
        sr = s.section("signal_research")
        self.signals = default_signal_registry()
        self.reference = ReferenceData(sector_file=s.resolve(sr["sector_file"]) if sr.get("sector_file") else None,
                                       market_cap_file=s.resolve(sr["market_cap_file"])
                                       if sr.get("market_cap_file") else None)
        self.research = SignalResearchStore(s.signal_research_dir, s.catalog_path, self,
                                            workers=int(sr.get("workers", 1)))
        self.settings_signal_imports_dir = s.signal_imports_dir
        self._import_lock = threading.Lock()
        self.registry.load_dir(s.signal_imports_dir)

    def resolve(self, cfg: ExperimentConfig):
        return resolve(cfg, self.store, self.registry, self.universes, self.policy)

    def backtest(self, cfg: ExperimentConfig, data=None) -> BacktestResult:
        return run_backtest(cfg, self.store, self.registry, self.universes, self.policy, data=data)

    def import_signal(self, experiment_id: str, signal_key: str, name: str = "", defaults: dict | None = None) -> dict:
        from strategy_lab.research.imports import import_signal
        return import_signal(self, experiment_id, signal_key, name, defaults)

    def close(self) -> None:
        self.jobs.shutdown()
        self.research.shutdown()


def default_lab(path: str | Path | None = None) -> Lab:
    return Lab(settings_mod.load(path))
