// The navigation: six sections in the header, and for the two sections with several pages, a second row of tabs.
// Which section and tab are active comes from the matched route, so aliases of old paths light up the right place.
import type { Route } from "@/lib/router";

export type Section = "overview" | "experiments" | "results" | "forward" | "data" | "settings";

export const SECTIONS: { section: Section; to: string; label: string }[] = [
  { section: "overview", to: "/", label: "Overview" },
  { section: "experiments", to: "/experiments", label: "Experiments" },
  { section: "results", to: "/results/compare", label: "Results" },
  { section: "forward", to: "/forward", label: "Forward testing" },
  { section: "data", to: "/data", label: "Market data" },
  { section: "settings", to: "/settings", label: "Settings" },
];

export function sectionOf(r: Route): Section | null {
  switch (r.page) {
    case "overview":
    case "forward":
    case "data":
    case "settings":
      return r.page;
    case "experiments":
    case "backtest":
    case "library":
      return "experiments";
    case "signals":
    case "entryexit":
      return r.view === "new" ? "experiments" : "results";
    case "results":
    case "run":
      return "results";
    default:
      return null;
  }
}

export interface Tab {
  to: string;
  label: string;
  active: (r: Route) => boolean;
}

const APP = "Market Signal Research";

/** The browser tab title: the page (active tab, else section), the record id on detail pages, then the app name. */
export function pageTitle(r: Route): string {
  if (r.page === "missing") return `Not found · ${APP}`;
  const section = sectionOf(r);
  const tab = section ? SECTION_TABS[section]?.flat().find((t) => t.active(r)) : undefined;
  let label = tab?.label ?? SECTIONS.find((s) => s.section === section)?.label ?? APP;
  if (r.page === "run") label = `Run ${r.id}`;
  else if (r.page === "signals" && r.view !== "new") label = r.key ? `${r.key} · ${r.id}` : `Signal study ${r.id}`;
  else if (r.page === "entryexit" && r.view !== "new") label = r.cell ? `Cell ${r.cell} · ${r.id}` : `Entry-exit study ${r.id}`;
  else if ((r.page === "library" || r.page === "forward" || r.page === "data") && r.id) label = `${r.id} · ${label}`;
  return label === APP ? APP : `${label} · ${APP}`;
}

// Tabs in groups; the shell draws a divider between groups.
export const SECTION_TABS: Partial<Record<Section, Tab[][]>> = {
  experiments: [
    [{ to: "/experiments", label: "All experiments", active: (r) => r.page === "experiments" }],
    [
      { to: "/backtest", label: "New backtest", active: (r) => r.page === "backtest" },
      { to: "/signals/new", label: "New signal study", active: (r) => r.page === "signals" && r.view === "new" },
      { to: "/entry-exit/new", label: "New entry-exit study", active: (r) => r.page === "entryexit" && r.view === "new" },
    ],
    [
      { to: "/library/strategies", label: "Strategies", active: (r) => r.page === "library" && r.tab === "strategies" },
      { to: "/library/signals", label: "Signals", active: (r) => r.page === "library" && r.tab === "signals" },
      { to: "/library/exits", label: "Exits", active: (r) => r.page === "library" && r.tab === "exits" },
    ],
  ],
  results: [
    [
      { to: "/results/compare", label: "Compare runs", active: (r) => r.page === "results" && r.view === "compare" },
      { to: "/results/signal-rankings", label: "Signal rankings", active: (r) => r.page === "results" && r.view === "signal-rankings" },
      { to: "/results/leaderboards", label: "Entry-exit leaderboards", active: (r) => r.page === "results" && r.view === "leaderboards" },
      { to: "/results/ledgers", label: "Research ledgers", active: (r) => r.page === "results" && r.view === "ledgers" },
    ],
  ],
};
