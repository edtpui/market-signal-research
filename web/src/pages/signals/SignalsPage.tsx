// Signal studies: the builder, one study's random benchmarking, and one signal's results. The signal library lives
// under Experiments > Signals, rankings under Results, and the study list under Experiments.
import type { SignalView } from "@/lib/router";
import { ExperimentBuilder } from "@/pages/signals/ExperimentBuilder";
import { ExperimentResults } from "@/pages/signals/ExperimentResults";
import { SignalResultPage } from "@/pages/signals/SignalResult";

export function SignalsPage({ route }: { route: { view: SignalView; id?: string; key?: string } }) {
  switch (route.view) {
    case "experiment":
      return <ExperimentResults key={route.id} id={route.id!} />;
    case "result":
      return <SignalResultPage key={`${route.id}/${route.key}`} id={route.id!} signalKey={route.key!} />;
    default:
      return <ExperimentBuilder />;
  }
}
