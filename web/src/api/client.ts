// Typed client for the Strategy Lab API (/api/v1). Types mirror the FastAPI responses (src/strategy_lab/api/app.py).
import type { Metrics } from "@/lib/metrics";

export class ApiError extends Error {
  status: number;
  problems?: string[];
  constructor(message: string, status: number, problems?: string[]) {
    super(message);
    this.status = status;
    this.problems = problems;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method,
      headers: body === undefined ? { Accept: "application/json" } : { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(`API unreachable: ${e instanceof Error ? e.message : String(e)}`, 0);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(`${method} ${path}: ${res.status} (not JSON)`, res.status);
  }
  if (!res.ok) {
    const d = data as { detail?: unknown; problems?: string[] } | null;
    const detail = typeof d?.detail === "string" ? d.detail : JSON.stringify(d?.detail ?? data);
    throw new ApiError(detail, res.status, d?.problems);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// -- types ------------------------------------------------------------------------------------------------------------
export interface Policy {
  history_floor: string;
  backtest_floor: string;
  ceiling: string;
  holdout_start: string;
}

export interface Health {
  ok: boolean;
  version: string;
  now_utc: string;
  policy: Policy;
  strategies: number;
  strategy_errors: { file: string; error: string }[];
  workspace: string;
  forward: { mock_dataset: string; ptrade_api_enabled: boolean };
}

export interface ParamSpec {
  default: unknown;
  kind: "int" | "float" | "bool" | "str" | "choice" | "symbols";
  min: number | null;
  max: number | null;
  choices: string[] | null;
  description: string;
}

export interface StrategyInfo {
  id: string;
  version: string;
  name: string;
  description: string;
  category: string;
  params: Record<string, ParamSpec>;
  required_fields: string[];
  order_types: string[];
  long_only: boolean;
  source_sha256: string;
  source: string;
  versions: string[];
}

export interface StrategyDetail extends StrategyInfo {
  source_file: string;
  code: string;
  warmup_default: number;
}

export interface DatasetMeta {
  dataset_id: string;
  version: string;
  name: string;
  provider: string;
  description: string;
  sources: string[];
  ingested_at_utc: string;
  n_symbols: number;
  n_rows: number;
  invalid_bars: number;
  first_date: string;
  last_date: string;
  n_sessions: number;
  n_actions: number;
  frequency: string;
  stats: Record<string, unknown>;
  policy: Policy;
}

export interface DatasetItem {
  id: string;
  name: string;
  provider: string;
  sources: string[];
  description: string;
  cached: boolean;
  current: DatasetMeta | null;
  versions: { version: string; ingested_at_utc: string; n_symbols: number; n_rows: number }[];
}

export interface DatasetDetail {
  meta: DatasetMeta;
  sessions_by_year: Record<string, number>;
  summary: { symbols: number; with_gaps: number; missing_bars: number; invalid_bars: number; late_start: number; early_end: number };
  periods: string[];
}

export interface CoverageRow {
  symbol: string;
  first_date: string | null;
  last_date: string | null;
  n_bars: number;
  expected: number;
  missing: number;
  invalid_bars: number;
  max_gap: number;
}

export interface ExecutionConfig {
  fill_timing: "next_open" | "next_close";
  slippage_bps: number;
  commission: { per_share: number; bps: number; per_order: number; minimum: number };
  spread: { model: "none" | "fixed_bps" | "hl_range"; bps: number; hl_fraction: number };
  participation_cap: number | null;
  cash_policy: "scale" | "reject";
  allow_short: boolean;
  short_borrow_bps_per_year: number;
  fractional_shares: boolean;
  max_gross_leverage: number;
  price_mode: "adjusted" | "raw";
}

export interface ExperimentConfig {
  name?: string;
  strategy: string;
  strategy_version?: string | null;
  params: Record<string, unknown>;
  dataset: string;
  dataset_version?: string | null;
  universe?: string[] | null;
  universe_name?: string | null;
  start: string;
  end: string;
  frequency: "1d" | "1w" | "1mo";
  initial_capital: number;
  benchmark: string | null;
  execution: ExecutionConfig;
  seed: number;
  notes?: string;
}

export interface Settings {
  policy: Policy;
  settings_file: string;
  datasets: { id: string; name: string; provider: string; sources: string[]; description: string }[];
  universes: string[];
  execution_defaults: ExecutionConfig;
  paths: Record<string, string>;
  forward: { host: string; mock_dataset: string; ptrade_api_url: string | null };
  research_periods: { name: string; start: string; end: string }[];
}

export type RunStatus = "queued" | "running" | "done" | "failed";

export interface RunRow {
  run_id: string;
  name: string;
  kind: "backtest" | "reproduction";
  parent_run_id: string | null;
  strategy: string;
  strategy_version: string;
  dataset: string;
  dataset_version: string;
  start: string;
  end: string;
  frequency: string;
  config_hash: string;
  status: RunStatus;
  error: string | null;
  created_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
  periods: string[];
  summary: Record<string, number | null> | null;
  reproduction: { identical: boolean; differing_artifacts: string[]; code_changed: string[] } | null;
}

export interface Manifest {
  run_id: string;
  config_hash: string;
  label: string;
  periods: string[];
  strategy: { id: string; version: string; source_sha256: string };
  engine: { source_sha256: string; commit: string | null; src_dirty: boolean | null };
  versions: Record<string, string>;
  dataset: { id: string; version: string; provider: string; sources: string[]; ingested_at_utc: string };
  artifacts: Record<string, string>;
}

export interface RunDetail {
  run: RunRow;
  config: ExperimentConfig;
  manifest: Manifest | null;
  metrics: Metrics | null;
  data_report: Record<string, unknown> | null;
  state: Record<string, unknown> | null;
  error: string | null;
}

export interface EquityRow {
  date: string;
  equity: number;
  cash: number;
  long_value: number;
  short_value: number;
  n_positions: number;
  traded_notional: number;
  commission: number;
  slippage: number;
  dividends: number;
  borrow: number;
  stale_marks: number;
  gross_exposure: number;
  net_exposure: number;
  costs: number;
  drawdown: number;
  return: number | null;
  benchmark: number | null;
}

export type Row = Record<string, string | number | boolean | null>;

export interface Compare {
  runs: { run_id: string; name: string; strategy: string; version: string; start: string; end: string; periods: string[]; dataset: string; metrics: Metrics; overlap_metrics?: Metrics }[];
  overlap: { start: string | null; end: string | null; periods: string[]; series: { run_id: string; points: [string, number][]; drawdown: [string, number][] }[] };
  correlation: Record<string, Record<string, number | null>>;
  note: string;
}

export interface DeploymentSpec {
  name: string;
  strategy: string;
  strategy_version: string;
  params: Record<string, unknown>;
  universe: string[];
  execution: ExecutionConfig;
  initial_capital: number;
  start_date: string;
  seed: number;
}

export interface Deployment {
  deployment_id: string;
  deployment_key: string;
  name: string;
  target: "mock" | "ptrade";
  book_id: string;
  model_id: string;
  strategy: string;
  strategy_version: string;
  spec: DeploymentSpec;
  requested_start: string;
  start_date: string | null;
  status: string;
  bundle_dir: string;
  created_at_utc: string;
  last_sync_utc: string | null;
  last_asof: string | null;
  error: string | null;
  source_run_id: string | null;
  host_status: string | null;
}

export interface DeploymentListItem {
  deployment: Deployment;
  equity_value: number | null;
  cash: number | null;
  current_drawdown: number | null;
  n_positions: number;
  since_start: number | null;
  sparkline: [string, number][];
  label: string;
}

export interface Snapshot {
  deployment: Deployment;
  label: string;
  capital: number;
  equity: { date: string; equity: number; drawdown: number }[];
  positions: { symbol: string; quantity: number; close: number; value: number; weight: number }[];
  orders: { symbol: string; rank: number; size: number; eligible: string; renew: boolean; status: string }[];
  fills: { symbol: string; quantity: number; price: number; commission: number; timestamp: string; kind: string }[];
  metrics: Metrics;
  monthly?: { year: number; month: number; ret: number }[];
  logs: { at_utc: string; level: string; message: string; details: string | null }[];
  cash: number | null;
  equity_value: number | null;
  current_drawdown: number | null;
}

export interface HostState {
  target: string;
  label: string;
  dataset: string;
  clock: string | null;
  first_session: string;
  last_session: string;
  books: { book_id: string; model_id: string; start_date: string; status: string; registered_at_utc: string }[];
  run_log: { id: number; asof: string; status: string; details: string; finished_at_utc: string }[];
  ptrade_api: boolean;
}

export interface Reconciliation {
  deployment_id: string;
  window: [string, string];
  compared_days: number;
  forward_days: number;
  backtest_days: number;
  max_abs_equity_diff: number | null;
  mismatched_days: number;
  forward_trades: number;
  backtest_trades: number;
  trades_identical: boolean;
  identical: boolean;
  dataset: string;
}

export interface Validation {
  ok: boolean;
  problems: string[];
  warnings: string[];
}
