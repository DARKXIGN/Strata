"""Regional aggregation: is it my problem or everyone's in my region?

Pure functions over diagnosis records ({agent_id, ts, label, anomaly_score}) so they work
the same on Supabase rows and on the local SQLite history.

Rules (documented in the API and on the dashboard):
  * A record is abnormal if the classifier says anything but "normal", or the anomaly
    score (largest robust z of any feature) is at least ANOMALY_LIMIT.
  * An agent is abnormal "now" if at least half of its records in the last
    `status_seconds` (30 s: about 3 records at the default rate) are abnormal. The same window is used for every agent, so "now"
    means the same time period across the region. No records in that window: silent.
  * Region verdict over the reporting (non-silent) agents:
        regional_outage   at least 2 reporting and more than half of them abnormal
        individual_issue  some, but not a majority, abnormal (or the only agent is)
        normal            none abnormal
        no_data           nobody reporting
  * abnormal_ratio: abnormal records / all records in the last `window_seconds` (10 min).
"""

from __future__ import annotations

from collections import Counter, defaultdict

ANOMALY_LIMIT = 8.0  # same as ml.anomaly.RANGE_LIMIT


def is_abnormal(label: str | None, anomaly_score: float | None) -> bool:
    if label and label != "normal":
        return True
    return anomaly_score is not None and anomaly_score >= ANOMALY_LIMIT


def summarize(records: list[dict], agents: list[dict], now: float,
              window_seconds: float = 600, status_seconds: float = 30,
              bucket_seconds: float = 60) -> dict:
    """records: dicts with agent_id, ts (epoch s), label, anomaly_score.
    agents: dicts with id, name, region (agents with no records are listed as silent)."""
    meta = {a["id"]: a for a in agents}
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r["ts"] >= now - window_seconds:
            by_agent[r["agent_id"]].append(r)
            meta.setdefault(r["agent_id"], {"id": r["agent_id"], "name": r["agent_id"], "region": "unknown"})

    agent_rows = []
    for agent_id, a in sorted(meta.items()):
        rows = sorted(by_agent.get(agent_id, []), key=lambda r: r["ts"])
        recent = [r for r in rows if r["ts"] >= now - status_seconds]
        abnormal_recent = [r for r in recent if is_abnormal(r["label"], r.get("anomaly_score"))]
        if not recent:
            status = "silent"
        elif len(abnormal_recent) * 2 >= len(recent):
            status = "abnormal"
        else:
            status = "normal"
        labels = Counter(r["label"] for r in abnormal_recent if r["label"] != "normal")
        last = rows[-1] if rows else None
        agent_rows.append({
            "id": agent_id,
            "name": a.get("name") or agent_id,
            "region": a.get("region") or "unknown",
            "status": status,
            "main_issue": labels.most_common(1)[0][0] if labels else (
                "unusual pattern" if abnormal_recent else None),
            "records": len(rows),
            "abnormal_records": sum(is_abnormal(r["label"], r.get("anomaly_score")) for r in rows),
            "last_ts": last["ts"] if last else None,
            "last_label": last["label"] if last else None,
        })

    regions = []
    for region in sorted({a["region"] for a in agent_rows}):
        members = [a for a in agent_rows if a["region"] == region]
        reporting = [a for a in members if a["status"] != "silent"]
        abnormal = [a for a in reporting if a["status"] == "abnormal"]
        total = sum(a["records"] for a in members)
        bad = sum(a["abnormal_records"] for a in members)
        if not reporting:
            verdict = "no_data"
        elif not abnormal:
            verdict = "normal"
        elif len(reporting) >= 2 and len(abnormal) * 2 > len(reporting):
            verdict = "regional_outage"
        else:
            verdict = "individual_issue"
        regions.append({
            "region": region,
            "verdict": verdict,
            "agents": members,
            "reporting": len(reporting),
            "abnormal_agents": len(abnormal),
            "abnormal_ratio": round(bad / total, 3) if total else None,
            "timeline": _timeline(records, {a["id"] for a in members}, now, window_seconds, bucket_seconds),
        })

    return {"now": now, "window_seconds": window_seconds, "status_seconds": status_seconds, "regions": regions}


def _timeline(records, member_ids, now, window_seconds, bucket_seconds) -> list[dict]:
    """Per time bucket: how many agents reported and how many were mostly abnormal."""
    n = int(window_seconds // bucket_seconds)
    start = now - n * bucket_seconds
    buckets: list[dict[str, list[bool]]] = [defaultdict(list) for _ in range(n)]
    for r in records:
        if r["agent_id"] in member_ids and start <= r["ts"] < now:
            i = min(n - 1, int((r["ts"] - start) // bucket_seconds))
            buckets[i][r["agent_id"]].append(is_abnormal(r["label"], r.get("anomaly_score")))
    out = []
    for i, b in enumerate(buckets):
        abnormal = sum(1 for flags in b.values() if sum(flags) * 2 >= len(flags))
        out.append({"start": round(start + i * bucket_seconds, 1), "reporting": len(b), "abnormal": abnormal})
    return out
