// Entry-exit studies: the builder, one study (matrix and comparisons) and one cell. The exit library lives under
// Experiments > Exits, the leaderboards under Results, and the study list under Experiments.
import type { EEView } from "@/lib/router";
import { Builder } from "@/pages/entryexit/Builder";
import { CellView } from "@/pages/entryexit/CellView";
import { ExperimentView } from "@/pages/entryexit/ExperimentView";

export function EntryExitPage({ route }: { route: { view: EEView; id?: string; cell?: string } }) {
  switch (route.view) {
    case "experiment":
      return <ExperimentView key={route.id} id={route.id!} />;
    case "cell":
      return <CellView key={`${route.id}/${route.cell}`} id={route.id!} cell={route.cell!} />;
    default:
      return <Builder />;
  }
}
