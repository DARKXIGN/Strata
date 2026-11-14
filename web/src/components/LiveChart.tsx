import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Tick } from "../types";

export default function LiveChart({ history }: { history: Tick[] }) {
  const t0 = history[0]?.ts ?? 0;
  const data = history.map((t) => ({
    t: Math.round(t.ts - t0),
    latency: t.features.ext_rtt_ms,
    jitter: t.features.ext_jitter_ms,
    loss: t.features.ext_loss_pct,
  }));
  const last = history[history.length - 1]?.features;

  return (
    <section className="card">
      <h2 className="card-title">Live latency</h2>
      <div className="stats">
        <Stat name="Latency" value={last?.ext_rtt_ms} unit="ms" />
        <Stat name="Jitter" value={last?.ext_jitter_ms} unit="ms" />
        <Stat name="Loss" value={last?.ext_loss_pct} unit="%" />
      </div>
      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
            <XAxis dataKey="t" tick={{ fontSize: 13 }} unit="s" />
            <YAxis tick={{ fontSize: 13 }} />
            <Tooltip />
            <Line dataKey="latency" stroke="#2563EB" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="jitter" stroke="#93B4F5" strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function Stat({ name, value, unit }: { name: string; value: number | null | undefined; unit: string }) {
  return (
    <div className="stat">
      <span className="caption">{name}</span>
      <span className="stat-value">
        {value == null ? "—" : value.toFixed(0)}
        <span className="stat-unit">{unit}</span>
      </span>
    </div>
  );
}
