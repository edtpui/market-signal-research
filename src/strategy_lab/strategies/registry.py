"""Strategy discovery: the built-in library plus user modules (settings [strategies] user_dir/*.py).

Keyed by (id, version), so an old version stays runnable for reproduction while a new one is added next to it.
A user module that fails to import is reported, not fatal; a duplicate (id, version) is an error.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
import traceback
from pathlib import Path

from strategy_lab.core.strategy import Strategy

BUILTIN_MODULES = ("strategy_lab.strategies.basic", "strategy_lab.strategies.ml")


class StrategyNotFound(KeyError):
    pass


class StrategyRegistry:
    def __init__(self):
        self._by_key: dict[tuple[str, str], type[Strategy]] = {}
        self._order: list[tuple[str, str]] = []
        self.errors: list[dict] = []
        self.sources: dict[tuple[str, str], str] = {}

    def register(self, cls: type[Strategy], source: str = "builtin") -> None:
        if not (inspect.isclass(cls) and issubclass(cls, Strategy)) or inspect.isabstract(cls):
            raise TypeError(f"{cls!r} is not a concrete Strategy subclass")
        for attr in ("id", "version"):
            if not isinstance(getattr(cls, attr, None), str) or not getattr(cls, attr):
                raise TypeError(f"{cls.__name__} needs a non-empty string `{attr}`")
        key = (cls.id, cls.version)
        if key in self._by_key and self._by_key[key] is not cls:
            raise ValueError(f"strategy {cls.id} version {cls.version} is defined twice "
                             f"({self.sources[key]} and {source})")
        self._by_key[key] = cls
        self.sources[key] = source
        if key not in self._order:
            self._order.append(key)

    def load_module(self, name: str) -> None:
        mod = importlib.import_module(name)
        for cls in _strategies_in(mod):
            self.register(cls, f"builtin:{name}")

    def load_dir(self, path: Path) -> None:
        path = Path(path)
        if not path.is_dir():
            return
        for f in sorted(path.glob("*.py")):
            if f.name.startswith("_"):
                continue
            modname = f"strategy_lab_user.{f.stem}"
            try:
                spec = importlib.util.spec_from_file_location(modname, f)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[modname] = mod
                spec.loader.exec_module(mod)
                for cls in _strategies_in(mod):
                    self.register(cls, f"user:{f.name}")
            except Exception as e:  # noqa: BLE001 - one broken user file must not hide the others
                self.errors.append(dict(file=str(f), error=f"{type(e).__name__}: {e}",
                                        traceback=traceback.format_exc(limit=5)))

    def get(self, strategy_id: str, version: str | None = None) -> type[Strategy]:
        if version is not None:
            try:
                return self._by_key[(strategy_id, version)]
            except KeyError:
                have = sorted(v for i, v in self._by_key if i == strategy_id)
                raise StrategyNotFound(f"strategy {strategy_id!r} version {version!r} is not available "
                                       f"(versions: {have or 'none'})") from None
        versions = [k for k in self._order if k[0] == strategy_id]
        if not versions:
            raise StrategyNotFound(f"no strategy {strategy_id!r} (have {sorted({k[0] for k in self._order})})")
        return self._by_key[versions[-1]]

    def all(self) -> list[type[Strategy]]:
        return [self._by_key[k] for k in self._order]

    def versions(self, strategy_id: str) -> list[str]:
        return [v for i, v in self._order if i == strategy_id]

    def describe(self) -> list[dict]:
        out = []
        for k in self._order:
            d = self._by_key[k].describe()
            d["source"] = self.sources[k]
            d["versions"] = self.versions(k[0])
            out.append(d)
        return out


def _strategies_in(mod) -> list[type[Strategy]]:
    return [c for _, c in inspect.getmembers(mod, inspect.isclass)
            if issubclass(c, Strategy) and c is not Strategy and not inspect.isabstract(c)
            and c.__module__ == mod.__name__ and getattr(c, "id", None)]


def default_registry(user_dir: Path | None = None) -> StrategyRegistry:
    reg = StrategyRegistry()
    for m in BUILTIN_MODULES:
        reg.load_module(m)
    if user_dir is not None:
        reg.load_dir(user_dir)
    return reg
