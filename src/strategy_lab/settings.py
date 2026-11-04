"""Project settings (config/settings.toml). Relative paths resolve against the project root. STRATEGY_LAB_SETTINGS
points at another file (tests use one under tmp_path)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from strategy_lab.data.policy import DataPolicy

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = ROOT / "config" / "settings.toml"


@dataclass
class Settings:
    path: Path
    root: Path
    raw: dict
    policy: DataPolicy
    datasets: list[dict] = field(default_factory=list)

    def resolve(self, p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (self.root / p).resolve()

    def section(self, name: str) -> dict:
        return dict(self.raw.get(name, {}))

    @property
    def cache_dir(self) -> Path:
        return self.resolve(self.section("data").get("cache_dir", "var/cache"))

    @property
    def experiments_dir(self) -> Path:
        return self.resolve(self.section("store").get("experiments_dir", "var/experiments"))

    @property
    def catalog_path(self) -> Path:
        return self.resolve(self.section("store").get("catalog", "var/catalog.db"))

    @property
    def forward_dir(self) -> Path:
        return self.resolve(self.section("forward").get("dir", "var/forward"))

    @property
    def user_strategies_dir(self) -> Path:
        return self.resolve(self.section("strategies").get("user_dir", "strategies_user"))

    @property
    def signal_research_dir(self) -> Path:
        """Signal research experiments; by default next to the backtest experiments directory."""
        d = self.section("signal_research").get("dir")
        return self.resolve(d) if d else self.experiments_dir.parent / "signal_research"

    @property
    def entry_exit_dir(self) -> Path:
        """Entry-exit research experiments; by default next to the backtest experiments directory."""
        d = self.section("entry_exit").get("dir")
        return self.resolve(d) if d else self.experiments_dir.parent / "entry_exit"

    @property
    def entry_exit_imports_dir(self) -> Path:
        """Generated strategy modules of entry-exit cells imported into the Strategy Library."""
        d = self.section("entry_exit").get("imports_dir")
        return self.resolve(d) if d else self.experiments_dir.parent / "entry_exit_imports"

    @property
    def signal_imports_dir(self) -> Path:
        """Generated strategy modules of researched signals imported into the Strategy Library."""
        d = self.section("signal_research").get("imports_dir")
        return self.resolve(d) if d else self.experiments_dir.parent / "signal_imports"


def load(path: str | Path | None = None) -> Settings:
    p = Path(path or os.environ.get("STRATEGY_LAB_SETTINGS") or DEFAULT_SETTINGS)
    with open(p, "rb") as f:
        raw = tomllib.load(f)
    root = Path(raw.get("root", p.parent.parent if p == DEFAULT_SETTINGS else p.parent))
    root = root if root.is_absolute() else (p.parent / root).resolve()
    policy = DataPolicy.from_settings(raw.get("data", {}))
    return Settings(path=p, root=root, raw=raw, policy=policy, datasets=list(raw.get("dataset", [])))
