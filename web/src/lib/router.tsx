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

export type Route =
  | { page: "overview" }
  | { page: "strategies"; id?: string }
  | { page: "signals"; view: SignalView; id?: string; key?: string }
  | { page: "backtest" }
  | { page: "run"; id: string }
  | { page: "forward"; id?: string }
  | { page: "analytics" }
  | { page: "data"; id?: string }
  | { page: "experiments" }
  | { page: "settings" }
  | { page: "missing"; path: string };

export type SignalView = "library" | "signal" | "new" | "rankings" | "history" | "experiment" | "result";

export function match(path: string): Route {
  const p = path.replace(/\/+$/, "") || "/";
  if (p === "/") return { page: "overview" };
  if (p === "/signals") return { page: "signals", view: "library" };
  if (p === "/signals/new") return { page: "signals", view: "new" };
  if (p === "/signals/rankings") return { page: "signals", view: "rankings" };
  if (p === "/signals/history") return { page: "signals", view: "history" };
  let sm = /^\/signals\/library\/([^/]+)$/.exec(p);
  if (sm) return { page: "signals", view: "signal", id: decodeURIComponent(sm[1]) };
  sm = /^\/signals\/experiments\/([^/]+)\/([^/]+)$/.exec(p);
  if (sm) return { page: "signals", view: "result", id: decodeURIComponent(sm[1]), key: decodeURIComponent(sm[2]) };
  sm = /^\/signals\/experiments\/([^/]+)$/.exec(p);
  if (sm) return { page: "signals", view: "experiment", id: decodeURIComponent(sm[1]) };
  let m = /^\/strategies(?:\/([^/]+))?$/.exec(p);
  if (m) return { page: "strategies", id: m[1] ? decodeURIComponent(m[1]) : undefined };
  if (p === "/backtest") return { page: "backtest" };
  m = /^\/runs\/([^/]+)$/.exec(p);
  if (m) return { page: "run", id: decodeURIComponent(m[1]) };
  m = /^\/forward(?:\/([^/]+))?$/.exec(p);
  if (m) return { page: "forward", id: m[1] ? decodeURIComponent(m[1]) : undefined };
  if (p === "/analytics") return { page: "analytics" };
  m = /^\/data(?:\/([^/]+))?$/.exec(p);
  if (m) return { page: "data", id: m[1] ? decodeURIComponent(m[1]) : undefined };
  if (p === "/experiments") return { page: "experiments" };
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
