// Entry-Exit Research: types mirroring /api/v1/entry-exit (src/strategy_lab/api/entry_exit.py) and their hooks.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, qs, type ExecutionConfig, type ParamSpec, type Policy, type Row } from "@/api/client";
import type { AnySpec, Partition, Verdict } from "@/api/signals";

export type ExitKind = "condition" | "stop" | "target";
export interface ExitInfo {
  id: string; version: string; family: string; kind: ExitKind; name: string; rule: string; doc: string;
  params: Record<string, ParamSpec>; fields: string[]; needs_benchmark: boolean; warmup: number; intrabar: boolean; role: string;
  style?: string; lookahead?: boolean; shiftable?: boolean;
}
export interface Catalog {
  roles: { id: string; name: string; description: string }[];
  exits: ExitInfo[]; exit_families: { id: string; name: string; description: string; n_exits: number }[]; n_exits: number;
  entry_signals: { id: string; name: string; family: string; trigger: string; fields: string[] }[];
  exit_signals: { id: string; name: string; family: string; trigger: string; fields: string[] }[];
  signal_families: { id: string; name: string; description: string; n_signals: number }[];
  semantics: { timing: string; priority: string[]; entry_session: string; r: string; trailing: string };
}

export interface ExitComponentSpec { id: string; params: Record<string, unknown>; weight?: number; signal?: AnySpec | null; label?: string }
export interface ExitPlanSpec {
  kind?: "plan"; name?: string; components: ExitComponentSpec[]; condition_mode: "or" | "and" | "k_of_n" | "weighted";
  k?: number | null; threshold?: number | null; min_bars: number; label?: string;
}
export interface ExitGrid { plan: ExitPlanSpec; component: number; grid: Record<string, unknown[]> }
export interface Sizing { mode: "risk" | "weight"; risk_per_position: number; position_weight: number; max_position_weight: number }

export interface EEConfig {
  name?: string; notes?: string;
  entries: AnySpec[]; entry_families: string[]; entry_grids: { id: string; params: Record<string, unknown>; grid: Record<string, unknown[]> }[];
  random_entries: { enabled: boolean; n: number };
  timing: { enabled: boolean; draws: number };
  calibration: { enabled: boolean; placebo_probs: number[]; oracle_drop: number };
  exits: ExitPlanSpec[]; exit_ids: string[]; exit_families: string[]; exit_grids: ExitGrid[]; protective: ExitComponentSpec[];
  baseline_exit: string | null;
  dataset: string; dataset_version?: string | null; universe?: string[] | null; universe_name?: string | null;
  start: string; end: string; frequency: "1d"; benchmarks: string[];
  sampling: { mode: "all" | "first" | "cooldown"; cooldown: number };
  eligibility: { min_price: number; min_dollar_volume: number; min_history: number };
  matching: { volatility: boolean; vol_window: number; vol_groups: number; sector: boolean; liquidity: boolean; liquidity_groups: number;
    price: boolean; price_groups: number; market_cap: boolean; market_cap_groups: number; min_pool: number };
  max_hold: number; ref_atr_n: number; ref_atr_mult: number;
  execution: ExecutionConfig; trade_notional: number;
  portfolio: { run: "all" | "top" | "none"; top: number; initial_capital: number; max_positions: number; sizing: Sizing;
    random_controls: number; baseline_buy_and_hold: boolean };
  controls: { matched: boolean; draws: number; random_dates: boolean; date_draws: number; same_vol_regime: boolean; min_date_pool: number; permutations: number };
  partition_scheme: "default" | "none" | "custom"; partitions: Partition[]; evaluate_test: boolean;
  walk_forward: { enabled: boolean; first_train_years: number; step_years: number; objective: "mean_ret" | "excess_matched" | "expectancy_r" | "ret_per_session"; min_trades: number };
  stats: { bootstrap: number; alpha: number; economic_annual_excess: number; min_trades: number };
  seed: number;
  resolved_entries?: { key: string; name: string; family: string; kind: string; spec: AnySpec }[] | null;
  resolved_exits?: { key: string; name: string; family: string; kinds: string[]; deployable: boolean; trigger: string; components: Record<string, unknown>[]; style?: string; lookahead?: boolean; shiftable?: number[] }[] | null;
  resolved_grids?: unknown[] | null; analysis_end?: string | null;
}

