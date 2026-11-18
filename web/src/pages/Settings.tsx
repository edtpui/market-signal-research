// Settings: the effective configuration (config/settings.toml), read-only here: data policy, research periods,
// datasets, universes, the research labs, storage paths, execution defaults, the forward host and the ptrade contract.
// Edit the TOML file and restart. The colour theme is the header's toggle.
import type { ReactNode } from "react";

import { useHealth, useSettings } from "@/api/hooks";
import { ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge } from "@/components/common";

const path = (v: string | null | undefined, empty = "not set") => (v ? <span className="num text-2xs break-all">{v}</span> : <span className="text-muted-foreground">{empty}</span>);

function Reference({ r }: { r: { available: boolean; source: string | null; note: string } }) {
  return (
    <span>
      <span className={r.available ? "text-pos" : "text-muted-foreground"}>{r.available ? "available" : "not available"}</span>
      {r.source && <> · {path(r.source)}</>}
      <div className="text-2xs text-muted-foreground">{r.note}</div>
    </span>
  );
}

export function SettingsPage() {
  const s = useSettings();
  const h = useHealth();
  if (s.isLoading) return <Loading rows={8} />;
  if (s.error) return <ErrorState error={s.error} />;
  const d = s.data!;
  return (
    <div className="space-y-3">
      <PageHeader title="Settings" sub={<span className="num">{d.settings_file}</span>} />
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title="Data policy (research safety)">
          <KV rows={[
            ["History floor", <span className="num">{d.policy.history_floor} (indicator warm-up only)</span>],
            ["Backtest floor", <span className="num">{d.policy.backtest_floor}</span>],
            ["Ceiling", <span className="num">{d.policy.ceiling} (hard maximum)</span>],
            ["HOLDOUT starts", <span className="num">{d.policy.holdout_start}: never loaded in this workspace</span>],
          ]} />
          <div className="mt-3 space-y-1">
            {d.research_periods.map((p) => <div key={p.name} className="flex items-center gap-2 text-xs"><PeriodBadge period={p.name} /><span className="num">{p.start} → {p.end}</span></div>)}
          </div>
        </Panel>
        <Panel title="Forward host and ptrade">
          <KV rows={[
            ["Host", d.forward.host === "mock" ? "Mock of ptrade's basket host (SIMULATED)" : d.forward.host],
            ["Mock dataset", <span className="num">{d.forward.mock_dataset}</span>],
            ["ptrade API", d.forward.ptrade_api_url ?? "disabled (API v1 serves HOLDOUT/FWD evidence; set [forward] ptrade_api_url only in an operational deployment after approval)"],
            ["Execution authority", "the paper host: Market Signal Research exports and reads, it never writes to a paper ledger"],
            ["ptrade contract", <span className="num">mirrored from ptrade commit {d.ptrade.contract_commit} (paper/contract.py)</span>],
            ["ptrade universe file", path(d.ptrade.universe_file, "not set: deploy checks report the universe as unchecked")],
          ]} />
        </Panel>
        <Panel title="Research labs">
          <KV rows={[
            ["Signal studies: workers", <span className="num">{d.research.signal.workers} (studies run one at a time; each runs its signals on this many threads)</span>],
            ["Sector reference", <Reference r={d.research.signal.reference.sector} />],
            ["Market-cap reference", <Reference r={d.research.signal.reference.market_cap} />],
            ["Entry-exit studies: workers", <span className="num">{d.research.entry_exit.workers}</span>],
          ]} />
        </Panel>
        <Panel title="Datasets and universes">
          <div className="space-y-2">
            {d.datasets.map((x) => (
              <div key={x.id} className="text-xs">
                <div className="font-medium">{x.id} <span className="font-normal text-muted-foreground">· {x.provider}</span></div>
                <div className="text-muted-foreground">{x.name}</div>
                <div className="num text-2xs break-all text-muted-foreground">{x.sources.join("; ")}</div>
              </div>
            ))}
            <div className="text-xs"><span className="font-medium">Universes</span> <span className="num">{["all", ...d.universes].join(", ")}</span></div>
          </div>
        </Panel>
        <Panel title="Storage" className="xl:col-span-2">
          <KV rows={[
            ...Object.entries(d.paths).map(([k, v]) => [k.replace(/_/g, " "), path(v)] as [string, ReactNode]),
            ["Strategies loaded", <span className="num">{h.data?.strategies ?? "—"}{h.data?.strategy_errors.length ? ` (${h.data.strategy_errors.length} failed)` : ""}</span>],
          ]} />
        </Panel>
        <Panel title="Execution defaults" className="xl:col-span-2">
          <pre className="num text-[11px] leading-snug">{JSON.stringify(d.execution_defaults, null, 1)}</pre>
        </Panel>
      </div>
    </div>
  );
}
