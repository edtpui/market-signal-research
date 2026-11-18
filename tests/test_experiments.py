"""Experiment store: resolved configs, immutable run directories, provenance, reproduction, saved configs, jobs."""
from __future__ import annotations

import json
import os
import stat

import pytest

from conftest import check_live_owner_is_left_alone, wait_done
from strategy_lab.data.policy import PolicyError
from strategy_lab.experiments.config import ExperimentConfig
from strategy_lab.experiments.store import ARTIFACTS, RunExists

CFG = dict(strategy="momentum_rotation", params={"lookback": 60, "top_k": 2, "rebalance_every": 10},
           dataset="synthetic", universe_name="syn", start="2019-01-02", end="2021-12-31", benchmark="SPYX",
           execution={"slippage_bps": 5, "commission": {"per_share": 0.005, "minimum": 1.0}})


def test_run_is_resolved_immutable_and_has_provenance(lab):
    run_id = lab.experiments.create(ExperimentConfig(**CFG, name="m1"))
    lab.experiments.execute(run_id)
    meta = lab.experiments.get(run_id)
    assert meta["status"] == "done" and meta["periods"] == ["IS"] and meta["summary"]["total_return"] is not None
    d = lab.experiments.path(run_id)
    cfg = json.loads((d / "config.json").read_text())
    assert cfg["dataset_version"] and cfg["universe"] == ["SYNA", "SYNB", "SYNC", "SYND", "SYNE"]
    assert cfg["strategy_version"] == "1.0"
    man = json.loads((d / "manifest.json").read_text())
    assert set(man["artifacts"]) == set(ARTIFACTS)
    assert len(man["engine"]["source_sha256"]) == 64 and len(man["strategy"]["source_sha256"]) == 64
    assert man["dataset"]["version"] == cfg["dataset_version"] and "EXPLORATORY" in man["label"]
    for f in d.iterdir():
        assert not os.stat(f).st_mode & stat.S_IWRITE, f"{f.name} is writable"
    with pytest.raises(RunExists):
        lab.experiments.execute(run_id)


def test_reproduction_is_byte_identical(lab):
    run_id = lab.experiments.create(ExperimentConfig(**CFG))
    lab.experiments.execute(run_id)
    rep = lab.experiments.reproduce(run_id)
    assert rep["identical"] and not rep["differing_artifacts"] and not rep["code_changed"]
    new = lab.experiments.get(rep["reproduction_run_id"])
    assert new["kind"] == "reproduction" and new["parent_run_id"] == run_id and new["reproduction"]["identical"]
    assert lab.experiments.get(run_id)["status"] == "done"                 # the original is untouched


def test_failures_are_recorded_and_bad_configs_fail_fast(lab):
    with pytest.raises(PolicyError):
        lab.experiments.create(ExperimentConfig(**{**CFG, "end": "2026-03-01"}))
    with pytest.raises(KeyError):
        lab.experiments.create(ExperimentConfig(**{**CFG, "strategy": "nope"}))
    bad = ExperimentConfig(**{**CFG, "strategy": "artifact_model", "params": {"artifact": "C:/no/such.pkl",
                                                                             "sha256": "0" * 64}})
    run_id = lab.experiments.create(bad)
    with pytest.raises(FileNotFoundError):
        lab.experiments.execute(run_id)
    meta = lab.experiments.get(run_id)
    assert meta["status"] == "failed" and "no file" in meta["error"]
    assert (lab.experiments.path(run_id) / "error.txt").exists()


def test_jobs_run_independent_experiments_in_parallel(lab):
    ids = [lab.experiments.create(ExperimentConfig(**{**CFG, "params": {**CFG["params"], "top_k": k}}))
           for k in (1, 2, 3)]
    for i in ids:
        lab.jobs.submit(i)
    metas = [wait_done(lab, i) for i in ids]
    assert all(m["status"] == "done" for m in metas)
    assert len({m["summary"]["total_return"] for m in metas}) == 3


def test_saved_configs_roundtrip(lab):
    cid = lab.experiments.save_config("momentum syn", ExperimentConfig(**CFG))
    got = lab.experiments.configs()
    assert got[0]["config_id"] == cid and got[0]["config"]["strategy"] == "momentum_rotation"
    assert lab.experiments.delete_config(cid) and not lab.experiments.configs()


def test_interrupted_runs_are_marked_failed(lab):
    run_id = lab.experiments.create(ExperimentConfig(**CFG))
    assert lab.experiments.recover_interrupted() == 1
    assert "interrupted" in lab.experiments.get(run_id)["error"]


def test_recovery_leaves_runs_of_a_live_process_alone(lab):
    """Before 2026-11-06 only entry-exit studies had owner files: starting the server marked a backtest that a script
    was executing as interrupted."""
    run_id = lab.experiments.create(ExperimentConfig(**CFG))
    assert json.loads((lab.experiments.path(run_id) / "owner.json").read_text())["pid"] == os.getpid()
    check_live_owner_is_left_alone(lab.experiments, run_id)


def test_store_paths_stay_inside_their_directories(lab):
    for store in (lab.experiments, lab.research, lab.entry_exit):
        for bad in ("..", "../experiments", "nope"):
            with pytest.raises(KeyError):
                store.path(bad)


def test_config_hash_ignores_labels():
    a = ExperimentConfig(**CFG, name="a", notes="x")
    b = ExperimentConfig(**CFG, name="b", notes="y")
    assert a.config_hash() == b.config_hash()
    assert a.config_hash() != ExperimentConfig(**{**CFG, "seed": 1}).config_hash()


def test_a_run_on_a_legacy_snapshot_still_reproduces(lab):
    """A dataset version written before calendar.parquet existed is read with its own (union) calendar."""
    run_id = lab.experiments.create(ExperimentConfig(**CFG))
    lab.experiments.execute(run_id)
    v = lab.store.version("synthetic", lab.experiments.config(run_id).dataset_version)
    (v.path / "calendar.parquet").unlink()
    lab.store._versions.clear()
    rep = lab.experiments.reproduce(run_id)
    assert rep["identical"], rep
