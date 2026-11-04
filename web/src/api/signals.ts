// Signal Research Lab: types mirroring /api/v1/signals (src/strategy_lab/api/signals.py) and their query hooks.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, qs, type ParamSpec, type Policy, type Row } from "@/api/client";

export type Direction = "bullish" | "bearish";
export type Verdict = "supported" | "significant_negligible" | "reversed" | "not_significant" | "insufficient_sample";

export interface SignalInfo {
  id: string;
  version: string;
  family: string;
  name: string;
  direction: Direction;
  scope: "stock" | "cross_section" | "market";
  trigger: string;
  doc: string;
  fields: string[];
  needs_benchmark: boolean;
  params: Record<string, ParamSpec>;
  warmup: number;
  has_diagnostics: boolean;
  source_sha256: string;
}

export interface FamilyInfo { id: string; name: string; description: string; n_signals: number }
export interface Catalog { families: FamilyInfo[]; signals: SignalInfo[]; n_signals: number; n_families: number }
export interface SignalDetail extends SignalInfo { code: string; versions: string[]; family_info: { name: string; description: string }; results: Row[] }

export interface RegimeFilter { market_trend: "any" | "up" | "down"; stock_vol: "any" | "low" | "mid" | "high"; vol_window: number }
export interface SignalSpec { kind: "signal"; id: string; version?: string | null; params: Record<string, unknown>; regime?: RegimeFilter | null; label?: string }
export interface ComponentSpec { id: string; version?: string | null; params: Record<string, unknown>; weight: number; negate: boolean }
export interface CombinationSpec {
  kind: "combination"; name: string; direction: Direction; mode: "and" | "or" | "k_of_n" | "weighted";
  components: ComponentSpec[]; k?: number | null; threshold?: number | null; window: number; edge: boolean;
  regime?: RegimeFilter | null; label?: string;
}
export type AnySpec = SignalSpec | CombinationSpec;

export interface Partition { name: string; role: "exploration" | "train" | "validation" | "test"; start: string; end: string; locked?: boolean }

export interface SignalExperimentConfig {
  name?: string; notes?: string;
  signals: AnySpec[]; families: string[]; grids: { id: string; params: Record<string, unknown>; grid: Record<string, unknown[]> }[];
  dataset: string; dataset_version?: string | null; universe?: string[] | null; universe_name?: string | null;
  start: string; end: string; frequency: "1d" | "1w" | "1mo"; horizons: number[]; primary_horizon?: number | null;
  entry: "next_open" | "next_close";
  sampling: { mode: "all" | "first" | "cooldown" | "non_overlapping"; cooldown: number };
  matching: { volatility: boolean; vol_window: number; vol_groups: number; sector: boolean; liquidity: boolean; liquidity_groups: number;
    price: boolean; price_groups: number; market_cap: boolean; market_cap_groups: number; min_pool: number };
  random_dates: { enabled: boolean; same_vol_regime: boolean; min_pool: number };
  permutations: number; benchmarks: string[]; sector_benchmark: boolean;
  eligibility: { min_price: number; min_dollar_volume: number; min_history: number };
  partition_scheme: "default" | "none" | "custom"; partitions: Partition[]; evaluate_test: boolean;
  costs: { round_trip_bps: number };
  stats: { bootstrap: number; alpha: number; economic_annual_excess: number; min_events: number;
    significance: "randomization_and_bootstrap" | "randomization" | "bootstrap"; p_value: "empirical" | "normal_approx" };
  seed: number; save_controls: number; workers: number;
  resolved_signals?: ResolvedSignal[] | null; analysis_end?: string | null;
}

export interface ResolvedSignal {
  key: string; kind: "signal" | "combination"; id: string; version: string; family: string; name: string; direction: Direction;
  scope: string; fields: string[]; warmup: number; needs_benchmark: boolean; params: Record<string, unknown>;
  source_sha256: string; trigger: string; spec: AnySpec;
}

export interface Options {
  defaults: Omit<SignalExperimentConfig, "dataset" | "universe" | "start" | "end" | "families">;
  reference: { sector: { available: boolean; source: string | null; note: string }; market_cap: { available: boolean; source: string | null; note: string } };
  policy: Policy;
  default_partitions: Partition[];
  verdicts: Verdict[];
}

