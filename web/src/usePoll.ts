import { useEffect, useState } from "react";

/** GET a server API every `ms`. Keeps the last good value; errors are reported, not thrown. */
export function usePoll<T>(url: string | null, ms = 5000): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!url) return;
    let stopped = false;
    const load = async () => {
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(res.status === 503 ? "History is not available on this server" : `HTTP ${res.status}`);
        const json = (await res.json()) as T;
        if (!stopped) {
          setData(json);
          setError(null);
        }
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    load();
    const id = window.setInterval(load, ms);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [url, ms]);

  return { data, error };
}
