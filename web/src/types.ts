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

/** Tossface glyph per strata layer. */
export const LAYER_ICON: Record<string, string> = {
  device: "\u{1F4BB}", // laptop
  wifi: "\u{1F4F6}", // antenna bars
  router: "\u{1F4E1}", // satellite antenna
  isp: "\u{1F310}", // globe with meridians
  dns: "\u{1F4D6}", // open book
  server: "\u{1F5C4}\u{FE0F}", // file cabinet
};