export interface ValidateResult {
  ok: boolean; n_signals: number; n_tests: number; n_symbols: number;
  signals: { key: string; name: string; family: string; direction: Direction; scope: string; warmup: number }[];
  partitions: Partition[]; analysis_end: string; primary_horizon: number; dataset_version: string; periods: string[]; notes: string[];
}

export type ExpStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface ExperimentRow {
  experiment_id: string; name: string; kind: "study" | "reproduction"; parent_id: string | null; dataset: string; dataset_version: string;
  universe_name: string | null; n_symbols: number; start: string; end: string; analysis_end: string; frequency: string;
  config_hash: string; methodology_hash: string; status: ExpStatus; error: string | null; created_at_utc: string;
  started_at_utc: string | null; finished_at_utc: string | null; n_signals: number; progress_done: number; progress_failed: number;
  evaluate_test: boolean; partitions: Partition[] | null; periods: string[];
  summary: { signals: number; done: number; failed: number; seconds: number; verdicts: Record<string, Record<string, number>> } | null;
  reproduction: { identical: boolean; differing_signals: string[]; compared: number; code_changed: boolean; reproduction_id: string; experiment_id: string } | null;
}

export interface RunRow { experiment_id: string; signal_key: string; dir: string; status: string; error: string | null; n_events: number | null; seconds: number | null; content_sha256: string | null }

export interface ExperimentDetail {
  experiment: ExperimentRow;
  config: SignalExperimentConfig;
  study: { report: Record<string, unknown>; timings: Record<string, number>; parts: (Partition & { first: number; last: number; sessions: number })[]; reference: Options["reference"] } | null;
  manifest: Record<string, unknown> | null;
  runs: RunRow[];
  resolution_note: string | null;
  partial: boolean | null;
  prior_usage: { partition: string; role: string; start: string; end: string; experiments_so_far: number; tests_so_far: number }[];
  locked: { signal_key: string; lock_key: string; first_use: number; at_utc: string; locked_experiment: string; evaluations: number }[];
  error: string | null;
}

export interface ResultRow {
  experiment_id: string; signal_key: string; signal_id: string; version: string; family: string; name: string; direction: Direction;
  scope: string; kind: string; partition: string; role: string; horizon: number; n: number; n_dates: number; mean: number | null;
  excess: number | null; excess_net: number | null; ci_lo: number | null; ci_hi: number | null; p_matched: number | null; q_matched: number | null;
  excess_rd: number | null; p_rd: number | null; q_rd: number | null; excess_bench: number | null; win_rate: number | null;
  ann_net_excess: number | null; consistency_years: number | null; verdict: Verdict; significant: number; economic: number;
  dataset?: string; experiment_name?: string; created_at_utc?: string; q_across_rows?: number | null; n_symbols?: number;
}

