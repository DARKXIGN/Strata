import { useEffect, useRef, useState } from "react";
import type { FaultEvent, Tick } from "./types";

export const HISTORY_SECONDS = 90;

/** Subscribes to the server WebSocket, reconnecting on drop. Keeps a rolling history
 * and the fault inject / detect / reset moments that fall inside it. */
export function useLiveData() {
  const [latest, setLatest] = useState<Tick | null>(null);
  const [history, setHistory] = useState<Tick[]>([]);
  const [events, setEvents] = useState<FaultEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const retry = useRef<number | undefined>(undefined);
  const prev = useRef<Tick | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;

    const onTick = (tick: Tick) => {
      const before = prev.current?.fault;
      const now = tick.fault;
      const found: FaultEvent[] = [];
      if (now.injected_at && now.injected_at !== before?.injected_at) {
        found.push({ kind: "inject", ts: now.injected_at, label: now.active });
      }
      if (now.detected_at && now.detected_at !== before?.detected_at) {
        found.push({ kind: "detect", ts: now.detected_at, label: now.active });
      }
      if (before?.active && !now.active) {
        found.push({ kind: "reset", ts: tick.ts, label: before.active });
      }
      prev.current = tick;

      const cutoff = tick.ts - HISTORY_SECONDS;
      setLatest(tick);
      setHistory((h) => [...h, tick].filter((t) => t.ts >= cutoff));
      setEvents((e) => [...e, ...found].filter((ev) => ev.ts >= cutoff));
    };

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (event) => onTick(JSON.parse(event.data) as Tick);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry.current = window.setTimeout(connect, 1000);
      };
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry.current);
      ws?.close();
    };
  }, []);

  return { latest, history, events, connected };
}
