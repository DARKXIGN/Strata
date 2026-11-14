import type { Tick } from "../types";
import { LABEL_TEXT, featureName, featureValue } from "../types";

/** Why this diagnosis: every class probability, the features that drove it, and the
 * normal-only anomaly check. */
export default function WhyCard({ tick }: { tick: Tick | null }) {
  if (!tick) {
    return (
      <section className="card card-wide">
        <h2 className="card-title">Why this diagnosis</h2>
        <p className="caption">Waiting for the first measurement…</p>
      </section>
    );
  }
  const { label, probs } = tick.diagnosis;
  const ranked = Object.entries(probs).sort((a, b) => b[1] - a[1]);
  const contribs = tick.diagnosis.contributions ?? [];
  const maxAbs = Math.max(0.001, ...contribs.map((c) => Math.abs(c.contribution)));
  const a = tick.anomaly;

  return (
    <section className="card card-wide why">
      <h2 className="card-title">Why this diagnosis</h2>

      <div className="why-grid">
        <div>
          <p className="caption why-sub">Probability of each cause</p>
          <div className="probs">
            {ranked.map(([name, p]) => (
              <div key={name} className={`prob-row ${name === label ? "prob-row-top" : ""}`}>
                <span className="prob-name">{LABEL_TEXT[name] ?? name}</span>
                <span className="prob-bar">
                  <span className="prob-fill" style={{ width: `${(p * 100).toFixed(1)}%` }} />
                </span>
                <span className="prob-value">{p < 0.01 && p > 0 ? "<1" : (p * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <p className="caption why-sub">
            What drove “{LABEL_TEXT[label] ?? label}” <span className="why-legend">
              <span className="contrib-key contrib-key-pos" /> toward <span className="contrib-key contrib-key-neg" /> away
            </span>
          </p>
          {contribs.length === 0 ? (
            <p className="caption">Feature contributions need the trained model.</p>
          ) : (
            <div className="contribs">
              {contribs.map((c) => {
                const w = `${((Math.abs(c.contribution) / maxAbs) * 50).toFixed(1)}%`;
                const pos = c.contribution > 0;
                return (
                  <div key={c.feature} className="contrib-row" title={`SHAP ${c.contribution > 0 ? "+" : ""}${c.contribution}`}>
                    <span className="contrib-name">
                      {featureName(c.feature)} <span className="contrib-value">{featureValue(c.feature, c.value)}</span>
                    </span>
                    <span className="contrib-track">
                      <span className="contrib-axis" />
                      <span
                        className={`contrib-bar ${pos ? "contrib-pos" : "contrib-neg"}`}
                        style={pos ? { left: "50%", width: w } : { right: "50%", width: w }}
                      />
                    </span>
                    <span className="contrib-num">
                      {pos ? "+" : "−"}
                      {Math.abs(c.contribution).toFixed(1)}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {a && <AnomalyNote anomaly={a} label={label} />}
    </section>
  );
}

function AnomalyNote({ anomaly: a, label }: { anomaly: NonNullable<Tick["anomaly"]>; label: string }) {
  if (!a.unusual) {
    return <p className="anomaly anomaly-ok">Anomaly check: measurements match usual traffic on this network.</p>;
  }
  const detail =
    a.by === "range"
      ? `${featureName(a.feature)} is ${featureValue(a.feature, a.value)}, usually ${featureValue(a.feature, a.usual)}.`
      : "Several measurements together sit outside the usual pattern.";
  if (label === "normal") {
    return (
      <p className="anomaly anomaly-warn">
        <strong>Unusual, but not a known fault.</strong> {detail} The classifier only knows the faults it was trained on,
        so this may be something new.
      </p>
    );
  }
  return <p className="anomaly anomaly-info">Anomaly check agrees this is not usual traffic. {detail}</p>;
}
