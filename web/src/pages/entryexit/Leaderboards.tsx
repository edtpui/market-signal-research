// Leaderboards across entry-exit experiments, from the catalog: standalone entries, exits conditional on fixed entry
// cohorts, entry-exit pairs, multi-entry combinations and combined systems, ranked by a selectable objective on one
// partition role (train = in-sample, validation = chronological out-of-sample; never mixed).
import { useState } from "react";

import { useEEOptions, useLeaderboard, type BoardRow } from "@/api/entryExit";
import { ChoiceSelect, Field, NumberInput } from "@/components/ConfigForm";
import { DataTable, type Col } from "@/components/DataTable";
import { ErrorState, Loading, PageHeader, Panel, PeriodBadge } from "@/components/common";
import * as fmt from "@/lib/format";
import { navigate } from "@/lib/router";
import { numc, pctc, qv } from "@/pages/entryexit/common";
import { VerdictBadge } from "@/pages/signals/common";

const BOARD = {
  entries: "Best standalone entry signals (mean over their exits)",
  exits: "Best exit methods conditional on fixed entry cohorts (mean over entries)",
  pairs: "Best entry-exit combinations",
  multi_entry: "Best multi-entry combinations",
  systems: "Best combined entry and exit systems (multi-entry x multi-component exits)",
};
const OBJECTIVE: Record<string, string> = {
  risk_adjusted: "Highest risk-adjusted return (portfolio Sharpe)", low_drawdown: "Lowest drawdown (subject to the drawdown cap)",
  expectancy: "Highest expectancy (R)", consistency: "Most consistent annual performance", oos_excess: "Strongest excess over random entries",
  confidence: "Best statistical confidence (lowest BH q)", cagr: "Highest portfolio CAGR", mean_ret: "Highest mean trade return",
};

export function Leaderboards() {
  const opts = useEEOptions();
  const [board, setBoard] = useState("pairs");
  const [objective, setObjective] = useState("oos_excess");
  const [role, setRole] = useState("validation");
  const [minTrades, setMinTrades] = useState(30);
  const [maxDd, setMaxDd] = useState<number | null>(null);
  const q = useLeaderboard({ board, objective, role, min_trades: minTrades, max_drawdown: maxDd });
  const agg = board === "entries" || board === "exits";
  const cols: Col<BoardRow>[] = agg ? [
    { id: "name", header: board === "entries" ? "Entry" : "Exit", value: (r) => r.name },
    { id: "family", header: "Family", value: (r) => r.family },
    { id: "mean", header: "Mean of objective", value: (r) => r.mean, cell: (r) => numc(r.mean, 4), align: "right" },
    { id: "best", header: "Best", value: (r) => r.best, cell: (r) => numc(r.best, 4), align: "right" },
    { id: "cells", header: "Cells", value: (r) => r.cells, align: "right" },
    { id: "supported", header: "Supported", value: (r) => r.supported, align: "right" },
    { id: "experiment_id", header: "Experiment", value: (r) => r.experiment_id },
  ] : [
    { id: "entry_name", header: "Entry", value: (r) => r.entry_name },
    { id: "exit_name", header: "Exit", value: (r) => r.exit_name },
    { id: "n", header: "Trades", value: (r) => r.n, align: "right" },
    { id: "excess_matched", header: "Excess vs random", value: (r) => r.excess_matched, cell: (r) => pctc(r.excess_matched), align: "right" },
    { id: "q", header: "q", value: (r) => r.q, cell: (r) => qv(r.q), align: "right" },
    { id: "verdict", header: "Verdict", value: (r) => r.verdict, cell: (r) => <VerdictBadge v={r.verdict} /> },
    { id: "mean_ret", header: "Mean trade", value: (r) => r.mean_ret, cell: (r) => pctc(r.mean_ret), align: "right" },
    { id: "expectancy_r", header: "Expectancy R", value: (r) => r.expectancy_r, cell: (r) => numc(r.expectancy_r), align: "right" },
    { id: "win_rate", header: "Win", value: (r) => r.win_rate, cell: (r) => <span className="num">{fmt.pct(r.win_rate, 0, false)}</span>, align: "right" },
    { id: "consistency_years", header: "Years +", value: (r) => r.consistency_years, cell: (r) => <span className="num">{fmt.pct(r.consistency_years, 0, false)}</span>, align: "right" },
    { id: "sharpe", header: "Sharpe", value: (r) => r.sharpe, cell: (r) => numc(r.sharpe), align: "right" },
    { id: "cagr", header: "CAGR", value: (r) => r.cagr, cell: (r) => pctc(r.cagr), align: "right" },
    { id: "max_drawdown", header: "Max DD", value: (r) => r.max_drawdown, cell: (r) => pctc(r.max_drawdown), align: "right" },
    { id: "experiment_id", header: "Experiment", value: (r) => r.experiment_id, hidden: true },
  ];
  return (
    <div className="space-y-3">
      <PageHeader title="Entry-exit leaderboards" sub={<span className="flex items-center gap-2">Ranked within one partition role; {role === "train" ? <PeriodBadge period="IS" /> : <PeriodBadge period="OOS" />} rows are never mixed. Rankings are descriptive: q-values are per experiment.</span>} />
      <Panel title={BOARD[board as keyof typeof BOARD]} bodyClass="p-0" actions={
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Board"><ChoiceSelect value={board} onChange={setBoard} className="h-7 w-56 text-xs" options={Object.entries(BOARD).map(([k, v]) => ({ value: k, label: v.split(" (")[0] }))} aria-label="board" /></Field>
          <Field label="Objective"><ChoiceSelect value={objective} onChange={setObjective} className="h-7 w-64 text-xs" options={(opts.data?.objectives ?? Object.keys(OBJECTIVE)).map((k) => ({ value: k, label: OBJECTIVE[k] ?? k }))} aria-label="objective" /></Field>
          <Field label="Partition role"><ChoiceSelect value={role} onChange={setRole} className="h-7 w-40 text-xs" options={[{ value: "validation", label: "validation (OOS)" }, { value: "train", label: "train (IS)" }, { value: "test", label: "test (if spent)" }, { value: "exploration", label: "exploration" }]} aria-label="role" /></Field>
          <Field label="Min trades"><NumberInput className="h-7 w-20" value={minTrades} min={1} onChange={(v) => setMinTrades(v ?? 30)} /></Field>
          <Field label="Max drawdown cap" hint="for 'lowest drawdown subject to return constraints': drop rows with a deeper drawdown"><NumberInput className="h-7 w-24" value={maxDd} allowEmpty onChange={setMaxDd} /></Field>
        </div>}>
        {q.isLoading ? <Loading rows={8} /> : q.error ? <ErrorState error={q.error} /> : (
          <DataTable rows={q.data ?? []} cols={cols} name={`leaderboard-${board}-${objective}-${role}`} maxHeight={640}
            onRowClick={(r) => (r.cell ? navigate(`/entry-exit/experiments/${r.experiment_id}/cells/${r.cell}`) : navigate(`/entry-exit/experiments/${r.experiment_id}`))} />
        )}
      </Panel>
    </div>
  );
}
