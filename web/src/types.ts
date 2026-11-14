export type LayerStatus = "ok" | "problem" | "unmeasured";

export interface Tick {
  type: "tick";
  ts: number;
  features: Record<string, number | null>;
  diagnosis: {
    label: string;
    probs: Record<string, number>;
    layer: string | null;
    layers: Record<string, LayerStatus>;
    model?: string;
    contributions?: Contribution[];
  };
  explanation: {
    mode: string; // what produced this text: template | local | api
    requested?: string; // mode chosen in settings
    summary: string;
    actions: string[];
    pending?: boolean; // an LLM call is running; template shown meanwhile
    fallback?: string | null; // why the LLM text is not shown
  };
  fault: { active: string | null; injected_at: number | null; detected_at: number | null };
  anomaly?: Anomaly | null;
}

export interface Status {
  sim_mode: boolean;
  sim_reason: string;
  fault_mode: string;
  explain_mode: string;
  labels: string[];
  layers: string[];
  injectable: string[];
  active_fault: string | null;
}

export const LABEL_TEXT: Record<string, string> = {
  normal: "Normal",
  high_latency: "High latency",
  packet_loss: "Packet loss",
  bandwidth_limit: "Bandwidth limit",
  dns_slow: "Slow DNS",
  dns_failure: "DNS failure",
};

export const LAYER_TEXT: Record<string, string> = {
  device: "Device",
  wifi: "Wi-Fi",
  router: "Router",
  isp: "ISP link",
  dns: "DNS",
  server: "Destination server",
};

/** Fault lifecycle moments drawn as markers on the time series. */
export interface FaultEvent {
  kind: "inject" | "detect" | "reset";
  ts: number;
  label: string | null;
}

/** Fluent Emoji (Flat, MIT) icon per strata layer, bundled in public/icons/fluent. */
export const LAYER_ICON: Record<string, string> = {
  device: "/icons/fluent/laptop_flat.svg",
  wifi: "/icons/fluent/antenna_bars_flat.svg",
  router: "/icons/fluent/satellite_antenna_flat.svg",
  isp: "/icons/fluent/globe_with_meridians_flat.svg",
  dns: "/icons/fluent/open_book_flat.svg",
  server: "/icons/fluent/file_cabinet_flat.svg",
};

export interface ExplainSettings {
  mode: "template" | "local" | "api";
  base_url: string;
  model: string;
  api_key_set: boolean;
  api_key_masked: string;
  timeout: number;
  default_local_url?: string;
}

export interface Contribution {
  feature: string;
  value: number | null;
  contribution: number; // SHAP, raw model score: + pushes toward the diagnosis, - away
}

export interface Anomaly {
  unusual: boolean;
  by: "range" | "forest" | null;
  feature: string; // the feature furthest from its usual value
  value: number | null;
  usual: number;
  z: number;
}

/** Human names and units for model features. */
export const FEATURE_TEXT: Record<string, { name: string; unit: "ms" | "%" | "Mbps" | "flag" | "count" | "hop" }> = {
  gw_rtt_ms: { name: "Gateway round trip", unit: "ms" },
  gw_jitter_ms: { name: "Gateway jitter", unit: "ms" },
  gw_loss_pct: { name: "Gateway loss", unit: "%" },
  ext_rtt_ms: { name: "Internet round trip", unit: "ms" },
  ext_jitter_ms: { name: "Internet jitter", unit: "ms" },
  ext_loss_pct: { name: "Internet loss", unit: "%" },
  dns_primary_ms: { name: "Configured DNS time", unit: "ms" },
  dns_primary_ok: { name: "Configured DNS answered", unit: "flag" },
  dns_alt_ms: { name: "Alternate DNS time", unit: "ms" },
  dns_alt_ok: { name: "Alternate DNS answered", unit: "flag" },
  trace_hops: { name: "Hops to destination", unit: "count" },
  trace_first_hop_ms: { name: "First hop delay", unit: "ms" },
  trace_max_hop_ms: { name: "Slowest hop delay", unit: "ms" },
  trace_jump_hop: { name: "Hop where delay jumps", unit: "hop" },
  throughput_mbps: { name: "Download speed", unit: "Mbps" },
  ext_minus_gw_ms: { name: "Delay beyond the gateway", unit: "ms" },
  ext_loss_minus_gw_loss: { name: "Loss beyond the gateway", unit: "%" },
  dns_max_ms: { name: "Slowest DNS lookup", unit: "ms" },
  dns_over_rtt_ms: { name: "DNS time beyond round trip", unit: "ms" },
  dns_fail_count: { name: "DNS resolvers failing", unit: "count" },
  dns_primary_only_fail: { name: "Only configured DNS failing", unit: "flag" },
};

export function featureName(key: string): string {
  return FEATURE_TEXT[key]?.name ?? key;
}

export function featureValue(key: string, v: number | null): string {
  if (v == null) return "no answer";
  const unit = FEATURE_TEXT[key]?.unit;
  if (unit === "flag") return v >= 0.5 ? "yes" : "no";
  if (unit === "hop") return v === 0 ? "none" : `hop ${v.toFixed(0)}`;
  if (unit === "count") return v.toFixed(0);
  const digits = Math.abs(v) < 10 ? 1 : 0;
  return `${v.toFixed(digits)} ${unit}`;
}