export interface Options {
  defaults: Omit<EEConfig, "dataset" | "start" | "end">; policy: Policy; default_partitions: Partition[];
  matrix_metrics: string[]; portfolio_metrics: string[]; objectives: string[]; boards: string[];
}

export interface ValidateResult {
  ok: boolean; n_entries: number; n_exits: number; n_cells: number; n_symbols: number; dataset_version: string;
  partitions: (Partition & { locked: boolean })[]; analysis_end: string;
  entries: { key: string; name: string; family: string; warmup: number }[];
  exits: { key: string; name: string; family: string; deployable: boolean; trigger: string; style?: string; timing?: boolean }[]; periods: string[]; notes: string[];
}

export type Status = "queued" | "running" | "done" | "failed" | "cancelled";
export interface ExperimentRow {
  experiment_id: string; name: string; kind: string; parent_id: string | null; dataset: string; dataset_version: string;
  universe_name: string | null; n_symbols: number; start: string; end: string; analysis_end: string; status: Status; error: string | null;
  created_at_utc: string; started_at_utc: string | null; finished_at_utc: string | null; n_entries: number; n_exits: number; n_cells: number;
  progress_done: number; progress_total: number; stage: string | null; evaluate_test: boolean; partitions: (Partition & { locked: boolean })[] | null;
  periods: string[]; summary: { cells: number; portfolio_runs: number; seconds: number; verdicts: Record<string, Record<string, number>>; timings?: Record<string, number> } | null;
  reproduction: { identical: number; cells: number; differing: string[]; code_changed: boolean; reproduction: string } | null;
}
export interface ExperimentDetail {
  meta: ExperimentRow; config: EEConfig;
  study: { report: Record<string, unknown>; timings: Record<string, number>; parts: (Partition & { first: number; last: number; sessions: number })[] } | null;
  entries: { key: string; name: string; family: string; kind: string; counts: { events: number; triggers: number; unmatched: number; by_partition: Record<string, number> } }[] | null;
  manifest: Record<string, unknown> | null; prior_usage: { partition: string; experiments: number; cells: number }[];
}

export interface MatrixCell { cell: string; value: number | null; n: number | null; q: number | null; verdict: Verdict | null; has_portfolio: boolean }
export interface Matrix {
  partition: string; metric: string;
  entries: { index: number; key: string; name: string; family: string; kind: string }[];
  exits: { index: number; key: string; name: string; family: string; kinds: string[]; deployable: boolean; components: number }[];
  cells: MatrixCell[][];
}

export interface Hist { edges: number[]; counts: number[] }
export interface Excess { n: number; excess: number | null; control_mean: number | null; ci: [number | null, number | null]; se: number | null; p: number | null; null: { mean: number | null; sd: number | null; q: [number | null, number | null] } | null }
export interface Group { group: string | number; n: number; mean_ret: number; excess?: number | null; n_excess?: number }
export interface CellPartition {
  partition: string; role: string; n: number; n_dates?: number; mean_ret?: number; median_ret?: number; sd_ret?: number | null; win_rate?: number;
  profit_factor?: number | null; expectancy_r?: number | null; expectancy_ref_r?: number | null; avg_win?: number | null; avg_loss?: number | null;
  avg_bars?: number; median_bars?: number; avg_win_bars?: number | null; avg_loss_bars?: number | null; ret_per_session?: number | null;
  trade_sharpe?: number | null; mae?: number; mae_r?: number | null; mfe?: number; mfe_r?: number | null; worst?: number; q05?: number | null; cvar05?: number | null;
  stop_rate?: number; target_rate?: number; condition_rate?: number; max_hold_rate?: number; period_end_rate?: number; partial_rate?: number;
  own_risk_rate?: number; stop_breach_rate?: number; reasons?: Record<string, number>; excess_bench?: number | null; bench_ret?: number | null;
  matched?: Excess | null; random_dates?: Excess | null; by_year?: Group[]; by_market_trend?: Group[]; by_market_vol?: Group[]; by_stock_vol?: Group[];
  consistency_years?: number | null; positive_years?: number | null; hist_ret?: Hist | null; hist_bars?: Hist | null; excluded?: Record<string, number>;
  timing?: { n: number; excess: number | null; real_mean?: number; shifted_mean?: number; ci: [number | null, number | null]; se: number | null;
    p: number | null; beat_share?: number; by_year?: Group[]; positive_years?: number | null } | null;
}
export interface PortfolioRun {
  cell: string; partition: string; role: string; metrics: Record<string, number | null | unknown>; reasons: Record<string, string>;
  random_entry: Record<string, { controls: number[]; mean: number | null; excess: number | null; share_below: number | null }>; n_random: number; events: number;
}
export interface TestRow { cell: string; partition: string; role: string; n: number; n_dates: number; excess: number | null; ci: [number | null, number | null]; p: number | null; q: number | null; m_tests: number; verdict: { category: Verdict; reason: string; annualized_net_excess: number | null } }
export interface PairRow { entry: number; exit: number; cell: string; base_cell: string; partition: string; n: number; diff: number | null; ci?: [number | null, number | null]; p?: number | null; q?: number | null; p_z?: number | null; q_z?: number | null; blocks?: number; win_share?: number; diff_median?: number }
/** The random-timing exit benchmark (timing.json): each exit against its own sell signals moved to random dates. */
export interface TimingRow {
  cell: string; entry: number; exit: number; partition: string; role: string; style: string; n: number; excess: number | null;
  ci: [number | null, number | null]; p: number | null; q: number | null; m_tests?: number; beat_share: number | null;
  positive_years: number | null; calibration: boolean;
}
export interface StyleGroup {
  entry: number; style: string; partition: string; role: string; exits: number; n: number; excess: number | null;
  ci: [number | null, number | null]; se: number | null; beat: number; of: number; by_year: Group[]; positive_years: number | null;
}
export interface Timing { tests: TimingRow[]; calibration: TimingRow[]; style_groups: StyleGroup[]; draws: number; method: string; note: string }

