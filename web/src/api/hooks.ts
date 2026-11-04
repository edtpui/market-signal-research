// TanStack Query hooks: one per API read, and mutations that invalidate what they change.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api, qs, type Compare, type CoverageRow, type DatasetDetail, type DatasetItem, type DeploymentListItem,
  type DeploymentSpec, type EquityRow, type ExperimentConfig, type Health, type HostState, type Reconciliation,
  type Row, type RunDetail, type RunRow, type Settings, type Snapshot, type StrategyDetail, type StrategyInfo,
  type Validation,
} from "@/api/client";

export const useHealth = () => useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health"), refetchInterval: 30_000 });
export const useSettings = () => useQuery({ queryKey: ["settings"], queryFn: () => api.get<Settings>("/settings"), staleTime: 300_000 });

export const useStrategies = () => useQuery({
  queryKey: ["strategies"], staleTime: 60_000,
  queryFn: () => api.get<{ strategies: StrategyInfo[]; errors: { file: string; error: string }[] }>("/strategies"),
});
export const useStrategy = (id?: string, version?: string) => useQuery({
  queryKey: ["strategy", id, version], enabled: !!id,
  queryFn: () => api.get<StrategyDetail>(`/strategies/${encodeURIComponent(id!)}${qs({ version })}`),
});

export const useDatasets = () => useQuery({ queryKey: ["datasets"], queryFn: () => api.get<DatasetItem[]>("/data/datasets") });
export const useDataset = (id?: string, enabled = true) => useQuery({
  queryKey: ["dataset", id], enabled: !!id && enabled, staleTime: 120_000,
  queryFn: () => api.get<DatasetDetail>(`/data/datasets/${encodeURIComponent(id!)}`),
});
export const useCoverage = (id?: string, onlyGaps = false, enabled = true) => useQuery({
  queryKey: ["coverage", id, onlyGaps], enabled: !!id && enabled, staleTime: 120_000,
  queryFn: () => api.get<{ rows: CoverageRow[]; total: number }>(`/data/datasets/${encodeURIComponent(id!)}/coverage${qs({ only_gaps: onlyGaps })}`),
});
export const useBars = (dataset?: string, symbol?: string, priceMode = "adjusted") => useQuery({
  queryKey: ["bars", dataset, symbol, priceMode], enabled: !!dataset && !!symbol, staleTime: 300_000,
  queryFn: () => api.get<{ rows: Row[]; actions: Row[]; report: Record<string, unknown> }>(
    `/data/datasets/${encodeURIComponent(dataset!)}/bars/${encodeURIComponent(symbol!)}${qs({ price_mode: priceMode })}`),
});
export const useUniverses = (dataset?: string) => useQuery({
  queryKey: ["universes", dataset], enabled: !!dataset, staleTime: 120_000,
  queryFn: () => api.get<{ name: string; symbols?: string[]; error?: string }[]>(`/data/universes${qs({ dataset })}`),
});

export const useExperiments = (poll = false) => useQuery({
  queryKey: ["experiments"], queryFn: () => api.get<RunRow[]>("/experiments"),
  refetchInterval: (q) => (poll || (q.state.data ?? []).some((r) => r.status === "queued" || r.status === "running") ? 1500 : false),
});
export const useRun = (id?: string) => useQuery({
  queryKey: ["run", id], enabled: !!id,
  queryFn: () => api.get<RunDetail>(`/experiments/${encodeURIComponent(id!)}`),
  refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.run.status) ? 1500 : false),
});
export const useRunEquity = (id?: string, enabled = true) => useQuery({
  queryKey: ["run-equity", id], enabled: !!id && enabled, staleTime: Infinity,
  queryFn: () => api.get<EquityRow[]>(`/experiments/${encodeURIComponent(id!)}/equity`),
});
export const useRunTable = (id: string | undefined, name: string, enabled = true) => useQuery({
  queryKey: ["run-table", id, name], enabled: !!id && enabled, staleTime: Infinity,
  queryFn: () => api.get<{ rows: Row[]; total: number }>(`/experiments/${encodeURIComponent(id!)}/table/${name}`),
});
export const useRolling = (id: string | undefined, window: number, enabled = true) => useQuery({
  queryKey: ["rolling", id, window], enabled: !!id && enabled, staleTime: Infinity,
  queryFn: () => api.get<{ window: number; rows: Row[]; benchmark?: Row[] }>(`/experiments/${encodeURIComponent(id!)}/rolling${qs({ window })}`),
});
export const useRunLogs = (id: string | undefined, enabled = true) => useQuery({
  queryKey: ["run-logs", id], enabled: !!id && enabled, staleTime: Infinity,
  queryFn: () => api.get<{ timestamp: string; message: string }[]>(`/experiments/${encodeURIComponent(id!)}/logs`),
});
export const useCompare = (ids: string[]) => useQuery({
  queryKey: ["compare", ids.join(",")], enabled: ids.length > 0,
  queryFn: () => api.get<Compare>(`/compare${qs({ ids: ids.join(",") })}`),
});
export const useConfigs = () => useQuery({
  queryKey: ["configs"], queryFn: () => api.get<{ config_id: string; name: string; created_at_utc: string; config: ExperimentConfig }[]>("/configs"),
});

