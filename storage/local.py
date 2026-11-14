"""Local SQLite store: upload queue and recent history in one table.

Every diagnosis is written here first (pending=1 when uploads are on). The uploader takes
pending rows for its own agent, sends them, and marks them sent; failures stay pending with
a backoff. Several agent processes on one host share the file (WAL mode, busy timeout), so
local history and regional aggregation work even with Supabase off.

Retention keeps the file small: rows already sent (or never meant to be sent) are dropped
after `keep_seconds`; if uploads are failing for a long time, the oldest pending rows beyond
`max_pending` are dropped so the disk never fills.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id            TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    region        TEXT NOT NULL,
    ts            REAL NOT NULL,
    label         TEXT NOT NULL,
    probs         TEXT NOT NULL,
    anomaly_score REAL,
    features      TEXT NOT NULL,
    pending       INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_try      REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS records_agent_ts ON records (agent_id, ts);
CREATE INDEX IF NOT EXISTS records_ts ON records (ts);
CREATE INDEX IF NOT EXISTS records_pending ON records (pending, agent_id, next_try);
CREATE TABLE IF NOT EXISTS agents (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    region    TEXT NOT NULL,
    last_seen REAL NOT NULL
);
"""


class LocalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writes -------------------------------------------------------------------

    def touch_agent(self, agent_id: str, name: str, region: str, now: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO agents (id, name, region, last_seen) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, region=excluded.region, last_seen=excluded.last_seen",
                (agent_id, name, region, now))

    def add(self, rec: dict, pending: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO records (id, agent_id, region, ts, label, probs, anomaly_score, features, pending) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rec["id"], rec["agent_id"], rec["region"], rec["ts"], rec["label"], json.dumps(rec["probs"]),
                 rec["anomaly_score"], json.dumps(rec["features"]), 1 if pending else 0))

    def take_pending(self, agent_id: str, limit: int, now: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM records WHERE pending=1 AND agent_id=? AND next_try<=? ORDER BY ts LIMIT ?",
                (agent_id, now, limit)).fetchall()
        return [self._row(r) for r in rows]

    def mark_sent(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._lock:
            self._db.executemany("UPDATE records SET pending=0 WHERE id=?", [(i,) for i in ids])

    def mark_failed(self, ids: list[str], now: float, base: float = 5.0, cap: float = 300.0) -> None:
        with self._lock:
            for i in ids:
                self._db.execute(
                    "UPDATE records SET attempts=attempts+1, "
                    "next_try=? + min(?, ? * (1 << min(attempts, 10))) WHERE id=?",
                    (now, cap, base, i))

    def prune(self, now: float, keep_seconds: float = 86400, max_pending: int = 100_000) -> int:
        with self._lock:
            n = self._db.execute("DELETE FROM records WHERE pending=0 AND ts < ?", (now - keep_seconds,)).rowcount
            over = self._db.execute("SELECT COUNT(*) FROM records WHERE pending=1").fetchone()[0] - max_pending
            if over > 0:
                n += self._db.execute(
                    "DELETE FROM records WHERE id IN (SELECT id FROM records WHERE pending=1 ORDER BY ts LIMIT ?)",
                    (over,)).rowcount
        return n

    # -- reads --------------------------------------------------------------------

    def pending_count(self, agent_id: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM records WHERE pending=1 AND agent_id=?",
                                    (agent_id,)).fetchone()[0]

    def history(self, agent_id: str, limit: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM records WHERE agent_id=? ORDER BY ts DESC LIMIT ?",
                                    (agent_id, limit)).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, since: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT agent_id, ts, label, anomaly_score FROM records WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        return [dict(r) for r in rows]

    def agents(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, name, region, last_seen FROM agents ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["probs"] = json.loads(d["probs"])
        d["features"] = json.loads(d["features"])
        return d
