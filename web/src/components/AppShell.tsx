// The frame (the ptrade terminal's header): brand, page tabs, the data policy, the paper host's clock, API status and
// the theme toggle.
import { FlaskConical, Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";

import { useHealth, useHost } from "@/api/hooks";
import { useTheme } from "@/components/theme";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Link, useLocation } from "@/lib/router";
import { cn } from "@/lib/utils";

export const NAV = [
  { to: "/", label: "Overview", match: (p: string) => p === "/" },
  { to: "/strategies", label: "Strategy Library", match: (p: string) => p.startsWith("/strategies") },
  { to: "/backtest", label: "Backtesting", match: (p: string) => p.startsWith("/backtest") || p.startsWith("/runs/") },
  { to: "/forward", label: "Forward Testing", match: (p: string) => p.startsWith("/forward") },
  { to: "/analytics", label: "Performance Analytics", match: (p: string) => p.startsWith("/analytics") },
  { to: "/data", label: "Market Data", match: (p: string) => p.startsWith("/data") },
  { to: "/experiments", label: "Experiment History", match: (p: string) => p.startsWith("/experiments") },
  { to: "/settings", label: "Settings", match: (p: string) => p.startsWith("/settings") },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { path } = useLocation();
  const health = useHealth();
  const host = useHost();
  const { theme, setTheme } = useTheme();
  const ceiling = health.data?.policy.ceiling;
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-11 max-w-[1680px] items-center gap-4 px-4">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight whitespace-nowrap">
            <FlaskConical className="size-4 text-primary" />
            Strategy<span className="font-normal text-muted-foreground">Lab</span>
          </Link>
          <nav className="flex min-w-0 items-center gap-0.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {NAV.map((n) => (
              <Link key={n.to} to={n.to} data-nav={n.label}
                className={cn("rounded-md px-2.5 py-1 text-xs font-medium whitespace-nowrap text-muted-foreground hover:bg-accent hover:text-foreground",
                  n.match(path) && "bg-accent text-foreground")}>
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs whitespace-nowrap">
            {ceiling && (
              <Badge variant="outline" className="rounded-sm border-warn/60 bg-warn/10 px-1.5 text-2xs font-semibold text-warn"
                title={`Development workspace: every bar is <= ${ceiling} (HOLDOUT starts ${health.data?.policy.holdout_start}); results are EXPLORATORY`} data-policy>
                DEV · data ≤ {ceiling}
              </Badge>
            )}
            <span className="hidden items-center gap-1.5 text-muted-foreground lg:flex" title="The paper host's clock: the last session it completed">
              Paper host
              <span className="num text-foreground">{host.data ? `${host.data.label} · ${host.data.clock ?? "not started"}` : "—"}</span>
            </span>
            <span className="flex items-center gap-1.5" data-api={health.isError ? "down" : "up"}>
              <span className={cn("size-2 rounded-full", health.isError ? "bg-neg" : health.data ? "bg-pos" : "bg-muted-foreground")} />
              <span className="text-muted-foreground">{health.isError ? "API down" : `API ${health.data?.version ?? ""}`}</span>
            </span>
            <Button variant="ghost" size="icon" className="size-7" aria-label="toggle theme" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1680px] flex-1 px-4 py-3">{children}</main>
    </div>
  );
}
