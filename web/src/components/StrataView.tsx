import { useEffect, useState } from "react";
import type { LayerStatus, Tick } from "../types";
import { LABEL_TEXT, LAYER_ICON, LAYER_TEXT } from "../types";

const STEP_MS = 320;

interface Props {
  layers: string[];
  tick: Tick | null;
}

type Features = Record<string, number | null>;

function num(v: number | null | undefined, digits = 0): string {
  return v == null ? "—" : v.toFixed(digits);
}

/** One line of evidence per layer, straight from the measurements. */
function evidence(layer: string, f: Features | undefined): string {
  if (!f) return "Waiting for data";
  switch (layer) {
    case "device":
      return "Measuring from this host";
    case "wifi":
      return "Not measured on a wired host";
    case "router":
      return `Gateway ${num(f.gw_rtt_ms, 1)} ms · loss ${num(f.gw_loss_pct)}%`;
    case "isp": {
      const tput = f.throughput_mbps == null ? "no download" : `${num(f.throughput_mbps, 1)} Mbps`;
      return `Internet ${num(f.ext_rtt_ms)} ms · loss ${num(f.ext_loss_pct)}% · ${tput}`;
    }
    case "dns": {
      const primary = f.dns_primary_ok === 0 ? "primary failed" : `primary ${num(f.dns_primary_ms)} ms`;
      const alt = f.dns_alt_ok === 0 ? "alternate failed" : `alternate ${num(f.dns_alt_ms)} ms`;
      return `Lookup ${primary} · ${alt}`;
    }
    case "server": {
      if (f.trace_hops == null) return "Route not traced";
      const jump = f.trace_jump_hop ? ` · delay jumps at hop ${f.trace_jump_hop}` : "";
      return `${num(f.trace_hops)} hops · slowest ${num(f.trace_max_hop_ms)} ms${jump}`;
    }
    default:
      return "";
  }
}

const STATUS_TEXT: Record<LayerStatus | "pending" | "digging", string> = {
  ok: "OK",
  problem: "Problem",
  unmeasured: "Not measured",
  pending: "",
  digging: "Checking…",
};

/** Re-runs the top-down dig whenever the diagnosis or the injected fault changes. */
function useDigDepth(key: string, count: number): number {
  const [depth, setDepth] = useState(0);
  useEffect(() => {
    setDepth(0);
    if (!count) return;
    let d = 0;
    const id = window.setInterval(() => {
      d += 1;
      setDepth(d);
      if (d >= count) window.clearInterval(id);
    }, STEP_MS);
    return () => window.clearInterval(id);
  }, [key, count]);
  return depth;
}

export default function StrataView({ layers, tick }: Props) {
  const label = tick?.diagnosis.label ?? "";
  const key = tick ? `${label}|${tick.fault.active ?? ""}` : "";
  const depth = useDigDepth(key, tick ? layers.length : 0);
  const digging = depth < layers.length;
  const problem = tick?.diagnosis.layer;

  let headline = "Waiting for the first measurement…";
  if (tick && digging) headline = "Digging through the layers…";
  else if (tick && problem) headline = `Found it: ${LAYER_TEXT[problem] ?? problem} — ${LABEL_TEXT[label] ?? label}`;
  else if (tick) headline = "All measured layers look healthy";

  return (
    <section className="card">
      <h2 className="card-title">Network strata</h2>
      <p className="caption">{headline}</p>
      <ol className="strata" style={{ ["--depth" as string]: depth, ["--count" as string]: Math.max(layers.length, 1) }}>
        {layers.map((layer, i) => {
          const real: LayerStatus = tick?.diagnosis.layers[layer] ?? "ok";
          const state = !tick || i > depth ? "pending" : i === depth ? "digging" : real;
          return (
            <li key={layer} className={`stratum stratum-${state}`}>
              <span className="stratum-icon" aria-hidden>
                <img src={LAYER_ICON[layer]} alt="" width={24} height={24} />
              </span>
              <span className="stratum-text">
                <span className="stratum-name">{LAYER_TEXT[layer] ?? layer}</span>
                <span className="stratum-evidence">{state === "pending" ? " " : evidence(layer, tick?.features)}</span>
              </span>
              <span className="stratum-state">{STATUS_TEXT[state]}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
