"""What the three experiment stores (backtests, signal studies, entry-exit studies) share, implemented once.

Each store keeps one write-once directory per experiment and indexes it in its own table of the catalog database
(var/catalog.db). Shared here: the catalog connection; reading, listing and updating an experiment row; recovery of
rows that a stopped process left queued or running (an owner file names the process executing an experiment, so a
second process never marks a live one failed); saved configurations; the read-only finish of a directory; and the
provenance every manifest records (source hashes, git state, package versions).

Nothing here computes results. Each manifest's source hash covers the directories that do (the stores pass them to
source_sha256), so this module is deliberately outside them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import sqlite3
import stat
import subprocess
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

import pandas as pd
from pydantic import BaseModel

import strategy_lab

PACKAGE = Path(strategy_lab.__file__).resolve().parent


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(catalog: Path) -> sqlite3.Connection:
    c = sqlite3.connect(catalog, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# -- provenance ------------------------------------------------------------------------------------------------------
def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def source_sha256(dirs: Iterable[str], extra_files: Iterable[str] = ()) -> str:
    """sha256 over every .py file under the given package directories (path, then bytes, in sorted order), then
    the bytes of each extra file. The order and the bytes hashed are part of every recorded hash: do not change them."""
    h = hashlib.sha256()
    for d in dirs:
        for f in sorted((PACKAGE / d).rglob("*.py")):
            h.update(f.relative_to(PACKAGE).as_posix().encode())
            h.update(f.read_bytes())
    for f in extra_files:
        h.update((PACKAGE / f).read_bytes())
    return h.hexdigest()


def git_state() -> dict:
    root = PACKAGE.parents[1]
    try:
        head = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "status", "--porcelain", "src"],
                               capture_output=True, text=True, timeout=10)
        return dict(commit=head.stdout.strip() or None, src_dirty=bool(dirty.stdout.strip()))
    except (OSError, subprocess.SubprocessError):
        return dict(commit=None, src_dirty=None)


def package_versions() -> dict:
    import duckdb
    import numpy
    import pydantic
    return dict(python=platform.python_version(), platform=platform.platform(), numpy=numpy.__version__,
                pandas=pd.__version__, duckdb=duckdb.__version__, pydantic=pydantic.__version__,
                strategy_lab=strategy_lab.__version__)


# -- write-once directories ------------------------------------------------------------------------------------------
def make_readonly(path: Path) -> None:
    for p in sorted(Path(path).rglob("*")):
        if p.is_file():
            os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def write_owner(d: Path) -> None:
    """Names the process that creates, then executes, an experiment: recovery leaves it alone while it lives."""
    (d / "owner.json").write_text(json.dumps(dict(pid=os.getpid(), host=platform.node(), at_utc=now_utc())),
                                  encoding="utf-8")


def pid_alive(pid) -> bool:
    """Whether a process with this id exists on this host (standard library only)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owned_by_another_live_process(d: Path) -> bool:
    """True when the directory's owner file names another process on this host that is still running. Without an
    owner file (directories made before owner files existed) the experiment counts as unowned."""
    f = d / "owner.json"
    try:
        own = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except (OSError, ValueError):
        own = None
    return bool(own and own.get("host") == platform.node() and own.get("pid") != os.getpid() and pid_alive(own["pid"]))


