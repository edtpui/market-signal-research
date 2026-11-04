"""The Lab: settings, data store, strategy registry, experiment store and forward service wired together once.
The API, the CLI and the tests all go through it."""
from __future__ import annotations

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

    def resolve(self, cfg: ExperimentConfig):
        return resolve(cfg, self.store, self.registry, self.universes, self.policy)

    def backtest(self, cfg: ExperimentConfig, data=None) -> BacktestResult:
        return run_backtest(cfg, self.store, self.registry, self.universes, self.policy, data=data)

    def close(self) -> None:
        self.jobs.shutdown()


def default_lab(path: str | Path | None = None) -> Lab:
    return Lab(settings_mod.load(path))
