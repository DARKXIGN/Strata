"""Fault control across agents: peer config, forwarding, and the server endpoints."""

from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from fastapi import HTTPException

from server.peers import DEFAULT_LAYOUT, PeerControl, PeerError, load_peers, parse_peers


def make_peer(agent_id: str):
    """A tiny stand-in for another agent's Strata server."""
    state = {"active": None, "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            state["calls"].append(("GET", self.path))
            self._send(200, {"sim_mode": True, "active_fault": state["active"],
                             "agent": {"id": agent_id, "name": f"agent-{agent_id[-1]}", "region": "seoul"}})

        def do_POST(self):
            state["calls"].append(("POST", self.path))
            name = self.path.rsplit("/", 1)[-1]
            if name == "reset":
                state["active"] = None
                return self._send(200, {"active": None})
            if name == "bogus":
                return self._send(404, {"detail": "unknown fault: bogus"})
            state["active"] = name
            self._send(200, {"active": name, "params": {}})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}", state


class ConfigTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_peers("strata-b=http://127.0.0.1:8001/, strata-c=http://127.0.0.1:8002"),
                         {"strata-b": "http://127.0.0.1:8001", "strata-c": "http://127.0.0.1:8002"})
        for bad in ("strata-b", "strata-b=ftp://x", "=http://127.0.0.1:1"):
            with self.assertRaises(ValueError):
                parse_peers(bad)

    def test_default_layout_for_agents_only(self):
        self.assertEqual(load_peers("strata-a", {}), {k: v for k, v in DEFAULT_LAYOUT.items() if k != "strata-a"})
        self.assertEqual(set(load_peers("strata-c", {})), {"strata-a", "strata-b"})
        self.assertEqual(load_peers("strata", {}), {})          # single-agent server: no peers
        self.assertEqual(load_peers("laptop", {}), {})

    def test_explicit_peers_override_and_exclude_self(self):
        env = {"STRATA_PEERS": "strata-a=http://127.0.0.1:9000,x=http://127.0.0.1:9001"}
        self.assertEqual(load_peers("strata-a", env), {"x": "http://127.0.0.1:9001"})


class PeerControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b, cls.b_url, cls.b_state = make_peer("strata-b")
        cls.c, cls.c_url, cls.c_state = make_peer("strata-c")

    @classmethod
    def tearDownClass(cls):
        cls.b.shutdown()
        cls.c.shutdown()

    def setUp(self):
        self.b_state["active"] = self.c_state["active"] = None
        self.ctl = PeerControl("strata-a", {"strata-b": self.b_url, "strata-c": self.c_url,
                                            "strata-d": "http://127.0.0.1:9"}, timeout=1)

    def test_status_all_reports_reachability_and_faults(self):
        self.c_state["active"] = "dns_slow"
        rows = {r["id"]: r for r in self.ctl.status_all()}
        self.assertEqual((rows["strata-b"]["reachable"], rows["strata-b"]["name"]), (True, "agent-b"))
        self.assertEqual(rows["strata-c"]["active_fault"], "dns_slow")
        self.assertFalse(rows["strata-d"]["reachable"])
        self.assertIn("not reachable", rows["strata-d"]["error"])

    def test_inject_and_reset_are_forwarded(self):
        self.assertEqual(self.ctl.inject("strata-b", "high_latency")["active"], "high_latency")
        self.assertEqual(self.b_state["active"], "high_latency")
        self.assertIn(("POST", "/api/faults/high_latency"), self.b_state["calls"])
        self.ctl.reset("strata-b")
        self.assertIsNone(self.b_state["active"])

    def test_errors_are_peer_errors(self):
        with self.assertRaises(PeerError) as ctx:
            self.ctl.inject("strata-b", "bogus")
        self.assertIn("HTTP 404", str(ctx.exception))
        with self.assertRaises(PeerError):
            self.ctl.inject("strata-d", "high_latency")

    def test_ignores_proxy_environment(self):
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9"}):
            ctl = PeerControl("strata-a", {"strata-b": self.b_url}, timeout=1)
            self.assertTrue(ctl.status("strata-b")["reachable"])


class ServerEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b, cls.b_url, cls.b_state = make_peer("strata-b")
        from server import main

        cls.main = main
        cls.orig = (main.peers, main.SELF_ID)
        main.SELF_ID = "strata-a"
        main.peers = PeerControl("strata-a", {"strata-b": cls.b_url, "strata-c": "http://127.0.0.1:9"}, timeout=1)

    @classmethod
    def tearDownClass(cls):
        cls.main.peers, cls.main.SELF_ID = cls.orig
        cls.main.faults.reset()
        cls.b.shutdown()

    def setUp(self):
        self.main.faults.reset()
        self.b_state["active"] = None

    def test_control_lists_self_first(self):
        out = self.main.agents_control()
        self.assertEqual([a["id"] for a in out["agents"]], ["strata-a", "strata-b", "strata-c"])
        self.assertTrue(out["agents"][0]["self"])
        self.assertFalse(out["agents"][2]["reachable"])

    def test_self_and_peer_faults(self):
        self.main.agent_inject("strata-a", "dns_failure")
        self.assertEqual(self.main.faults.state.name, "dns_failure")
        self.main.agent_inject("strata-b", "packet_loss")
        self.assertEqual(self.b_state["active"], "packet_loss")
        self.main.agent_reset("strata-a")
        self.assertIsNone(self.main.faults.state.name)

    def test_unknown_agent_and_fault_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            self.main.agent_inject("strata-z", "dns_failure")
        self.assertEqual(ctx.exception.status_code, 404)
        with self.assertRaises(HTTPException) as ctx:
            self.main.agent_inject("strata-b", "weak_wifi")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_unreachable_peer_is_502(self):
        with self.assertRaises(HTTPException) as ctx:
            self.main.agent_inject("strata-c", "dns_slow")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("not reachable", ctx.exception.detail)

    def test_all_applies_everywhere_best_effort(self):
        out = self.main.agent_inject("all", "high_latency")
        by_id = {r["id"]: r for r in out["results"]}
        self.assertTrue(by_id["strata-a"]["ok"] and by_id["strata-b"]["ok"])
        self.assertFalse(by_id["strata-c"]["ok"])
        self.assertEqual((self.main.faults.state.name, self.b_state["active"]), ("high_latency", "high_latency"))
        self.main.agent_reset("all")
        self.assertEqual((self.main.faults.state.name, self.b_state["active"]), (None, None))


if __name__ == "__main__":
    unittest.main()
