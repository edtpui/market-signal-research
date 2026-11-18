import { AppShell } from "@/components/AppShell";
import { Empty, PageHeader } from "@/components/common";
import { Link, match, useLocation } from "@/lib/router";
import { AnalyticsPage } from "@/pages/Analytics";
import { EntryExitPage } from "@/pages/entryexit/EntryExitPage";
import { ExitLibrary } from "@/pages/entryexit/ExitLibrary";
import { Leaderboards } from "@/pages/entryexit/Leaderboards";
import { BacktestPage } from "@/pages/Backtest";
import { ExperimentsPage } from "@/pages/Experiments";
import { ForwardPage } from "@/pages/Forward";
import { LedgersPage } from "@/pages/Ledgers";
import { MarketDataPage } from "@/pages/MarketData";
import { OverviewPage } from "@/pages/Overview";
import { RunPage } from "@/pages/Run";
import { SettingsPage } from "@/pages/Settings";
import { Rankings } from "@/pages/signals/Rankings";
import { SignalDetailPage } from "@/pages/signals/SignalDetail";
import { SignalLibrary } from "@/pages/signals/SignalLibrary";
import { SignalsPage } from "@/pages/signals/SignalsPage";
import { StrategiesPage } from "@/pages/Strategies";

export function App() {
  const { path } = useLocation();
  const route = match(path);
  return (
    <AppShell>
      {route.page === "overview" && <OverviewPage />}
      {route.page === "experiments" && <ExperimentsPage kind={route.kind} />}
      {route.page === "backtest" && <BacktestPage />}
      {route.page === "signals" && <SignalsPage route={route} />}
      {route.page === "entryexit" && <EntryExitPage route={route} />}
      {route.page === "library" && route.tab === "strategies" && <StrategiesPage id={route.id} />}
      {route.page === "library" && route.tab === "signals" && (route.id ? <SignalDetailPage key={route.id} id={route.id} /> : <SignalLibrary />)}
      {route.page === "library" && route.tab === "exits" && <ExitLibrary />}
      {route.page === "results" && route.view === "compare" && <AnalyticsPage />}
      {route.page === "results" && route.view === "signal-rankings" && <Rankings />}
      {route.page === "results" && route.view === "leaderboards" && <Leaderboards />}
      {route.page === "results" && route.view === "ledgers" && <LedgersPage />}
      {route.page === "run" && <RunPage key={route.id} id={route.id} />}
      {route.page === "forward" && <ForwardPage id={route.id} />}
      {route.page === "data" && <MarketDataPage id={route.id} />}
      {route.page === "settings" && <SettingsPage />}
      {route.page === "missing" && (
        <>
          <PageHeader title="Page not found" />
          <Empty title={`No page at ${route.path}`}>
            <Link to="/" className="text-primary underline">Back to Overview</Link>
          </Empty>
        </>
      )}
    </AppShell>
  );
}