export interface Hist { edges: number[]; counts: number[]; clipped?: [number, number] }
export interface Comparison {
  n: number; signal_mean: number | null; control_mean: number | null; excess: number | null; excess_net: number | null;
  ci: [number | null, number | null]; ci_conservative: [number | null, number | null]; se: number | null; p_boot: number | null;
  p?: number | null; p_two_sided?: number | null; p_z?: number | null; z?: number | null; percentile?: number | null;
  null_mean?: number | null; null_sd?: number | null; null_q?: [number | null, number | null]; permutations?: number;
  hist?: Hist; excess_hist?: Hist | null;
}
export interface HorizonStats {
  n: number; n_dates: number; n_symbols: number; mean: number | null; median?: number; std?: number | null; win_rate?: number;
  mean_net?: number; mfe?: number | null; mae?: number | null; fwd_vol?: number | null; q05?: number; q95?: number;
  mean_boot?: { mean: number | null; ci: [number | null, number | null]; se: number | null };
  matched: Comparison; random_date: Comparison | null;
  benchmarks: Record<string, { n: number; benchmark_mean: number | null; excess: number | null; ci: [number | null, number | null]; ci_conservative: [number | null, number | null]; p_boot: number | null }>;
  return_hist?: Hist | null;
}
export interface GroupRow { key: string; n: number; mean: number; excess_matched: number | null; n_matched: number; excess_benchmark: number | null }
export interface Breakdown {
  by_year: GroupRow[]; consistency_years: { groups: number; in_direction?: number; share_in_direction: number | null };
  by_vol_regime: GroupRow[]; by_market_trend: GroupRow[]; by_market_vol: GroupRow[];
  by_sector?: GroupRow[]; consistency_sectors?: { groups: number; in_direction?: number; share_in_direction: number | null };
}
export interface PartitionOut {
  horizons: Record<string, HorizonStats>;
  breakdowns: Record<string, Breakdown | null>;
  path: { k: number[]; n: number; signal: (number | null)[]; matched: (number | null)[]; [bench: string]: unknown } | null;
  n_events: number;
}
export interface SignalSummary {
  signal: ResolvedSignal; status: string;
  counts: { triggers: number; ineligible_triggers: number; events: number; by_partition: Record<string, number>;
    missing_forward: Record<string, number>; censored: Record<string, number>; unmatched: number; unmatched_no_group: number;
    unmatched_small_pool: number; random_date_unmatched: Record<string, number> | null; first_trigger: string | null };
  partitions: Record<string, PartitionOut>;
  timeline: { month: string; n: number }[];
  sampling: { mode: string; cooldown: number };
  timings: Record<string, number>;
}
export interface TestRow {
  key: string; partition: string; horizon: number; n: number; n_dates: number; direction: Direction; p_matched: number | null;
  p_matched_z: number | null; p_random_date: number | null; excess: number | null; excess_net: number | null; ci: [number | null, number | null];
  p_boot: number | null; q_matched: number | null; q_random_date: number | null; q_boot: number | null; m_tests: number;
  verdict: { category: Verdict; significant: boolean; economic: boolean; annualized_net_excess: number | null; reason: string };
}
export interface SignalResult { run: RunRow; summary: SignalSummary | null; tests: TestRow[]; error: string | null; locked: ExperimentDetail["locked"] }

export interface Inspect {
  signal: ResolvedSignal; symbol: string; dates: string[]; bars: Record<string, (number | null)[]>;
  series: { overlay: Record<string, (number | null)[]>; osc: Record<string, (number | null)[]>; levels: number[] };
  triggers: string[]; benchmark_source: string; report: Record<string, unknown>;
}

export interface Usage {
  partitions: { dataset: string; partition: string; role: string; start: string; end: string; experiments: number; tests: number }[];
  test_ledger: { signal_id: string; family: string; dataset: string; test_start: string; test_end: string; evaluations: number; locked: number; first_at: string; last_at: string }[];
  test_by_family: { family: string; evaluations: number; configurations: number }[];
}

export interface ImportRow { strategy_id: string; version: string; signal_key: string; experiment_id: string; module_file: string; created_at_utc: string; record: Record<string, unknown> }

// -- hooks -------------------------------------------------------------------------------------------------------------
const enc = encodeURIComponent;
const active = (s?: string) => s === "queued" || s === "running";

