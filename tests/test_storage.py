"""Storage add-on: config, local queue, uploader, Supabase REST client, aggregation.

Nothing here talks to Supabase: a fake client stands in for it, and the REST client is
exercised against a local HTTP server.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from storage import aggregate
from storage import config as scfg
from storage.local import LocalStore
from storage.service import StorageService, epoch, iso
from storage.supabase_rest import StorageError, SupabaseRest

SECRET = "sb_secret_TESTONLY_abcdefghijklmnopqrstuvwxyz0123"


class FakeSupabase:
    """Same methods as SupabaseRest, in memory. Set .down = True to simulate an outage."""

    def __init__(self) -> None:
        self.agents_rows: dict[str, dict] = {}
        self.rows: dict[str, dict] = {}
        self.down = False
        self.insert_calls = 0

    def _check(self):
        if self.down:
            raise StorageError("could not reach Supabase (URLError: offline)")

    def upsert_agent(self, agent):
        self._check()
        self.agents_rows[agent["id"]] = dict(agent)

    def insert_diagnoses(self, rows):
        self._check()
        self.insert_calls += 1
        for r in rows:
            if r["agent_id"] not in self.agents_rows:
                raise StorageError("HTTP 409 from Supabase: violates foreign key constraint")
            self.rows.setdefault(r["id"], dict(r))  # ignore-duplicates

    def agents(self):
        self._check()
        return list(self.agents_rows.values())

    def history(self, agent_id, limit):
        self._check()
        rows = sorted((r for r in self.rows.values() if r["agent_id"] == agent_id), key=lambda r: r["ts"], reverse=True)
        return rows[:limit]

    def recent(self, since_iso, limit=10000):
        self._check()
        since = epoch(since_iso)
        return sorted((r for r in self.rows.values() if epoch(r["ts"]) >= since), key=lambda r: r["ts"])[:limit]


def tick(label="normal", ts=1000.0, z=0.5):
    return {"ts": ts, "diagnosis": {"label": label, "probs": {label: 0.97}},
            "anomaly": {"z": z, "unusual": abs(z) > 8},
            "features": {"ext_rtt_ms": 33.5, "throughput_mbps": 108.0, "wifi_signal_pct": None}}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_service(tmp, client=None, agent="strata-a", region="seoul", clock=None, upload=True, record_seconds=10):
    cfg = scfg.StorageConfig(url="https://x.supabase.co" if upload else "", key=SECRET if upload else "",
                             agent_id=agent, agent_name=agent, region=region, db_path=Path(tmp) / "s.sqlite",
                             record_seconds=record_seconds, upload_enabled=upload, reason="ok" if upload else "off")
    return StorageService(cfg, store=LocalStore(cfg.db_path), client=client if upload else None,
                          clock=clock or Clock(), cache_seconds=0)


class ConfigTests(unittest.TestCase):
    def test_missing_values_disable_quietly(self):
        c = scfg.load({})
        self.assertFalse(c.upload_enabled)
        self.assertIn("not set", c.reason)

    def test_valid_secret_key(self):
        c = scfg.load({"SUPABASE_URL": "https://abc.supabase.co/", "SUPABASE_SECRET_KEY": SECRET,
                       "STRATA_NETNS": "strata-b", "STRATA_REGION": "seoul"})
        self.assertTrue(c.upload_enabled)
        self.assertEqual((c.agent_id, c.region, c.url), ("strata-b", "seoul", "https://abc.supabase.co"))

    def test_rejected_keys_and_urls(self):
        cases = {
            "eyJhbGciOiJIUzI1NiJ9.legacy.jwt": "legacy JWT",
            "sb_publishable_abcdefghijklmnop": "publishable",
            "some-random-token-value": "sb_secret_",
        }
        for key, reason in cases.items():
            c = scfg.load({"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SECRET_KEY": key})
            self.assertFalse(c.upload_enabled, key)
            self.assertIn(reason, c.reason)
            self.assertEqual(c.key, "")  # an unusable key is not even kept
        c = scfg.load({"SUPABASE_URL": "http://abc.supabase.co", "SUPABASE_SECRET_KEY": SECRET})
        self.assertFalse(c.upload_enabled)
        self.assertIn("https", c.reason)

    def test_old_variable_name_is_explained(self):
        c = scfg.load({"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SERVICE_KEY": SECRET})
        self.assertFalse(c.upload_enabled)
        self.assertIn("SUPABASE_SECRET_KEY", c.reason)

    def test_key_never_in_repr_or_public(self):
        c = scfg.load({"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SECRET_KEY": SECRET})
        for text in (repr(c), str(c), json.dumps(c.public())):
            self.assertNotIn(SECRET, text)
        self.assertEqual(c.public()["key_fingerprint"], scfg.fingerprint(SECRET))
        self.assertEqual(len(c.public()["key_fingerprint"]), 8)


class LocalStoreTests(unittest.TestCase):
    def test_backoff_and_prune(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStore(Path(d) / "q.sqlite")
            for i in range(5):
                s.add({"id": f"r{i}", "agent_id": "a", "region": "x", "ts": 100.0 + i, "label": "normal",
                       "probs": {}, "anomaly_score": None, "features": {}}, pending=True)
            self.assertEqual(len(s.take_pending("a", 10, now=200)), 5)
            self.assertEqual(s.take_pending("b", 10, now=200), [])  # other agents' rows are not ours to send
            s.mark_failed(["r0", "r1"], now=200)
            self.assertEqual({r["id"] for r in s.take_pending("a", 10, now=201)}, {"r2", "r3", "r4"})
            self.assertEqual(len(s.take_pending("a", 10, now=206)), 5)  # 5 s backoff passed
            s.mark_sent(["r0", "r1", "r2", "r3", "r4"])
            self.assertEqual(s.pending_count("a"), 0)
            self.assertEqual(s.prune(now=100.0 + 86400 + 10), 5)

    def test_pending_cap_drops_oldest(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStore(Path(d) / "q.sqlite")
            for i in range(10):
                s.add({"id": f"r{i}", "agent_id": "a", "region": "x", "ts": float(i), "label": "normal",
                       "probs": {}, "anomaly_score": None, "features": {}}, pending=True)
            s.prune(now=20, max_pending=6)
            self.assertEqual([r["id"] for r in s.take_pending("a", 20, now=20)], [f"r{i}" for i in range(4, 10)])


class ServiceTests(unittest.TestCase):
    def test_record_interval_and_label_change(self):
        with tempfile.TemporaryDirectory() as d:
            svc = make_service(d, FakeSupabase())
            self.assertTrue(svc.offer(tick("normal", 1000)))
            self.assertFalse(svc.offer(tick("normal", 1003)))       # within 10 s
            self.assertTrue(svc.offer(tick("dns_failure", 1004)))   # label changed
            self.assertTrue(svc.offer(tick("dns_failure", 1015)))   # interval passed
            rows = svc.store.history("strata-a", 10)
            self.assertEqual(len(rows), 3)
            self.assertNotIn("wifi_signal_pct", rows[0]["features"])  # None values dropped

    def test_upload_success_failure_and_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            fake, clock = FakeSupabase(), Clock(1000)
            svc = make_service(d, fake, clock=clock)
            for i in range(3):
                svc.offer(tick("normal", 1000 + 11 * i))
            self.assertEqual(svc.upload_once(), {"sent": 3})
            self.assertEqual(len(fake.rows), 3)
            self.assertIn("strata-a", fake.agents_rows)
            row = next(iter(fake.rows.values()))
            self.assertTrue(row["ts"].endswith("+00:00"))
            self.assertEqual(row["probs"], {"normal": 0.97})

            fake.down = True                                   # Supabase or internet down
            svc.offer(tick("high_latency", 1100))
            out = svc.upload_once()
            self.assertEqual(out["sent"], 0)
            self.assertIn("could not reach", out["error"])
            self.assertEqual(svc.store.pending_count("strata-a"), 1)   # kept for retry
            self.assertIn("could not reach", svc.status()["last_error"])

            fake.down = False
            clock.t += 1
            self.assertEqual(svc.upload_once()["sent"], 0)     # still backing off
            clock.t += 10
            self.assertEqual(svc.upload_once()["sent"], 1)     # recovered
            self.assertEqual(len(fake.rows), 4)
            self.assertIsNone(svc.status()["last_error"])

    def test_retry_never_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeSupabase()
            svc = make_service(d, fake)
            svc.offer(tick("normal", 1000))
            batch = svc.store.take_pending("strata-a", 10, 2000)
            fake.upsert_agent({"id": "strata-a", "name": "a", "region": "seoul"})
            fake.insert_diagnoses([{**batch[0], "ts": iso(batch[0]["ts"])}])  # sent, but the ack was lost
            svc.upload_once()
            self.assertEqual(len(fake.rows), 1)

    def test_upload_disabled_keeps_local_history_only(self):
        with tempfile.TemporaryDirectory() as d:
            svc = make_service(d, upload=False)
            svc.offer(tick("packet_loss", 1000))
            self.assertEqual(svc.upload_once(), {"sent": 0, "skipped": "upload disabled"})
            self.assertEqual(svc.store.pending_count("strata-a"), 0)
            h = svc.history()
            self.assertEqual((h["source"], len(h["records"])), ("local", 1))
            self.assertTrue(h["records"][0]["abnormal"])

    def test_storage_errors_never_raise_into_the_tick_loop(self):
        with tempfile.TemporaryDirectory() as d:
            svc = make_service(d, FakeSupabase())
            svc.store.close()  # every write now fails
            self.assertFalse(svc.offer(tick("normal", 1000)))
            self.assertIn("local store write failed", svc.status()["last_error"])

    def test_queries_fall_back_to_local_when_supabase_is_down(self):
        with tempfile.TemporaryDirectory() as d:
            fake, clock = FakeSupabase(), Clock(1000)
            svc = make_service(d, fake, clock=clock)
            svc.offer(tick("dns_slow", 990))
            svc.upload_once()
            self.assertEqual(svc.history()["source"], "supabase")
            self.assertEqual(svc.regions()["source"], "supabase")
            fake.down = True
            h = svc.history()
            self.assertEqual(h["source"], "local")
            self.assertIn("Supabase unavailable", h["note"])
            r = svc.regions()
            self.assertEqual(r["source"], "local")
            self.assertEqual(r["regions"][0]["agents"][0]["status"], "abnormal")

    def test_status_and_errors_never_contain_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            class Leaky(FakeSupabase):
                def insert_diagnoses(self, rows):
                    raise StorageError("HTTP 401 from Supabase: invalid key")

            svc = make_service(d, Leaky())
            svc.offer(tick("normal", 1000))
            with self.assertLogs("strata.storage", "WARNING") as logs:
                svc.upload_once()
            blob = json.dumps(svc.status()) + json.dumps(svc.history()) + "".join(logs.output)
            self.assertNotIn(SECRET, blob)


class AggregateTests(unittest.TestCase):
    AGENTS = [{"id": f"strata-{x}", "name": x, "region": "seoul"} for x in "abc"]

    def records(self, abnormal: set[str], now=10_000.0, other="normal"):
        out = []
        for a in "abc":
            for t in range(0, 600, 10):
                label = "high_latency" if f"strata-{a}" in abnormal and t >= 480 else other
                out.append({"agent_id": f"strata-{a}", "ts": now - 600 + t, "label": label, "anomaly_score": 0.5})
        return out

    def verdict(self, abnormal: set[str]):
        out = aggregate.summarize(self.records(abnormal), self.AGENTS, now=10_000.0)
        return out["regions"][0]

    def test_all_normal(self):
        r = self.verdict(set())
        self.assertEqual((r["verdict"], r["abnormal_agents"], r["abnormal_ratio"]), ("normal", 0, 0.0))

    def test_one_of_three_is_individual(self):
        r = self.verdict({"strata-b"})
        self.assertEqual((r["verdict"], r["abnormal_agents"], r["reporting"]), ("individual_issue", 1, 3))
        b = next(a for a in r["agents"] if a["id"] == "strata-b")
        self.assertEqual((b["status"], b["main_issue"]), ("abnormal", "high_latency"))
        self.assertAlmostEqual(r["abnormal_ratio"], 12 / 180, places=3)

    def test_majority_is_regional(self):
        self.assertEqual(self.verdict({"strata-a", "strata-b"})["verdict"], "regional_outage")
        self.assertEqual(self.verdict({"strata-a", "strata-b", "strata-c"})["verdict"], "regional_outage")

    def test_single_agent_cannot_be_regional(self):
        out = aggregate.summarize(
            [{"agent_id": "solo", "ts": 9990.0, "label": "dns_failure", "anomaly_score": None}],
            [{"id": "solo", "name": "solo", "region": "busan"}], now=10_000.0)
        self.assertEqual(out["regions"][0]["verdict"], "individual_issue")

    def test_silent_agents_and_regions(self):
        agents = self.AGENTS + [{"id": "strata-z", "name": "z", "region": "busan"}]
        out = aggregate.summarize(self.records({"strata-a"}), agents, now=10_000.0)
        busan = next(r for r in out["regions"] if r["region"] == "busan")
        self.assertEqual((busan["verdict"], busan["agents"][0]["status"]), ("no_data", "silent"))

    def test_anomaly_score_alone_counts(self):
        self.assertTrue(aggregate.is_abnormal("normal", 12.0))
        self.assertFalse(aggregate.is_abnormal("normal", 3.0))
        self.assertTrue(aggregate.is_abnormal("packet_loss", None))

    def test_timeline_buckets(self):
        r = self.verdict({"strata-a", "strata-b"})
        self.assertEqual(len(r["timeline"]), 10)
        self.assertEqual(r["timeline"][0]["abnormal"], 0)
        self.assertEqual(r["timeline"][-1], {**r["timeline"][-1], "reporting": 3, "abnormal": 2})


class RecordingHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []
    mode = "ok"

    def log_message(self, *args):
        pass

    def _record(self):
        n = int(self.headers.get("Content-Length") or 0)
        RecordingHandler.calls.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                       "body": json.loads(self.rfile.read(n)) if n else None})

    def do_GET(self):
        self._record()
        if RecordingHandler.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/steal")
            self.end_headers()
            return
        body = json.dumps([{"id": "strata-a", "name": "a", "region": "seoul"}]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self._record()
        if RecordingHandler.mode == "echo-key-error":
            body = json.dumps({"message": f"bad key {self.headers.get('apikey')}"}).encode()
            self.send_response(401)
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(201)
        self.end_headers()


class SupabaseRestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        RecordingHandler.calls.clear()
        RecordingHandler.mode = "ok"
        self.client = SupabaseRest(self.url, SECRET, timeout=2, _allow_http=True)

    def test_key_only_in_apikey_header(self):
        self.client.insert_diagnoses([{"id": "1", "agent_id": "strata-a", "ts": iso(1000.0), "label": "normal"}])
        call = RecordingHandler.calls[-1]
        headers = {k.lower(): v for k, v in call["headers"].items()}
        self.assertEqual(headers["apikey"], SECRET)
        self.assertNotIn("authorization", headers)
        self.assertIn("strata-agent", headers["user-agent"])
        self.assertEqual(call["path"], "/rest/v1/diagnoses?on_conflict=id")
        self.assertIn("resolution=ignore-duplicates", headers["prefer"])
        self.assertEqual(call["body"][0]["agent_id"], "strata-a")

    def test_queries_are_encoded(self):
        self.client.recent(iso(1000.0))
        self.client.history("strata-a", 5)
        self.assertIn("ts=gte.1970-01-01T00%3A16%3A40.000%2B00%3A00", RecordingHandler.calls[0]["path"])
        self.assertIn("agent_id=eq.strata-a", RecordingHandler.calls[1]["path"])
        self.assertIn("limit=5", RecordingHandler.calls[1]["path"])

    def test_error_text_never_contains_the_key(self):
        RecordingHandler.mode = "echo-key-error"
        with self.assertRaises(StorageError) as ctx:
            self.client.upsert_agent({"id": "strata-a", "name": "a", "region": "seoul"})
        self.assertIn("HTTP 401", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, repr(self.client))

    def test_redirects_are_not_followed(self):
        RecordingHandler.mode = "redirect"
        with self.assertRaises(StorageError):
            self.client.agents()
        self.assertEqual(len(RecordingHandler.calls), 1)

    def test_plain_http_refused_outside_tests(self):
        with self.assertRaises(ValueError):
            SupabaseRest("http://abc.supabase.co", SECRET)

    def test_unreachable_is_a_storage_error(self):
        dead = SupabaseRest("http://127.0.0.1:9", SECRET, timeout=1, _allow_http=True)
        with self.assertRaises(StorageError) as ctx:
            dead.agents()
        self.assertIn("could not reach", str(ctx.exception))

    def test_upload_uses_this_process_not_a_namespace(self):
        # Uploads must leave from the host network: no `ip netns exec`, no subprocess at all.
        with mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess used")):
            self.client.insert_diagnoses([{"id": "2", "agent_id": "strata-a", "ts": iso(1.0), "label": "normal"}])
        self.assertEqual(RecordingHandler.calls[-1]["method"], "POST")


class TimeTests(unittest.TestCase):
    def test_iso_roundtrip(self):
        self.assertAlmostEqual(epoch(iso(1700000000.123)), 1700000000.123, places=3)
        self.assertAlmostEqual(epoch("2026-11-14T12:00:00Z"), datetime(2026, 11, 14, 12).timestamp()
                               - (datetime(2026, 11, 14, 12).astimezone().utcoffset().total_seconds()), places=0)


if __name__ == "__main__":
    unittest.main()
