import type { Tick } from "../types";
import { LAYER_TEXT } from "../types";

interface Props {
  layers: string[];
  tick: Tick | null;
}

export default function StrataView({ layers, tick }: Props) {
  return (
    <section className="card">
      <h2 className="card-title">Network strata</h2>
      <p className="caption">Each layer from your device down to the destination.</p>
      <ol className="strata">
        {layers.map((layer) => {
          const state = tick?.diagnosis.layers[layer] ?? "ok";
          return (
            <li key={layer} className={`stratum stratum-${state}`}>
              <span className="stratum-dot" />
              <span className="stratum-name">{LAYER_TEXT[layer] ?? layer}</span>
              <span className="stratum-state">{state === "problem" ? "Problem" : "OK"}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
