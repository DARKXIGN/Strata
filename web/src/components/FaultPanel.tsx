import { useCallback, useEffect, useState } from "react";
import { LABEL_TEXT } from "../types";

interface Props {
  injectable: string[];
  active: string | null; // this server's own fault, from the live tick
}

interface ControlAgent {
  id: string;
  name: string;
  reachable: boolean;
  active_fault: string | null;
  mode?: string;
  self?: boolean;
  error?: string;
}

interface ControlResponse {
  self_id: string;
  agents: ControlAgent[];
}

const ALL = "all";

/** Inject or reset faults on this agent, on another agent on the same host, or on all of them. */
export default function FaultPanel({ injectable, active }: Props) {
  const [busy, setBusy] = useState(false);
  const [control, setControl] = useState<ControlResponse | null>(null);
  const [target, setTarget] = useState<string | null>(null); // null = this server's agent
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/agents/control");
      if (res.ok) setControl((await res.json()) as ControlResponse);
    } catch {
      /* the live view keeps working without the agent list */
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 3000);
    return () => window.clearInterval(id);
  }, [load]);

  const agents = control?.agents ?? [];
  const multi = agents.length > 1;
  const selfId = control?.self_id ?? null;
  const selected = target ?? selfId;
  const selectedAgent = agents.find((a) => a.id === selected);
  const activeOf = (a: ControlAgent) => (a.self ? active : a.active_fault);
  const selectedActive = selected === ALL ? null : selectedAgent ? activeOf(selectedAgent) : active;
  const anyActive = agents.some((a) => activeOf(a));

  const call = async (fault: string | null) => {
    setBusy(true);
    setMessage(null);
    const who = multi ? selected : null;
    const path = who
      ? `/api/agents/${encodeURIComponent(who)}/faults/${fault ?? "reset"}`
      : `/api/faults/${fault ?? "reset"}`;
    try {
      const res = await fetch(path, { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) setMessage(typeof body.detail === "string" ? body.detail : `Request failed (HTTP ${res.status})`);
      else if (Array.isArray(body.results)) {
        const failed = body.results.filter((r: { ok: boolean }) => !r.ok);
        if (failed.length) setMessage(failed.map((r: { error: string }) => r.error).join(" · "));
      }
    } catch {
      setMessage("Could not reach this server.");
    } finally {
      setBusy(false);
      load();
    }
  };

  const targetName = selected === ALL ? "all agents" : selectedAgent?.name ?? "this agent";
  const resetDisabled = busy || (selected === ALL ? !anyActive : !selectedActive);

  return (
    <section className="card">
      <h2 className="card-title">Fault injection</h2>
      <p className="caption">
        {multi
          ? "Pick an agent, then a fault. One agent shows an individual problem; most of them, a regional outage."
          : "Pick a fault to inject. Strata should find it within seconds."}
      </p>

      {multi && (
        <div className="agent-chips" role="radiogroup" aria-label="Agent to fault">
          {agents.map((a) => {
            const fault = activeOf(a);
            return (
              <button
                key={a.id}
                role="radio"
                aria-checked={selected === a.id}
                className={`agent-chip ${selected === a.id ? "agent-chip-on" : ""}`}
                disabled={!a.reachable}
                title={a.reachable ? undefined : a.error}
                onClick={() => setTarget(a.id)}
              >
                <span className="agent-chip-name">
                  {a.name}
                  {a.self && <span className="agent-chip-self"> (here)</span>}
                </span>
                <span className={`agent-chip-state ${fault ? "agent-chip-fault" : ""}`}>
                  {!a.reachable ? "offline" : fault ? LABEL_TEXT[fault] ?? fault : "no fault"}
                </span>
              </button>
            );
          })}
          <button
            role="radio"
            aria-checked={selected === ALL}
            className={`agent-chip ${selected === ALL ? "agent-chip-on" : ""}`}
            onClick={() => setTarget(ALL)}
          >
            <span className="agent-chip-name">All agents</span>
            <span className="agent-chip-state">same fault everywhere</span>
          </button>
        </div>
      )}

      <div className="fault-grid">
        {injectable.map((name) => (
          <button
            key={name}
            className={`fault-btn ${selectedActive === name ? "fault-btn-active" : ""}`}
            disabled={busy}
            onClick={() => call(name)}
          >
            {LABEL_TEXT[name] ?? name}
          </button>
        ))}
      </div>
      {message && <p className="caption fault-message">{message}</p>}
      <button className="reset-btn" disabled={resetDisabled} onClick={() => call(null)}>
        {multi ? `Reset ${targetName}` : "Reset network"}
      </button>
    </section>
  );
}
