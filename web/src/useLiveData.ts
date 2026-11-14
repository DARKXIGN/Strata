import { useEffect, useRef, useState } from "react";
import type { Tick } from "./types";

const HISTORY = 90;

/** Subscribes to the server WebSocket, reconnecting on drop. Keeps a rolling history. */
export function useLiveData() {
  const [latest, setLatest] = useState<Tick | null>(null);
  const [history, setHistory] = useState<Tick[]>([]);
  const [connected, setConnected] = useState(false);
  const retry = useRef<number | undefined>(undefined);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (event) => {
        const tick = JSON.parse(event.data) as Tick;
        setLatest(tick);
        setHistory((prev) => [...prev, tick].slice(-HISTORY));
      };
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

  return { latest, history, connected };
}