export const useHost = () => useQuery({ queryKey: ["host"], queryFn: () => api.get<HostState>("/forward/host"), refetchInterval: 15_000 });
export const useDeployments = () => useQuery({ queryKey: ["deployments"], queryFn: () => api.get<DeploymentListItem[]>("/forward/deployments") });
export const useSnapshot = (id?: string) => useQuery({
  queryKey: ["snapshot", id], enabled: !!id,
  queryFn: () => api.get<Snapshot>(`/forward/deployments/${encodeURIComponent(id!)}`),
});
export const useBundle = (id?: string, enabled = true) => useQuery({
  queryKey: ["bundle", id], enabled: !!id && enabled,
  queryFn: () => api.get<{ manifest: Record<string, unknown>; operator: string; books_entry: string; protocol: string }>(`/forward/deployments/${encodeURIComponent(id!)}/bundle`),
});

// -- mutations ---------------------------------------------------------------------------------------------------------
export function useLaunch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cfgs: ExperimentConfig[]) => cfgs.length === 1
      ? api.post<{ run_id: string }>("/experiments", cfgs[0]).then((r) => [r.run_id])
      : api.post<{ run_ids: string[] }>("/experiments/batch", cfgs).then((r) => r.run_ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["experiments"] }),
  });
}
export const useValidate = () => useMutation({
  mutationFn: (cfg: ExperimentConfig) => api.post<{ ok: boolean; periods: string[]; warmup: number; n_symbols: number }>("/experiments/validate", cfg),
});
export function useReproduce() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<{ status: string }>(`/experiments/${encodeURIComponent(id)}/reproduce`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["experiments"] }),
  });
}
export function useSaveConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { name: string; config: ExperimentConfig }) => api.post<{ config_id: string }>("/configs", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["configs"] }),
  });
}
export function useDeleteConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/configs/${encodeURIComponent(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["configs"] }),
  });
}
export function useIngest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/data/datasets/${encodeURIComponent(id)}/ingest`),
    onSuccess: () => qc.invalidateQueries(),
  });
}
function invalidateForward(qc: ReturnType<typeof useQueryClient>) {
  for (const k of ["host", "deployments", "snapshot"]) qc.invalidateQueries({ queryKey: [k] });
}
export function useDeploy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { spec?: DeploymentSpec; from_run_id?: string; start_date?: string; name?: string; target: "mock" | "ptrade" }) =>
      api.post<{ deployment_id: string; created: boolean }>("/forward/deployments", b),
    onSuccess: () => invalidateForward(qc),
  });
}
export const useValidateDeploy = () => useMutation({ mutationFn: (spec: DeploymentSpec) => api.post<Validation>("/forward/validate", spec) });
export function useAdvance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { to?: string; sessions?: number }) => api.post<{ clock: string | null; sessions: unknown[] }>("/forward/host/advance", b),
    onSuccess: () => invalidateForward(qc),
  });
}
export function useSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<Record<string, number>>(`/forward/deployments/${encodeURIComponent(id)}/sync`),
    onSuccess: () => invalidateForward(qc),
  });
}
export function useReconcile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<Reconciliation>(`/forward/deployments/${encodeURIComponent(id)}/reconcile`),
    onSuccess: () => invalidateForward(qc),
  });
}
export function useSetStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { id: string; status: "active" | "paused" | "retired"; reason: string }) =>
      api.post(`/forward/deployments/${encodeURIComponent(b.id)}/status`, { status: b.status, reason: b.reason }),
    onSuccess: () => invalidateForward(qc),
  });
}