# -- the stores ------------------------------------------------------------------------------------------------------
class CatalogStore:
    """One store: its experiment table and directories, and its saved configurations. Subclasses set the names."""

    TABLE: str                      # catalog table of experiments
    KEY: str                        # its primary-key column
    CONFIGS_TABLE: str              # saved configurations (config_id, name, config JSON, created_at_utc)
    CONFIG_ID_PREFIX = ""
    CONFIG_ID_HEX = 12
    CONFIG_MODEL: type[BaseModel]
    LABEL = "experiment"            # in "no <label> <id>" errors
    INTERRUPTED = "interrupted: the process stopped before the experiment finished"
    JSON_COLS: tuple[str, ...] = ("summary", "reproduction")
    BOOL_COLS: tuple[str, ...] = ()

    def __init__(self, root: Path, catalog: Path, lab, ddl: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._root_resolved = self.root.resolve()
        self.catalog_path = Path(catalog)
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.lab = lab
        with self._db() as c:
            c.executescript(ddl)

    def _db(self) -> sqlite3.Connection:
        return connect(self.catalog_path)

    def _set(self, eid: str, **cols) -> None:
        keys = ", ".join(f"{k}=?" for k in cols)
        with self._db() as c:
            c.execute(f"UPDATE {self.TABLE} SET {keys} WHERE {self.KEY}=?", (*cols.values(), eid))

    def _row(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in self.JSON_COLS:
            d[k] = json.loads(d[k]) if d.get(k) else None
        d["periods"] = d["periods"].split(",") if d.get("periods") else []
        for k in self.BOOL_COLS:
            d[k] = bool(d.get(k))
        return d

    def get(self, eid: str) -> dict | None:
        with self._db() as c:
            r = c.execute(f"SELECT * FROM {self.TABLE} WHERE {self.KEY}=?", (eid,)).fetchone()
        return None if r is None else self._row(r)

    def list(self, limit: int = 500) -> list[dict]:
        with self._db() as c:
            rows = c.execute(f"SELECT * FROM {self.TABLE} ORDER BY created_at_utc DESC, {self.KEY} DESC LIMIT ?",
                             (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def recover_interrupted(self) -> int:
        """At startup: an experiment left queued or running is marked failed, with the reason, unless its owner file
        names another live process (a script may be executing it)."""
        with self._db() as c:
            ids = [r[0] for r in c.execute(f"SELECT {self.KEY} FROM {self.TABLE} "
                                           "WHERE status IN ('running', 'queued')")]
        n = 0
        for eid in ids:
            if owned_by_another_live_process(self.root / eid):
                continue
            with self._db() as c:
                n += c.execute(f"UPDATE {self.TABLE} SET status='failed', finished_at_utc=?, error=? WHERE "
                               f"{self.KEY}=? AND status IN ('running', 'queued')",
                               (now_utc(), self.INTERRUPTED, eid)).rowcount
        return n

    # -- directories --------------------------------------------------------------------------------------------
    def path(self, eid: str) -> Path:
        p = (self.root / eid).resolve()
        if self._root_resolved not in p.parents or not (p / "config.json").exists():
            raise KeyError(f"no {self.LABEL} {eid}")
        return p

    def config(self, eid: str):
        return self.CONFIG_MODEL.model_validate_json((self.path(eid) / "config.json").read_text(encoding="utf-8"))

    def read_json(self, eid: str, name: str):
        p = self.path(eid) / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # -- saved configurations -----------------------------------------------------------------------------------
    def save_config(self, name: str, cfg: BaseModel) -> str:
        cid = f"{self.CONFIG_ID_PREFIX}{uuid.uuid4().hex[:self.CONFIG_ID_HEX]}"
        with self._db() as c:
            c.execute(f"INSERT INTO {self.CONFIGS_TABLE} VALUES (?,?,?,?)",
                      (cid, name, cfg.model_dump_json(), now_utc()))
        return cid

    def configs(self) -> list[dict]:
        with self._db() as c:
            rows = c.execute(f"SELECT * FROM {self.CONFIGS_TABLE} ORDER BY created_at_utc DESC").fetchall()
        return [dict(config_id=r["config_id"], name=r["name"], created_at_utc=r["created_at_utc"],
                     config=json.loads(r["config"])) for r in rows]

    def delete_config(self, config_id: str) -> bool:
        with self._db() as c:
            return c.execute(f"DELETE FROM {self.CONFIGS_TABLE} WHERE config_id=?", (config_id,)).rowcount > 0


class QueuedStore(CatalogStore):
    """A store whose experiments run on its own thread pool and can be cancelled (signal and entry-exit studies).
    Subclasses provide execute(eid)."""

    def __init__(self, root: Path, catalog: Path, lab, ddl: str, workers: int, thread_name: str):
        super().__init__(root, catalog, lab, ddl)
        self._cancel: dict[str, threading.Event] = {}
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=thread_name)
        self.futures: dict[str, Future] = {}

    def submit(self, eid: str) -> Future:
        f = self.pool.submit(self._safe_execute, eid)
        self.futures[eid] = f
        return f

    def _safe_execute(self, eid: str):
        try:
            return self.execute(eid)
        except Exception:  # noqa: BLE001 - recorded on the experiment
            return None

    def cancel(self, eid: str) -> dict:
        """A running experiment stops at its next check; a queued one is withdrawn and recorded as cancelled."""
        meta = self.get(eid)
        if meta is None:
            raise KeyError(f"no {self.LABEL} {eid}")
        if meta["status"] not in ("queued", "running"):
            return dict(experiment_id=eid, status=meta["status"], cancelled=False)
        self._cancel.setdefault(eid, threading.Event()).set()
        if meta["status"] == "queued":
            f = self.futures.get(eid)
            if f is not None and f.cancel():
                self._set(eid, status="cancelled", finished_at_utc=now_utc(), error="cancelled before it started")
                make_readonly(self.root / eid)
        return dict(experiment_id=eid, status="cancelling", cancelled=True)
