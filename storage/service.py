"""Storage service: record diagnoses locally, upload them in the background, answer queries.

Bolted onto the server without touching the measure -> classify -> explain path:
  * offer(tick) is called after a tick has been broadcast; it only writes to local SQLite.
  * the uploader runs as its own background task and talks to Supabase from the host
    network (this process), never from a fault namespace.
  * every method swallows its own errors; storage problems are reported in status() and
    never reach the tick loop or the dashboard's realtime view.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from storage import aggregate
from storage.config import StorageConfig
from storage.local import LocalStore
from storage.supabase_rest import StorageError, SupabaseRest

log = logging.getLogger("strata.storage")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds")


def epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


class StorageService:
    def __init__(self, cfg: StorageConfig, store: LocalStore | None = None, client=None,
                 clock=time.time, cache_seconds: float = 5.0) -> None:
        self.cfg = cfg
        self.clock = clock
        self.store = store or LocalStore(cfg.db_path)
        if client is not None:
            self.client = client
        elif cfg.upload_enabled:
            self.client = SupabaseRest(cfg.url, cfg.key)
        else:
            self.client = None
        self._last_recorded_at = 0.0
        self._last_label: str | None = None
        self._agent_synced = False
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self.cache_seconds = cache_seconds
        self.stats = {"recorded": 0, "uploaded": 0, "last_upload_at": None, "last_error": None,
                      "last_error_at": None}
        self.store.touch_agent(cfg.agent_id, cfg.agent_name, cfg.region, clock())

    @property
    def uploading(self) -> bool:
        return self.client is not None

    # -- recording ------------------------------------------------------------------

    def should_record(self, label: str, now: float) -> bool:
        return label != self._last_label or now - self._last_recorded_at >= self.cfg.record_seconds

    def offer(self, tick: dict) -> bool:
        """Maybe store this tick. Never raises."""
        try:
            label = tick["diagnosis"]["label"]
            now = float(tick.get("ts") or self.clock())
            if not self.should_record(label, now):
                return False
            anomaly = tick.get("anomaly") or {}
            z = anomaly.get("z")
            rec = {
                "id": str(uuid.uuid4()),
                "agent_id": self.cfg.agent_id,
                "region": self.cfg.region,
                "ts": now,
                "label": label,
                "probs": tick["diagnosis"].get("probs", {}),
                "anomaly_score": round(abs(float(z)), 3) if z is not None else None,
                "features": {k: v for k, v in (tick.get("features") or {}).items() if v is not None},
            }
            self.store.add(rec, pending=self.uploading)
            self.store.touch_agent(self.cfg.agent_id, self.cfg.agent_name, self.cfg.region, now)
            self._last_recorded_at, self._last_label = now, label
            self.stats["recorded"] += 1
            return True
        except Exception as exc:
            self._error(f"local store write failed: {type(exc).__name__}")
            return False

    # -- uploading --------------------------------------------------------------------

    def upload_once(self) -> dict:
        """Send one batch of this agent's pending rows. Never raises."""
        if not self.uploading:
            return {"sent": 0, "skipped": "upload disabled"}
        now = self.clock()
        try:
            self.store.prune(now)
            if not self._agent_synced:
                self.client.upsert_agent({"id": self.cfg.agent_id, "name": self.cfg.agent_name,
                                          "region": self.cfg.region})
                self._agent_synced = True
            batch = self.store.take_pending(self.cfg.agent_id, self.cfg.batch_size, now)
            if not batch:
                return {"sent": 0}
            rows = [{
                "id": r["id"], "agent_id": r["agent_id"], "ts": iso(r["ts"]), "label": r["label"],
                "probs": r["probs"], "anomaly_score": r["anomaly_score"], "features": r["features"],
            } for r in batch]
            try:
                self.client.insert_diagnoses(rows)
            except StorageError:
                self.store.mark_failed([r["id"] for r in batch], now)
                raise
            self.store.mark_sent([r["id"] for r in batch])
            self.stats["uploaded"] += len(batch)
            self.stats["last_upload_at"] = now
            self.stats["last_error"] = None
            return {"sent": len(batch)}
        except StorageError as exc:
            self._agent_synced = False  # re-send the agent row next time (cheap, fixes FK errors)
            self._error(str(exc))
            return {"sent": 0, "error": str(exc)}
        except Exception as exc:
            self._error(f"upload failed: {type(exc).__name__}")
            return {"sent": 0, "error": type(exc).__name__}

    async def run_uploader(self) -> None:
        import asyncio

        while True:
            if self.uploading:
                result = await asyncio.to_thread(self.upload_once)
                # Drain a backlog quickly after an outage, otherwise wait.
                if result.get("sent", 0) >= self.cfg.batch_size:
                    continue
            await asyncio.sleep(self.cfg.flush_seconds)

    def _error(self, msg: str) -> None:
        first = self.stats["last_error"] != msg
        self.stats["last_error"], self.stats["last_error_at"] = msg, self.clock()
        if first:  # log a new problem once, not every retry
            log.warning("storage: %s (realtime diagnosis unaffected)", msg)

    # -- queries ----------------------------------------------------------------------

    def _cached(self, key: tuple, build) -> dict:
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_seconds:
                return hit[1]
        value = build()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def history(self, agent_id: str | None = None, limit: int = 30) -> dict:
        agent_id = agent_id or self.cfg.agent_id
        limit = max(1, min(int(limit), 200))

        def build() -> dict:
            source, note = "local", None
            rows = None
            if self.uploading:
                try:
                    rows = [{"id": r["id"], "agent_id": r["agent_id"], "ts": epoch(r["ts"]), "label": r["label"],
                             "probs": r.get("probs") or {}, "anomaly_score": r.get("anomaly_score")}
                            for r in self.client.history(agent_id, limit)]
                    source = "supabase"
                except StorageError as exc:
                    note = f"Supabase unavailable ({exc}); showing local history"
            if rows is None:
                rows = [{k: r[k] for k in ("id", "agent_id", "ts", "label", "probs", "anomaly_score")}
                        for r in self.store.history(agent_id, limit)]
            for r in rows:
                r["abnormal"] = aggregate.is_abnormal(r["label"], r["anomaly_score"])
            return {"agent_id": agent_id, "source": source, "note": note, "records": rows}

        return self._cached(("history", agent_id, limit), build)

    def regions(self, minutes: float = 10) -> dict:
        window = max(60.0, min(float(minutes), 60.0) * 60)

        def build() -> dict:
            now = self.clock()
            source, note = "local", None
            records = agents = None
            if self.uploading:
                try:
                    agents = self.client.agents()
                    records = [{"agent_id": r["agent_id"], "ts": epoch(r["ts"]), "label": r["label"],
                                "anomaly_score": r.get("anomaly_score")}
                               for r in self.client.recent(iso(now - window))]
                    source = "supabase"
                except StorageError as exc:
                    note = f"Supabase unavailable ({exc}); showing agents on this host only"
                    records = agents = None
            if records is None:
                agents = self.store.agents()
                records = self.store.recent(now - window)
            out = aggregate.summarize(records, agents, now, window_seconds=window)
            out.update(source=source, note=note, self_agent=self.cfg.agent_id)
            return out

        return self._cached(("regions", window), build)

    def status(self) -> dict:
        out = self.cfg.public()
        try:
            pending = self.store.pending_count(self.cfg.agent_id)
        except Exception:
            pending = None
        out.update(pending=pending, **self.stats)
        return out
