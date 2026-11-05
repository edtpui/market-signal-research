// Settings: the effective configuration (config/settings.toml), read-only here: data policy, research periods,
// datasets, universes, storage paths, execution defaults and the forward host. Edit the TOML file and restart.
import { useHealth, useSettings } from "@/api/hooks";
import { useTheme } from "@/components/theme";
import { ErrorState, KV, Loading, PageHeader, Panel, PeriodBadge } from "@/components/common";
import { Switch } from "@/components/ui/primitives";

export function SettingsPage() {
  const s = useSettings();
  const h = useHealth();
  const { theme, setTheme } = useTheme();
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
        <Panel title="Forward host">
          <KV rows={[
            ["Host", d.forward.host === "mock" ? "Mock of ptrade's basket host (SIMULATED)" : d.forward.host],
            ["Mock dataset", <span className="num">{d.forward.mock_dataset}</span>],
            ["ptrade API", d.forward.ptrade_api_url ?? "disabled (API v1 serves HOLDOUT/FWD evidence; set [forward] ptrade_api_url only in an operational deployment after approval)"],
            ["Execution authority", "the paper host: Market Signal Research exports and reads, it never writes to a paper ledger"],
          ]} />
        </Panel>
        <Panel title="Datasets">
          <div className="space-y-2">
            {d.datasets.map((x) => (
              <div key={x.id} className="text-xs">
                <div className="font-medium">{x.id} <span className="font-normal text-muted-foreground">· {x.provider}</span></div>
                <div className="text-muted-foreground">{x.name}</div>
                <div className="num text-2xs break-all text-muted-foreground">{x.sources.join("; ")}</div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Storage, universes, interface">
          <KV rows={[
            ...Object.entries(d.paths).map(([k, v]) => [k, <span className="num text-2xs break-all">{v}</span>] as [string, React.ReactNode]),
            ["Universes", <span className="num">{["all", ...d.universes].join(", ")}</span>],
            ["Strategies loaded", <span className="num">{h.data?.strategies ?? "—"}{h.data?.strategy_errors.length ? ` (${h.data.strategy_errors.length} failed)` : ""}</span>],
            ["Theme", <span className="flex items-center gap-2"><Switch checked={theme === "dark"} onCheckedChange={(c) => setTheme(c ? "dark" : "light")} aria-label="dark theme" />{theme}</span>],
          ]} />
        </Panel>
        <Panel title="Execution defaults" className="xl:col-span-2">
          <pre className="num text-[11px] leading-snug">{JSON.stringify(d.execution_defaults, null, 1)}</pre>
        </Panel>
      </div>
    </div>
  );
}
