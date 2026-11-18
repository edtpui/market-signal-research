// The frame (the ptrade terminal's header): brand, the six sections, the data policy, API status and the theme toggle;
// below it, the active section's tabs (lib/nav.ts).
import { FlaskConical, Moon, Sun } from "lucide-react";
import { Fragment, useEffect, type ReactNode } from "react";

import { useHealth } from "@/api/hooks";
import { useTheme } from "@/components/theme";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { pageTitle, SECTIONS, SECTION_TABS, sectionOf } from "@/lib/nav";
import { Link, match, useLocation } from "@/lib/router";
import { cn } from "@/lib/utils";

export function AppShell({ children }: { children: ReactNode }) {
  const { path } = useLocation();
  const route = match(path);
  const section = sectionOf(route);
  const tabs = section ? SECTION_TABS[section] : undefined;
  const health = useHealth();
  const { theme, setTheme } = useTheme();
  const ceiling = health.data?.policy.ceiling;
  const title = pageTitle(route);
  useEffect(() => { document.title = title; }, [title]);
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-11 max-w-[1680px] items-center gap-4 px-4">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight whitespace-nowrap">
            <FlaskConical className="size-4 text-primary" />
            Market Signal<span className="font-normal text-muted-foreground">Research</span>
          </Link>
          <nav className="flex min-w-0 items-center gap-0.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {SECTIONS.map((n) => (
              <Link key={n.to} to={n.to} data-nav={n.label} aria-current={n.section === section ? "page" : undefined}
                className={cn("rounded-md px-2.5 py-1 text-xs font-medium whitespace-nowrap text-muted-foreground hover:bg-accent hover:text-foreground",
                  n.section === section && "bg-accent text-foreground")}>
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
            <span className="flex items-center gap-1.5" data-api={health.isError ? "down" : "up"}>
              <span className={cn("size-2 rounded-full", health.isError ? "bg-neg" : health.data ? "bg-pos" : "bg-muted-foreground")} />
              <span className="text-muted-foreground">{health.isError ? "API down" : `API ${health.data?.version ?? ""}`}</span>
            </span>
            <Button variant="ghost" size="icon" className="size-7" aria-label="toggle theme" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          </div>
        </div>
        {tabs && (
          <div className="border-t">
            <nav className="mx-auto flex h-9 max-w-[1680px] items-center gap-0.5 overflow-x-auto px-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" data-subnav={section}>
              {tabs.map((group, gi) => (
                <Fragment key={gi}>
                  {gi > 0 && <span className="mx-1.5 h-4 w-px shrink-0 bg-border" aria-hidden />}
                  {group.map((t) => {
                    const on = t.active(route);
                    return (
                      <Link key={t.to} to={t.to} data-tab={t.label} aria-current={on ? "page" : undefined}
                        className={cn("rounded-md px-2.5 py-1 text-xs font-medium whitespace-nowrap text-muted-foreground hover:text-foreground",
                          on && "bg-muted text-foreground")}>
                        {t.label}
                      </Link>
                    );
                  })}
                </Fragment>
              ))}
            </nav>
          </div>
        )}
      </header>
      <main className="mx-auto w-full max-w-[1680px] flex-1 px-4 py-3">{children}</main>
    </div>
  );
}
