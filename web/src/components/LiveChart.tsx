import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipProps } from "recharts";
import type { FaultEvent, Tick } from "../types";
import { LABEL_TEXT } from "../types";
import { HISTORY_SECONDS } from "../useLiveData";

const C = {
  latency: "#2563EB",
  jitter: "#8B95A1",
  grid: "#E5E8EB",
  axis: "#8B95A1",
  marker: "#4E5968",
};

interface Props {
  history: Tick[];
  events: FaultEvent[];
}

interface Point {
  t: number;
  latency: number | null;
  jitter: number | null;
  loss: number | null;
}

const X_DOMAIN: [number, number] = [-HISTORY_SECONDS, 0];
const X_TICKS = [-90, -60, -30, 0];
const xLabel = (v: number) => (v === 0 ? "now" : `${v}s`);

export default function LiveChart({ history, events }: Props) {
  const now = history[history.length - 1]?.ts ?? 0;
  const data: Point[] = history.map((t) => ({
    t: Math.round((t.ts - now) * 10) / 10,
    latency: t.features.ext_rtt_ms,
    jitter: t.features.ext_jitter_ms,
    loss: t.features.ext_loss_pct,
  }));
  const last = history[history.length - 1];
  const lastInject = [...events].reverse().find((e) => e.kind === "inject");
  const lastDetect = [...events].reverse().find((e) => e.kind === "detect" && lastInject && e.ts >= lastInject.ts);
  const detectSeconds = lastInject && lastDetect ? lastDetect.ts - lastInject.ts : null;

  // Marker lines carry no text labels: inject and detect are often < 1 s apart and the
  // labels would collide. The legend names them and the stats row gives the numbers.
  const markers = events.map((e) => ({ ...e, x: Math.round((e.ts - now) * 10) / 10 }));
  const faultNote = lastInject
    ? `${LABEL_TEXT[lastInject.label ?? ""] ?? lastInject.label} injected ${Math.max(0, Math.round(now - lastInject.ts))}s ago`
    : "No fault injected in the last 90 s";

  const renderMarkers = () =>
    markers.map((m) => (
      <ReferenceLine
        key={`${m.kind}-${m.ts}`}
        x={m.x}
        stroke={m.kind === "detect" ? C.latency : C.marker}
        strokeWidth={m.kind === "detect" ? 2 : 1.5}
        strokeDasharray={m.kind === "detect" ? undefined : "4 4"}
        ifOverflow="hidden"
      />
    ));

  return (
    <section className="card">
      <h2 className="card-title">Live latency</h2>
      <div className="stats">
        <Stat name="Latency" value={last?.features.ext_rtt_ms} unit="ms" />
        <Stat name="Jitter" value={last?.features.ext_jitter_ms} unit="ms" />
        <Stat name="Loss" value={last?.features.ext_loss_pct} unit="%" />
        <Stat
          name="Time to detect"
          value={detectSeconds}
          unit="s"
          digits={1}
          empty={lastInject && !lastDetect && last?.fault.active ? "…" : "—"}
        />
      </div>
      <p className="caption fault-note">{faultNote}</p>

      <div className="legend" aria-label="Legend">
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: C.latency }} /> Latency
        </span>
        <span className="legend-item">
          <span className="legend-swatch legend-dashed" style={{ borderColor: C.jitter }} /> Jitter
        </span>
        <span className="legend-item">
          <span className="legend-rule legend-rule-dashed" /> Fault injected / reset
        </span>
        <span className="legend-item">
          <span className="legend-rule" style={{ background: C.latency }} /> Detected
        </span>
      </div>

      <p className="caption chart-subtitle">Latency and jitter, ms</p>
      <div className="chart chart-main" role="img" aria-label="Latency and jitter in milliseconds over the last 90 seconds">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} syncId="live" margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={C.grid} />
            <XAxis dataKey="t" type="number" domain={X_DOMAIN} ticks={X_TICKS} tickFormatter={xLabel} hide />
            <YAxis
              tick={{ fontSize: 13, fill: C.axis }}
              axisLine={false}
              tickLine={false}
              width={40}
              allowDecimals={false}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: C.axis, strokeWidth: 1 }} isAnimationActive={false} />
            {renderMarkers()}
            <Line
              dataKey="latency"
              name="Latency"
              stroke={C.latency}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff" }}
              isAnimationActive={false}
              connectNulls={false}
            />
            <Line
              dataKey="jitter"
              name="Jitter"
              stroke={C.jitter}
              strokeWidth={2}
              strokeDasharray="5 4"
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff" }}
              isAnimationActive={false}
              connectNulls={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <p className="caption chart-subtitle">Packet loss, %</p>
      <div className="chart chart-loss" role="img" aria-label="Packet loss in percent over the last 90 seconds">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} syncId="live" margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={C.grid} />
            <XAxis
              dataKey="t"
              type="number"
              domain={X_DOMAIN}
              ticks={X_TICKS}
              tickFormatter={xLabel}
              tick={{ fontSize: 13, fill: C.axis }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[0, (max: number) => Math.max(10, Math.ceil(max / 10) * 10)]}
              tick={{ fontSize: 13, fill: C.axis }}
              axisLine={false}
              tickLine={false}
              width={40}
              tickCount={3}
            />
            <Tooltip content={() => null} cursor={{ stroke: C.axis, strokeWidth: 1 }} isAnimationActive={false} />
            {renderMarkers()}
            <Area
              dataKey="loss"
              name="Loss"
              stroke={C.latency}
              strokeWidth={2}
              fill={C.latency}
              fillOpacity={0.12}
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function ChartTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload as Point;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-time">{p.t === 0 ? "now" : `${Math.abs(p.t).toFixed(0)}s ago`}</div>
      <Row name="Latency" value={p.latency} unit="ms" swatch={<span className="legend-swatch" style={{ background: C.latency }} />} />
      <Row name="Jitter" value={p.jitter} unit="ms" swatch={<span className="legend-swatch legend-dashed" style={{ borderColor: C.jitter }} />} />
      <Row name="Loss" value={p.loss} unit="%" swatch={<span className="legend-swatch" style={{ background: C.latency, opacity: 0.35 }} />} />
    </div>
  );
}

function Row({ name, value, unit, swatch }: { name: string; value: number | null; unit: string; swatch: JSX.Element }) {
  return (
    <div className="chart-tooltip-row">
      {swatch}
      <span className="chart-tooltip-name">{name}</span>
      <span className="chart-tooltip-value">{value == null ? "—" : `${value.toFixed(value < 10 ? 1 : 0)} ${unit}`}</span>
    </div>
  );
}

function Stat({
  name,
  value,
  unit,
  digits = 0,
  empty = "—",
}: {
  name: string;
  value: number | null | undefined;
  unit: string;
  digits?: number;
  empty?: string;
}) {
  return (
    <div className="stat">
      <span className="caption">{name}</span>
      <span className="stat-value">
        {value == null ? empty : value.toFixed(digits)}
        {value != null && <span className="stat-unit">{unit}</span>}
      </span>
    </div>
  );
}
