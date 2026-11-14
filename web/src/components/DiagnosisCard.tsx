import type { Tick } from "../types";
import { LABEL_TEXT } from "../types";

export default function DiagnosisCard({ tick }: { tick: Tick | null }) {
  if (!tick) {
    return (
      <section className="card">
        <h2 className="card-title">Diagnosis</h2>
        <p className="caption">Waiting for the first measurement…</p>
      </section>
    );
  }

  const { label, probs } = tick.diagnosis;
  const top = Object.entries(probs)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);

  return (
    <section className="card">
      <h2 className="card-title">Diagnosis</h2>
      <div className="diagnosis-label">{LABEL_TEXT[label] ?? label}</div>
      <p className="body">{tick.explanation.summary}</p>
      <ul className="actions">
        {tick.explanation.actions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
      <div className="probs">
        {top.map(([name, p]) => (
          <div key={name} className="prob-row">
            <span className="prob-name">{LABEL_TEXT[name] ?? name}</span>
            <span className="prob-bar">
              <span className="prob-fill" style={{ width: `${(p * 100).toFixed(1)}%` }} />
            </span>
            <span className="prob-value">{(p * 100).toFixed(0)}%</span>
          </div>
        ))}
      </div>
      <p className="caption">Explanation: {tick.explanation.mode}</p>
    </section>
  );
}
