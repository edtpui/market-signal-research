// A small history-API router (the ptrade terminal's pattern): pages are a union, no routing dependency.
import { useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from "react";

const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

if (typeof window !== "undefined") window.addEventListener("popstate", emit);

export function navigate(to: string, replace = false) {
  if (replace) window.history.replaceState(null, "", to);
  else window.history.pushState(null, "", to);
  emit();
  if (!replace) window.scrollTo(0, 0);
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useLocation(): { path: string; search: URLSearchParams } {
  const href = useSyncExternalStore(subscribe, () => window.location.pathname + window.location.search);
  const [path, q = ""] = href.split("?");
  return { path, search: new URLSearchParams(q) };
}

export type ExperimentKind = "all" | "backtest" | "signal" | "entry-exit";
export type LibraryTab = "strategies" | "signals" | "exits";
export type ResultsView = "compare" | "signal-rankings" | "leaderboards" | "ledgers";
export type SignalView = "new" | "experiment" | "result";
export type EEView = "new" | "experiment" | "cell";

export type Route =
  | { page: "overview" }
  | { page: "experiments"; kind?: ExperimentKind }
  | { page: "backtest" }
  | { page: "signals"; view: SignalView; id?: string; key?: string }
  | { page: "entryexit"; view: EEView; id?: string; cell?: string }
  | { page: "library"; tab: LibraryTab; id?: string }
  | { page: "results"; view: ResultsView }
  | { page: "run"; id: string }
  | { page: "forward"; id?: string }
  | { page: "data"; id?: string }
  | { page: "settings" }
  | { page: "missing"; path: string };

const dec = (s: string | undefined) => (s ? decodeURIComponent(s) : undefined);

// Paths that older links, bookmarks and generated records use, mapped onto the current pages.
const ALIASES: Record<string, Route> = {
  "/signals": { page: "library", tab: "signals" },
  "/entry-exit": { page: "library", tab: "exits" },
  "/signals/history": { page: "experiments", kind: "signal" },
  "/entry-exit/history": { page: "experiments", kind: "entry-exit" },
  "/analytics": { page: "results", view: "compare" },
  "/signals/rankings": { page: "results", view: "signal-rankings" },
  "/entry-exit/leaderboards": { page: "results", view: "leaderboards" },
};

const RESULTS_VIEWS: readonly ResultsView[] = ["compare", "signal-rankings", "leaderboards", "ledgers"];

export function match(path: string): Route {
  const p = path.replace(/\/+$/, "") || "/";
  if (p === "/") return { page: "overview" };
  if (ALIASES[p]) return ALIASES[p];
  if (p === "/experiments") return { page: "experiments" };
  if (p === "/backtest") return { page: "backtest" };
  if (p === "/signals/new") return { page: "signals", view: "new" };
  if (p === "/entry-exit/new") return { page: "entryexit", view: "new" };
  let m = /^\/signals\/experiments\/([^/]+)\/([^/]+)$/.exec(p);
  if (m) return { page: "signals", view: "result", id: dec(m[1]), key: dec(m[2]) };
  m = /^\/signals\/experiments\/([^/]+)$/.exec(p);
  if (m) return { page: "signals", view: "experiment", id: dec(m[1]) };
  m = /^\/entry-exit\/experiments\/([^/]+)\/cells\/([^/]+)$/.exec(p);
  if (m) return { page: "entryexit", view: "cell", id: dec(m[1]), cell: dec(m[2]) };
  m = /^\/entry-exit\/experiments\/([^/]+)$/.exec(p);
  if (m) return { page: "entryexit", view: "experiment", id: dec(m[1]) };
  if (p === "/library") return { page: "library", tab: "strategies" };
  m = /^\/library\/(strategies|signals)(?:\/([^/]+))?$/.exec(p);
  if (m) return { page: "library", tab: m[1] as LibraryTab, id: dec(m[2]) };
  if (p === "/library/exits") return { page: "library", tab: "exits" };
  m = /^\/strategies(?:\/([^/]+))?$/.exec(p);
  if (m) return { page: "library", tab: "strategies", id: dec(m[1]) };
  m = /^\/signals\/library\/([^/]+)$/.exec(p);
  if (m) return { page: "library", tab: "signals", id: dec(m[1]) };
  if (p === "/results") return { page: "results", view: "compare" };
  m = /^\/results\/([^/]+)$/.exec(p);
  if (m && (RESULTS_VIEWS as readonly string[]).includes(m[1])) return { page: "results", view: m[1] as ResultsView };
  m = /^\/runs\/([^/]+)$/.exec(p);
  if (m) return { page: "run", id: dec(m[1])! };
  m = /^\/forward(?:\/([^/]+))?$/.exec(p);
  if (m) return { page: "forward", id: dec(m[1]) };
  m = /^\/data(?:\/([^/]+))?$/.exec(p);
  if (m) return { page: "data", id: dec(m[1]) };
  if (p === "/settings") return { page: "settings" };
  return { page: "missing", path };
}

export function Link({ to, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) {
  return (
    <a
      href={to}
      onClick={(e: MouseEvent<HTMLAnchorElement>) => {
        onClick?.(e);
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        navigate(to);
      }}
      {...rest}
    />
  );
}

export function setSearch(params: Record<string, string | null>) {
  const q = new URLSearchParams(window.location.search);
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === "") q.delete(k);
    else q.set(k, v);
  }
  const s = q.toString();
  navigate(`${window.location.pathname}${s ? `?${s}` : ""}`, true);
}
