// Signal Research Lab: one entry in the app's navigation, sub-pages for the library, a signal's definition, the
// experiment builder, rankings, research history, an experiment's random benchmarking, and one signal's results.
import type { SignalView } from "@/lib/router";
import { ExperimentBuilder } from "@/pages/signals/ExperimentBuilder";
import { ExperimentResults } from "@/pages/signals/ExperimentResults";
import { ResearchHistory } from "@/pages/signals/ResearchHistory";
import { Rankings } from "@/pages/signals/Rankings";
import { SignalDetailPage } from "@/pages/signals/SignalDetail";
import { SignalLibrary } from "@/pages/signals/SignalLibrary";
import { SignalResultPage } from "@/pages/signals/SignalResult";

export function SignalsPage({ route }: { route: { view: SignalView; id?: string; key?: string } }) {
  switch (route.view) {
    case "signal":
      return <SignalDetailPage key={route.id} id={route.id!} />;
    case "new":
      return <ExperimentBuilder />;
    case "rankings":
      return <Rankings />;
    case "history":
      return <ResearchHistory />;
    case "experiment":
      return <ExperimentResults key={route.id} id={route.id!} />;
    case "result":
      return <SignalResultPage key={`${route.id}/${route.key}`} id={route.id!} signalKey={route.key!} />;
    default:
      return <SignalLibrary />;
  }
}