export interface CellDetail {
  cell: string; entry: { key: string; name: string; family: string; kind: string; id: string; index: number };
  exit: { key: string; name: string; family: string; index: number; components: number; kinds: string[]; trigger: string; deployable: boolean };
  counts: Record<string, unknown>; partitions: Record<string, CellPartition>; portfolio: Record<string, PortfolioRun>; tests: TestRow[]; vs_baseline: PairRow[];
  timing_tests?: TimingRow[];
}
export interface Comparisons {
  baseline: { key: string | null; index: number | null };
  hold_entry: PairRow[]; resolution_note?: string | null;
  hold_exit: Record<string, Record<string, { entry: number; cell: string; excess: number | null; q: number | null; verdict: Verdict; n: number }[]>>;
}
export interface WalkForward {
  walk_forward: { folds: { fold: number; train: string; test: string }[]; objective: string;
    groups: Record<string, { cells: string[]; folds: { fold: number; chosen: string | null; is_value?: number; oos_value?: number | null; oos_mean_all?: number | null; oos_rank?: number | null; n_candidates?: number; spearman_is_oos?: number | null }[];
      oos_mean_selected: number | null; oos_mean_all: number | null; selection_skill: number | null; spearman_mean: number | null }> };
  sensitivity: { grid: number; entry: number; component: string; params: string[]; points: { cell: string; params: Record<string, unknown>; train: number | null; validation: number | null; smoothed_train: number | null; neighbours: number }[];
    best_train: string | null; plateau: string | null; share_positive_train: number | null; share_positive_both: number | null; spearman_train_validation: number | null }[];
}
export interface BoardRow {
  experiment_id: string; cell?: string; partition?: string; role?: string; entry_name?: string; exit_name?: string; entry_family?: string; exit_family?: string;
  entry_kind?: string; exit_components?: number; n?: number; mean_ret?: number | null; win_rate?: number | null; profit_factor?: number | null; expectancy_r?: number | null;
  avg_bars?: number | null; excess_matched?: number | null; q?: number | null; verdict?: Verdict; consistency_years?: number | null; sharpe?: number | null; cagr?: number | null;
  max_drawdown?: number | null; sortino?: number | null; total_return?: number | null; key?: string; name?: string; family?: string; mean?: number; best?: number; cells?: number; supported?: number;
}

const enc = encodeURIComponent;
const active = (s?: string) => s === "queued" || s === "running";

