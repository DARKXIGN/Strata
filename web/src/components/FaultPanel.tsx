import { useState } from "react";
import { LABEL_TEXT } from "../types";

interface Props {
  injectable: string[];
  active: string | null;
}

export default function FaultPanel({ injectable, active }: Props) {
  const [busy, setBusy] = useState(false);

  const call = async (path: string) => {
    setBusy(true);
    try {
      await fetch(path, { method: "POST" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2 className="card-title">Fault injection</h2>
      <p className="caption">Pick a fault to inject. Strata should find it within seconds.</p>
      <div className="fault-grid">
        {injectable.map((name) => (
          <button
            key={name}
            className={`fault-btn ${active === name ? "fault-btn-active" : ""}`}
            disabled={busy}
            onClick={() => call(`/api/faults/${name}`)}
          >
            {LABEL_TEXT[name] ?? name}
          </button>
        ))}
      </div>
      <button className="reset-btn" disabled={busy || !active} onClick={() => call("/api/faults/reset")}>
        Reset network
      </button>
    </section>
  );
}
