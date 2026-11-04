import { AppShell } from "@/components/AppShell";
import { Empty } from "@/components/common";
import { Link, match, useLocation } from "@/lib/router";
import { AnalyticsPage } from "@/pages/Analytics";
import { BacktestPage } from "@/pages/Backtest";
import { ExperimentsPage } from "@/pages/Experiments";
import { ForwardPage } from "@/pages/Forward";
import { MarketDataPage } from "@/pages/MarketData";
import { OverviewPage } from "@/pages/Overview";
import { RunPage } from "@/pages/Run";
import { SettingsPage } from "@/pages/Settings";
import { StrategiesPage } from "@/pages/Strategies";

export function App() {
  const { path } = useLocation();
  const route = match(path);
  return (
    <AppShell>
      {route.page === "overview" && <OverviewPage />}
      {route.page === "strategies" && <StrategiesPage id={route.id} />}
      {route.page === "backtest" && <BacktestPage />}
      {route.page === "run" && <RunPage key={route.id} id={route.id} />}
      {route.page === "forward" && <ForwardPage id={route.id} />}
      {route.page === "analytics" && <AnalyticsPage />}
      {route.page === "data" && <MarketDataPage id={route.id} />}
      {route.page === "experiments" && <ExperimentsPage />}
      {route.page === "settings" && <SettingsPage />}
      {route.page === "missing" && (
        <Empty title={`No page at ${route.path}`}>
          <Link to="/" className="text-primary underline">Back to Overview</Link>
        </Empty>
      )}
    </AppShell>
  );
}
