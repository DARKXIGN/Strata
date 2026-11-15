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
  const confidence = probs[label] ?? 0;
  const unknown = label === "normal" && tick.anomaly?.unusual;

  return (
    <section className="card">
      <h2 className="card-title">Diagnosis</h2>
      <div className="diagnosis-head">
        <span className="diagnosis-label">{LABEL_TEXT[label] ?? label}</span>
        <span className="diagnosis-conf">{(confidence * 100).toFixed(0)}% sure</span>
        {unknown && <span className="pill-warn">Unusual pattern</span>}
      </div>
      <p className="body">{tick.explanation.summary}</p>
      <ul className="actions">
        {tick.explanation.actions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
      <p className="caption diagnosis-foot">
        Model: {tick.diagnosis.model === "lightgbm" ? "LightGBM" : "rule-based fallback"} · Explanation:{" "}
        {explanationNote(tick.explanation)}
      </p>
    </section>
  );
}

const MODE_TEXT: Record<string, string> = { template: "template", local: "local LLM", api: "API LLM" };

function explanationNote(e: Tick["explanation"]): string {
  const requested = e.requested ?? e.mode;
  if (requested === "template") return "template (offline)";
  if (e.mode !== "template") return MODE_TEXT[e.mode] ?? e.mode;
  if (e.pending) return `template while the ${MODE_TEXT[requested]} writes…`;
  if (e.fallback) return `template (${MODE_TEXT[requested]} unavailable: ${e.fallback})`;
  return "template";
}
