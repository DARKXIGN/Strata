export type LayerStatus = "ok" | "problem";

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