export const useEECatalog = () => useQuery({ queryKey: ["ee-catalog"], staleTime: 300_000, queryFn: () => api.get<Catalog>("/entry-exit/catalog") });
export const useEEOptions = () => useQuery({ queryKey: ["ee-options"], staleTime: 300_000, queryFn: () => api.get<Options>("/entry-exit/options") });
export const useEEExperiments = () => useQuery({
  queryKey: ["ee-experiments"], queryFn: () => api.get<ExperimentRow[]>("/entry-exit/experiments"),
  refetchInterval: (q) => ((q.state.data ?? []).some((r) => active(r.status)) ? 2000 : false),
});
export const useEEExperiment = (id?: string) => useQuery({
  queryKey: ["ee-experiment", id], enabled: !!id, queryFn: () => api.get<ExperimentDetail>(`/entry-exit/experiments/${enc(id!)}`),
  refetchInterval: (q) => (active(q.state.data?.meta.status) ? 1500 : false),
});
export const useMatrix = (id: string | undefined, partition: string, metric: string, enabled = true) => useQuery({
  queryKey: ["ee-matrix", id, partition, metric], enabled: !!id && !!partition && enabled,
  queryFn: () => api.get<Matrix>(`/entry-exit/experiments/${enc(id!)}/matrix${qs({ partition, metric })}`),
});
export const useCell = (id?: string, cell?: string) => useQuery({
  queryKey: ["ee-cell", id, cell], enabled: !!id && !!cell, staleTime: Infinity,
  queryFn: () => api.get<CellDetail>(`/entry-exit/experiments/${enc(id!)}/cells/${enc(cell!)}`),
});
export const useCellTrades = (id?: string, cell?: string, partition?: string) => useQuery({
  queryKey: ["ee-trades", id, cell, partition], enabled: !!id && !!cell, staleTime: Infinity,
  queryFn: () => api.get<Row[]>(`/entry-exit/experiments/${enc(id!)}/cells/${enc(cell!)}/trades${qs({ partition, limit: 20000 })}`),
});
export const useCellEquity = (id?: string, cell?: string, partition?: string, enabled = true) => useQuery({
  queryKey: ["ee-equity", id, cell, partition], enabled: !!id && !!cell && !!partition && enabled, staleTime: Infinity, retry: false,
  queryFn: () => api.get<{ equity: Row[]; trades: Row[]; monthly: Row[]; buy_and_hold: Row[] }>(`/entry-exit/experiments/${enc(id!)}/cells/${enc(cell!)}/equity${qs({ partition })}`),
});
export const useComparisons = (id?: string, enabled = true) => useQuery({
  queryKey: ["ee-comparisons", id], enabled: !!id && enabled, staleTime: Infinity,
  queryFn: () => api.get<Comparisons>(`/entry-exit/experiments/${enc(id!)}/comparisons`),
});
export const useWalkForward = (id?: string, enabled = true) => useQuery({
  queryKey: ["ee-wf", id], enabled: !!id && enabled, staleTime: Infinity,
  queryFn: () => api.get<WalkForward>(`/entry-exit/experiments/${enc(id!)}/walk-forward`),
});
export const useTiming = (id?: string, enabled = true) => useQuery({
  queryKey: ["ee-timing", id], enabled: !!id && enabled, staleTime: Infinity,
  queryFn: () => api.get<Partial<Timing>>(`/entry-exit/experiments/${enc(id!)}/timing`),
});
export const useLeaderboard = (f: { board: string; objective: string; role: string; experiment_id?: string; min_trades?: number; max_drawdown?: number | null }) => useQuery({
  queryKey: ["ee-board", f], queryFn: () => api.get<BoardRow[]>(`/entry-exit/leaderboards${qs(f)}`),
});
export const useEEConfigs = () => useQuery({
  queryKey: ["ee-configs"], queryFn: () => api.get<{ config_id: string; name: string; created_at_utc: string; config: EEConfig }[]>("/entry-exit/configs"),
});
export const useEEImports = () => useQuery({ queryKey: ["ee-imports"], queryFn: () => api.get<{ strategy_id: string; version: string; experiment_id: string; cell: string; created_at_utc: string }[]>("/entry-exit/imports") });
/** One row per (experiment, partition) from the append-only ee_data_usage ledger. */
export interface EEUsageRow { experiment_id: string; dataset: string; dataset_version: string | null; partition: string; role: string; start: string | null; end: string | null; n_cells: number; at_utc: string }
export const useEEUsage = () => useQuery({ queryKey: ["ee-usage"], queryFn: () => api.get<EEUsageRow[]>("/entry-exit/usage") });

