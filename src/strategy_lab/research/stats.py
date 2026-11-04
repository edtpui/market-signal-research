"""Statistics for signal research: clustered bootstrap, randomization p-values, Benjamini-Hochberg, and the
classification that separates statistical from economic significance. numpy only (no scipy on this host).

Dependence: events on one session share market shocks (cross-sectional correlation) and an h-session holding period
overlaps the next h-1 sessions' events (serial dependence). The bootstrap therefore resamples clusters: blocks of
consecutive sessions of length max(h, 1) (each block keeps every event of its sessions), and separately whole
tickers; the reported standard error is the larger of the two.
"""
from __future__ import annotations

import math

import numpy as np


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def cluster_bootstrap(values: np.ndarray, clusters: np.ndarray, n_boot: int, rng: np.random.Generator,
                      ) -> dict:
    """Mean of values with a cluster bootstrap. values: per event (finite); clusters: integer cluster id per event.
    Returns mean, se, 95% percentile interval and the bootstrap means' count. Fewer than 2 clusters: se undefined."""
    v = np.asarray(values, dtype=float)
    c = np.asarray(clusters)
    n = len(v)
    out = dict(mean=float(v.mean()) if n else None, se=None, ci=[None, None], clusters=0)
    if n == 0:
        return out
    ids, inv = np.unique(c, return_inverse=True)
    k = len(ids)
    out["clusters"] = int(k)
    if k < 2 or n_boot < 10:
        return out
    s = np.bincount(inv, weights=v, minlength=k)
    m = np.bincount(inv, minlength=k).astype(float)
    means = np.empty(n_boot)
    step = max(1, int(4_000_000 // k))                  # bound the index matrix to ~32 MB
    for a in range(0, n_boot, step):
        b = min(n_boot, a + step)
        idx = rng.integers(0, k, size=(b - a, k))
        means[a:b] = s[idx].sum(axis=1) / m[idx].sum(axis=1)
    out["se"] = float(means.std(ddof=1))
    out["ci"] = [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]
    return out


def block_ids(date_idx: np.ndarray, block: int) -> np.ndarray:
    return np.asarray(date_idx) // max(1, int(block))


def directional_p_from_null(actual: float, null: np.ndarray, direction: str) -> dict:
    """Empirical randomization p-value: (1 + #null at least as extreme in the signal's direction) / (B + 1).
    Also the two-sided version and a normal approximation from the null's mean and sd (no 1/(B+1) floor)."""
    null = np.asarray(null, dtype=float)
    null = null[np.isfinite(null)]
    B = len(null)
    if B == 0 or actual is None or not np.isfinite(actual):
        return dict(p=None, p_two_sided=None, p_z=None, z=None, percentile=None, null_mean=None, null_sd=None,
                    null_q=[None, None], permutations=B)
    hi = int((null >= actual).sum())
    lo = int((null <= actual).sum())
    p_dir = (1 + (hi if direction == "bullish" else lo)) / (B + 1)
    p_two = min(1.0, 2 * (1 + min(hi, lo)) / (B + 1))
    mu, sd = float(null.mean()), float(null.std(ddof=1)) if B > 1 else 0.0
    z = (actual - mu) / sd if sd > 0 else None
    p_z = None if z is None else (1 - norm_cdf(z) if direction == "bullish" else norm_cdf(z))
    return dict(p=p_dir, p_two_sided=p_two, p_z=p_z, z=z, percentile=float((null < actual).mean()),
                null_mean=mu, null_sd=sd, null_q=[float(np.percentile(null, 2.5)), float(np.percentile(null, 97.5))],
                permutations=B)


def bootstrap_p(mean: float | None, se: float | None, direction: str) -> float | None:
    """One-sided p for the mean being on the signal's side of zero, normal approximation with a clustered se."""
    if mean is None or se is None or se <= 0:
        return None
    z = mean / se
    return 1 - norm_cdf(z) if direction == "bullish" else norm_cdf(z)


def benjamini_hochberg(p: list[float | None]) -> list[float | None]:
    """BH-adjusted q-values (step-up, monotone). None stays None and does not count as a test."""
    idx = [i for i, x in enumerate(p) if x is not None and np.isfinite(x)]
    out: list[float | None] = [None] * len(p)
    m = len(idx)
    if m == 0:
        return out
    vals = np.array([p[i] for i in idx], dtype=float)
    order = np.argsort(vals, kind="mergesort")
    ranked = vals[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    q = np.minimum(q, 1.0)
    for pos, o in enumerate(order):
        out[idx[o]] = float(q[pos])
    return out


VERDICTS = ("supported", "significant_negligible", "reversed", "not_significant", "insufficient_sample")


def verdict(*, n: int, n_dates: int, direction: str, excess: float | None, excess_net: float | None,
            q: float | None, ci: list, horizon: int, periods_per_year: float, rule: str, alpha: float,
            econ_annual: float, min_events: int) -> dict:
    """Classifies one (signal, horizon) test. Signs are taken in the signal's direction: a bearish signal is
    'supported' when its excess return is negative.

    statistically significant: q (BH over the experiment's tests) < alpha under `rule`; with
      randomization_and_bootstrap the clustered bootstrap CI of the excess must also exclude zero on the signal's side.
    economically significant: the net excess (after round-trip costs) in the signal's direction, annualized by the
      holding period (x periods_per_year / h, i.e. capital continuously redeployed), is at least econ_annual.
    """
    sgn = 1.0 if direction == "bullish" else -1.0
    out = dict(category="insufficient_sample", significant=False, economic=False, annualized_net_excess=None,
               reason="")
    if n < min_events or n_dates < 10 or excess is None:
        out["reason"] = f"{n} events on {n_dates} sessions (need >= {min_events} events on >= 10 sessions)"
        return out
    ann = None if excess_net is None else sgn * excess_net * periods_per_year / max(1, horizon)
    out["annualized_net_excess"] = ann
    ci_ok = ci[0] is not None and ci[1] is not None and ((ci[0] > 0) if sgn > 0 else (ci[1] < 0))
    ci_opp = ci[0] is not None and ci[1] is not None and ((ci[1] < 0) if sgn > 0 else (ci[0] > 0))
    q_ok = q is not None and q < alpha
    if rule == "randomization":
        sig = q_ok
    elif rule == "bootstrap":
        sig = ci_ok
    else:
        sig = q_ok and ci_ok
    econ = ann is not None and ann >= econ_annual
    out.update(significant=bool(sig), economic=bool(econ))
    if sig and econ:
        out.update(category="supported", reason="significant in the signal's direction and economically meaningful")
    elif sig:
        out.update(category="significant_negligible",
                   reason=f"significant, but the annualized net excess {ann if ann is None else round(ann, 4)} is "
                          f"below {econ_annual}")
    elif ci_opp:
        out.update(category="reversed", reason="the clustered CI excludes zero on the opposite side")
    else:
        out.update(category="not_significant", reason="no reliable difference from the controls")
    return out
