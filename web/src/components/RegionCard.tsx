import type { AgentSummary, RegionSummary, RegionsResponse, Verdict } from "../types";
import { LABEL_TEXT } from "../types";
import { usePoll } from "../usePoll";

const VERDICT_TEXT: Record<Verdict, string> = {
  regional_outage: "Regional outage",
  individual_issue: "Individual problem",
  normal: "All normal",
  no_data: "No recent data",
};

function issueText(a: AgentSummary): string {
  if (a.status === "silent") return "no report in the last 30 s";
  if (a.status === "normal") return "normal";
  return a.main_issue ? LABEL_TEXT[a.main_issue] ?? a.main_issue : "abnormal";
}

/** The answer to "is it just me?" from the point of view of this server's agent. */
function headline(regions: RegionSummary[], self: string): string {
  const mine = regions.find((r) => r.agents.some((a) => a.id === self));
  const me = mine?.agents.find((a) => a.id === self);
  if (!mine || !me) return "This agent has not reported yet.";
  const others = mine.agents.filter((a) => a.id !== self && a.status === "abnormal").map((a) => a.name);
  if (mine.verdict === "regional_outage")
    return me.status === "abnormal"
      ? `Not just you: most agents in ${mine.region} are affected at the same time.`
      : `${mine.region} has a regional outage, but this agent looks fine.`;
  if (me.status === "abnormal") return `Just you: other agents in ${mine.region} look fine.`;
  if (others.length) return `You're fine. ${others.join(", ")} ${others.length > 1 ? "have" : "has"} a problem of their own.`;
  return mine.verdict === "no_data" ? "Waiting for agents to report." : `Everything in ${mine.region} looks normal.`;
}

export default function RegionCard({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const { data, error } = usePoll<RegionsResponse>("/api/regions?minutes=10");
  const regions = data?.regions ?? [];

  return (
    <section className="card regions">
      <h2 className="card-title">Just me, or everyone?</h2>
      <p className="body region-headline">{data ? headline(regions, data.self_agent) : error ?? "Loading…"}</p>

      {regions.map((r) => (
        <div key={r.region} className="region">
          <div className="region-head">
            <span className="region-name">{r.region}</span>
            <span className={`verdict verdict-${r.verdict}`}>{VERDICT_TEXT[r.verdict]}</span>
            <span className="caption region-ratio">
              {r.abnormal_ratio == null ? "" : `${Math.round(r.abnormal_ratio * 100)}% abnormal in 10 min`}
            </span>
          </div>

          <div className="timeline" aria-label="Agents abnormal per minute, last 10 minutes">
            {r.timeline.map((b) => (
              <span
                key={b.start}
                className="timeline-bar"
                title={`${new Date(b.start * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}: ${b.abnormal}/${b.reporting} abnormal`}
              >
                <span
                  className="timeline-fill"
                  style={{ height: b.reporting ? `${(b.abnormal / Math.max(1, r.agents.length)) * 100}%` : "0%" }}
                />
              </span>
            ))}
          </div>
          <p className="caption timeline-caption">Agents with a problem, per minute (last 10 min)</p>

          <ul className="agent-list">
            {r.agents.map((a) => (
              <li key={a.id}>
                <button
                  className={`agent-row agent-${a.status} ${selected === a.id ? "agent-selected" : ""}`}
                  onClick={() => onSelect(a.id)}
                  title="Show this agent's history"
                >
                  <span className="agent-dot" aria-hidden />
                  <span className="agent-name">
                    {a.name}
                    {a.id === data?.self_agent && <span className="agent-self"> (this server)</span>}
                  </span>
                  <span className="agent-issue">{issueText(a)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}

      <p className="caption region-source">
        {data?.note ??
          (data?.source === "supabase" ? "From Supabase, all agents" : "Agents on this server's host only (Supabase off)")}
        {" · "}A region is in outage when most of its reporting agents are abnormal in the same 30 seconds.
      </p>
    </section>
  );
}