export const useEEValidate = () => useMutation({ mutationFn: (cfg: EEConfig) => api.post<ValidateResult>("/entry-exit/validate", cfg) });
export function useEELaunch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cfg: EEConfig) => api.post<ExperimentRow>("/entry-exit/experiments", cfg),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ee-experiments"] }),
  });
}
export function useEECancel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/entry-exit/experiments/${enc(id)}/cancel`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ee-experiments"] }); qc.invalidateQueries({ queryKey: ["ee-experiment"] }); },
  });
}
export function useEEReproduce() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<{ reproduction: string; identical: number; cells: number }>(`/entry-exit/experiments/${enc(id)}/reproduce`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ee-experiments"] }),
  });
}
export function useEESaveConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { name: string; config: EEConfig }) => api.post<{ config_id: string }>("/entry-exit/configs", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ee-configs"] }),
  });
}
export function useEEDeleteConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/entry-exit/configs/${enc(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ee-configs"] }),
  });
}
export function useEEImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { experiment_id: string; cell: string; name?: string }) => api.post<{ strategy_id: string; version: string; created: boolean }>("/entry-exit/import", b),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["strategies"] }); qc.invalidateQueries({ queryKey: ["ee-imports"] }); },
  });
}

// -- metric labels and units ------------------------------------------------------------------------------------------
export const METRIC_LABEL: Record<string, string> = {
  excess_matched: "Excess vs matched random entries", excess_random_dates: "Excess vs random dates", excess_bench: "Excess vs benchmark",
  mean_ret: "Mean trade return (net)", median_ret: "Median trade return", win_rate: "Win rate", profit_factor: "Profit factor",
  expectancy_r: "Expectancy (R)", avg_bars: "Average holding (sessions)", ret_per_session: "Return per session held (x252)",
  trade_sharpe: "Per-trade Sharpe (annualized by holding)", mae: "Mean adverse excursion", mfe: "Mean favourable excursion",
  stop_rate: "Stop-loss exits", target_rate: "Profit-target exits", q: "BH q-value (matched test)", consistency_years: "Years with positive excess",
  positive_years: "Years with positive mean return", n: "Trades",
  total_return: "Portfolio total return", cagr: "Portfolio CAGR", sharpe: "Portfolio Sharpe", sortino: "Portfolio Sortino",
  max_drawdown: "Portfolio max drawdown", calmar: "Portfolio Calmar", volatility: "Portfolio volatility", exposure: "Portfolio exposure",
  turnover: "Portfolio turnover", trade_count: "Portfolio trades", avg_trade_return: "Portfolio average trade",
  excess_return: "Portfolio excess vs benchmark", excess_random_portfolio: "Sharpe minus random-entry portfolios",
  timing_excess: "Excess vs random timing of the same exit", q_timing: "BH q-value (random-timing test)",
  timing_beat_share: "Trades beating their random timing",
};
export const PCT_METRICS = new Set(["excess_matched", "excess_random_dates", "excess_bench", "mean_ret", "median_ret", "win_rate", "ret_per_session", "mae", "mfe",
  "stop_rate", "target_rate", "consistency_years", "positive_years", "total_return", "cagr", "max_drawdown", "volatility", "exposure", "avg_trade_return", "excess_return",
  "timing_excess", "timing_beat_share"]);
export const SIGNED_METRICS = new Set(["excess_matched", "excess_random_dates", "excess_bench", "mean_ret", "median_ret", "expectancy_r", "ret_per_session",
  "trade_sharpe", "total_return", "cagr", "sharpe", "sortino", "calmar", "excess_return", "avg_trade_return", "excess_random_portfolio", "timing_excess"]);
export const LOWER_IS_BETTER = new Set(["q", "q_timing"]);

export const STYLE_LABEL: Record<string, string> = {
  strength: "Sell into strength", weakness: "Sell into weakness", neutral: "Neutral signal", time: "Time exit",
  protective: "Protective stop", target: "Profit target", calibration: "Calibration rule", mixed: "Mixed plan",
};
export const STYLE_INFO: Record<string, string> = {
  strength: "sells after a rise: overbought oscillators, closes above an upper band",
  weakness: "sells after a fall: trend breaks, moving-average crosses, breakdowns below a prior low or a lower band",
  neutral: "a condition that is neither (or a custom signal)", time: "fixed holding period or calendar rule",
  protective: "stop-loss levels only", target: "profit targets only", calibration: "placebo or cheating rule that checks the benchmark",
  mixed: "conditions of more than one style",
};
