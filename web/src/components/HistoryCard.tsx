import type { HistoryResponse, StorageStatus } from "../types";
import { LABEL_TEXT } from "../types";
import { usePoll } from "../usePoll";

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
}

function sourceNote(h: HistoryResponse | null, s: StorageStatus | null): string {
  if (h?.note) return h.note;
  if (!s) return "";
  if (!s.upload_enabled) return `Kept on this server only (upload off: ${s.reason}).`;
  const pending = s.pending ? ` · ${s.pending} waiting to upload` : "";
  const err = s.last_error ? ` · last upload error: ${s.last_error}` : "";
  return `${h?.source === "supabase" ? "From Supabase" : "Local"}${pending}${err}`;
}

/** Recent diagnoses of one agent, newest first. Data comes from the Strata server, never Supabase directly. */
export default function HistoryCard({ agentId, onAgentChange }: { agentId: string | null; onAgentChange: (id: string | null) => void }) {
  const url = `/api/history?limit=30${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ""}`;
  const { data, error } = usePoll<HistoryResponse>(url);
  const { data: storage } = usePoll<StorageStatus>("/api/storage/status", 10000);
  const records = data?.records ?? [];
  const isSelf = !agentId || agentId === storage?.agent_id;

  return (
    <section className="card history">
      <div className="card-head">
        <h2 className="card-title">Diagnosis history</h2>
        {!isSelf && (
          <button className="link-btn" onClick={() => onAgentChange(null)}>
            Back to this agent
          </button>
        )}
      </div>
      <p className="caption">
        {data ? `${data.agent_id}${isSelf ? " (this server)" : ""} · newest first` : error ?? "Loading…"}
      </p>

      {records.length === 0 ? (
        <p className="caption empty">{error ? error : "No diagnoses recorded yet. One is stored about every 10 seconds."}</p>
      ) : (
        <ol className="history-list">
          {records.map((r) => {
            const conf = r.probs?.[r.label];
            return (
              <li key={r.id} className={`history-row ${r.abnormal ? "history-bad" : "history-ok"}`}>
                <span className="history-time">{clock(r.ts)}</span>
                <span className="history-dot" aria-hidden />
                <span className="history-label">
                  {LABEL_TEXT[r.label] ?? r.label}
                  {r.label === "normal" && r.abnormal && <span className="history-flag"> · unusual</span>}
                </span>
                <span className="history-conf">{conf != null ? `${Math.round(conf * 100)}%` : ""}</span>
              </li>
            );
          })}
        </ol>
      )}
      <p className="caption history-source">{sourceNote(data, storage)}</p>
    </section>
  );
}
