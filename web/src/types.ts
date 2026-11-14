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
  };
  explanation: { mode: string; summary: string; actions: string[] };
  fault: { active: string | null; injected_at: number | null; detected_at: number | null };
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