export const useCatalog = () => useQuery({ queryKey: ["sig-catalog"], staleTime: 300_000, queryFn: () => api.get<Catalog>("/signals/catalog") });
export const useSignalDef = (id?: string) => useQuery({
  queryKey: ["sig-def", id], enabled: !!id, queryFn: () => api.get<SignalDetail>(`/signals/catalog/${enc(id!)}`),
});
export const useOptions = () => useQuery({ queryKey: ["sig-options"], staleTime: 300_000, queryFn: () => api.get<Options>("/signals/options") });
export const useSignalExperiments = () => useQuery({
  queryKey: ["sig-experiments"], queryFn: () => api.get<ExperimentRow[]>("/signals/experiments"),
  refetchInterval: (q) => ((q.state.data ?? []).some((r) => active(r.status)) ? 2000 : false),
});
export const useSignalExperiment = (id?: string) => useQuery({
  queryKey: ["sig-experiment", id], enabled: !!id,
  queryFn: () => api.get<ExperimentDetail>(`/signals/experiments/${enc(id!)}`),
  refetchInterval: (q) => (active(q.state.data?.experiment.status) ? 1500 : false),
});
export const useExperimentResults = (id?: string, partition?: string, horizon?: number, poll = false) => useQuery({
  queryKey: ["sig-results", id, partition, horizon, poll], enabled: !!id,
  queryFn: () => api.get<ResultRow[]>(`/signals/experiments/${enc(id!)}/results${qs({ partition, horizon })}`),
  refetchInterval: poll ? 2000 : false,
});
export const useSignalResult = (id?: string, key?: string) => useQuery({
  queryKey: ["sig-result", id, key], enabled: !!id && !!key, staleTime: Infinity,
  queryFn: () => api.get<SignalResult>(`/signals/experiments/${enc(id!)}/signals/${enc(key!)}`),
});
export const useSignalEvents = (id?: string, key?: string, enabled = true) => useQuery({
  queryKey: ["sig-events", id, key], enabled: !!id && !!key && enabled, staleTime: Infinity,
  queryFn: () => api.get<{ rows: Row[]; columns: string[] }>(`/signals/experiments/${enc(id!)}/signals/${enc(key!)}/events${qs({ limit: 20000 })}`),
});
export const useRankings = (f: { partition?: string; horizon?: number; family?: string; direction?: string; verdict?: string }) => useQuery({
  queryKey: ["sig-rankings", f],
  queryFn: () => api.get<{ rows: ResultRow[]; n: number; note: string }>(`/signals/rankings${qs(f)}`),
});
export const useUsage = () => useQuery({ queryKey: ["sig-usage"], queryFn: () => api.get<Usage>("/signals/usage") });
export const useSignalConfigs = () => useQuery({
  queryKey: ["sig-configs"], queryFn: () => api.get<{ config_id: string; name: string; created_at_utc: string; config: SignalExperimentConfig }[]>("/signals/configs"),
});
export const useImports = () => useQuery({ queryKey: ["sig-imports"], queryFn: () => api.get<ImportRow[]>("/signals/imports") });
export const useInspect = (body: { spec: AnySpec; dataset: string; symbol: string; start?: string; end?: string; universe_name?: string | null; universe?: string[] | null } | null) => useQuery({
  queryKey: ["sig-inspect", body], enabled: !!body && !!body.symbol && !!body.dataset, staleTime: 300_000,
  queryFn: () => api.post<Inspect>("/signals/inspect", body),
});

export const useValidateSignals = () => useMutation({ mutationFn: (cfg: SignalExperimentConfig) => api.post<ValidateResult>("/signals/experiments/validate", cfg) });
export function useLaunchSignals() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cfg: SignalExperimentConfig) => api.post<{ experiment_id: string }>("/signals/experiments", cfg),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sig-experiments"] }),
  });
}
export function useCancelSignals() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/signals/experiments/${enc(id)}/cancel`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sig-experiments"] }); qc.invalidateQueries({ queryKey: ["sig-experiment"] }); },
  });
}
export function useReproduceSignals() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/signals/experiments/${enc(id)}/reproduce`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sig-experiments"] }),
  });
}
export function useSaveSignalConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { name: string; config: SignalExperimentConfig }) => api.post<{ config_id: string }>("/signals/configs", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sig-configs"] }),
  });
}
export function useDeleteSignalConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/signals/configs/${enc(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sig-configs"] }),
  });
}
export function useImportSignal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { experiment_id: string; signal_key: string; name?: string; defaults?: Record<string, unknown> }) =>
      api.post<{ strategy_id: string; version: string; created: boolean }>("/signals/import", b),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["strategies"] }); qc.invalidateQueries({ queryKey: ["sig-imports"] }); },
  });
}

// -- shared labels -------------------------------------------------------------------------------------------------------
export const VERDICT_LABEL: Record<Verdict, string> = {
  supported: "supported", significant_negligible: "significant, negligible", reversed: "reversed",
  not_significant: "not significant", insufficient_sample: "insufficient sample",
};
export const VERDICT_TONE: Record<Verdict, string> = {
  supported: "border-pos/40 bg-pos/10 text-pos", significant_negligible: "border-warn/40 bg-warn/10 text-warn",
  reversed: "border-neg/40 bg-neg/10 text-neg", not_significant: "border-border text-muted-foreground",
  insufficient_sample: "border-dashed border-border text-muted-foreground",
};
