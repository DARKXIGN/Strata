"""LLM explanation against a fake OpenAI-compatible server, plus key handling."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from collector import sim
from explain import config as cfg
from explain.llm import LLMError, build_messages, parse_reply
from explain.service import ExplainService

KEY = "sk-test-0123456789abcdefSECRET"
GOOD = {"summary": "Your link adds about 150 ms. The gateway is fine, so the delay is past your router.",
        "actions": ["Pause big downloads.", "Call your ISP with this time."]}


class FakeLLM(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        kind = self.path.split("/")[1]
        if kind == "slow":
            time.sleep(1.0)
        if kind == "auth":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(f"bad key {self.headers.get('Authorization')}".encode())
            return
        content = "Sure! ```json\n" + json.dumps(GOOD) + "\n```" if kind != "bad" else "I think it is DNS."
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


class ExplainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def settings(self, kind: str, mode: str = "api", timeout: float = 0.5) -> cfg.ExplainSettings:
        return cfg.ExplainSettings(mode=mode, base_url=f"http://127.0.0.1:{self.port}/{kind}/v1",
                                   model="test-model", api_key=KEY, timeout=timeout)

    def wait_for(self, svc, label, row, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            out = svc.explain(label, {label: 0.9}, row, "isp")
            if not out["pending"]:
                return out
            time.sleep(0.05)
        self.fail("LLM call never finished")

    def test_template_first_then_llm(self):
        svc = ExplainService(self.settings("ok"))
        row = sim.measure("high_latency")
        first = svc.explain("high_latency", {"high_latency": 0.9}, row, "isp")
        self.assertEqual(first["mode"], "template")
        self.assertTrue(first["pending"])
        out = self.wait_for(svc, "high_latency", row)
        self.assertEqual(out["mode"], "api")
        self.assertEqual(out["summary"], GOOD["summary"])
        self.assertEqual(out["actions"], GOOD["actions"])
        call = FakeLLM.seen[-1]
        self.assertTrue(call["path"].endswith("/chat/completions"))
        self.assertEqual(call["auth"], f"Bearer {KEY}")
        self.assertEqual(call["body"]["model"], "test-model")

    def test_timeout_falls_back_to_template(self):
        svc = ExplainService(self.settings("slow", timeout=0.3))
        out = self.wait_for(svc, "dns_slow", sim.measure("dns_slow"))
        self.assertEqual(out["mode"], "template")
        self.assertIn("timed out", out["fallback"])

    def test_bad_reply_falls_back(self):
        svc = ExplainService(self.settings("bad"))
        out = self.wait_for(svc, "dns_failure", sim.measure("dns_failure"))
        self.assertEqual(out["mode"], "template")
        self.assertIn("not JSON", out["fallback"])

    def test_auth_error_never_shows_key(self):
        svc = ExplainService(self.settings("auth"))
        out = self.wait_for(svc, "packet_loss", sim.measure("packet_loss"))
        self.assertIn("HTTP 401", out["fallback"])
        self.assertNotIn(KEY, json.dumps(out))
        result = svc.test(self.settings("auth"))
        self.assertFalse(result["ok"])
        self.assertNotIn(KEY, json.dumps(result))

    def test_unreachable_server(self):
        s = cfg.ExplainSettings(mode="local", base_url="http://127.0.0.1:9/v1", model="m", timeout=0.5)
        result = ExplainService(s).test(s)
        self.assertFalse(result["ok"])
        self.assertIn("could not reach", result["error"])

    def test_local_mode_needs_no_key(self):
        s = cfg.ExplainSettings(mode="local", base_url=f"http://127.0.0.1:{self.port}/ok/v1", model="llama3")
        result = ExplainService(s).test(s)
        self.assertTrue(result["ok"], result)
        self.assertIsNone(FakeLLM.seen[-1]["auth"])

    def test_not_ready_reports_reason(self):
        svc = ExplainService(cfg.ExplainSettings(mode="api", base_url="https://x/v1", model="m"))
        out = svc.explain("normal", {"normal": 1.0}, sim.measure(None), None)
        self.assertEqual(out["fallback"], "no API key")
        self.assertFalse(out["pending"])


class PromptTests(unittest.TestCase):
    def test_prompt_carries_diagnosis_not_a_question(self):
        msgs = build_messages("dns_failure", {"dns_failure": 0.98, "normal": 0.01}, sim.measure("dns_failure"), "dns")
        facts = json.loads(msgs[1]["content"])
        self.assertEqual(facts["diagnosis"], "DNS resolver failure")
        self.assertIn("never replace", msgs[0]["content"])
        self.assertNotIn("configured DNS lookup, ms", facts["measurements"])  # failed lookup has no time

    def test_parse_reply_limits(self):
        summary, actions = parse_reply(json.dumps({"summary": "x" * 900, "actions": ["a", "b", "c", "d"]}))
        self.assertEqual(len(summary), 700)
        self.assertEqual(actions, ["a", "b", "c"])
        with self.assertRaises(LLMError):
            parse_reply(json.dumps({"summary": "ok", "actions": ["only one"]}))


class KeyHandlingTests(unittest.TestCase):
    def test_mask_and_repr(self):
        s = cfg.ExplainSettings(mode="api", base_url="https://api.example.com/v1", model="m", api_key=KEY)
        self.assertNotIn(KEY, repr(s))
        self.assertNotIn(KEY, str(s))
        self.assertNotIn(KEY, json.dumps(s.public()))
        self.assertEqual(s.public()["api_key_masked"], "sk-…CRET")
        self.assertEqual(cfg.mask("short"), "•" * 8)

    def test_save_is_private_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.local.json"
            s = cfg.ExplainSettings(mode="api", base_url="https://api.example.com/v1", model="m", api_key=KEY)
            cfg.save(s, path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded = cfg.load(path)
            self.assertEqual((loaded.mode, loaded.model, loaded.api_key), ("api", "m", KEY))

    def test_validate(self):
        self.assertIsNone(cfg.validate("local", "http://localhost:11434/v1", "llama3"))
        self.assertIsNotNone(cfg.validate("magic", "", ""))
        self.assertIsNotNone(cfg.validate("api", "ftp://x", "m"))

    def test_config_local_is_gitignored(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn("config.local.json", (root / ".gitignore").read_text().split())


if __name__ == "__main__":
    unittest.main()
