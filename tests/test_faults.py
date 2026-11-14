"""Fault command construction and collection planning. No commands are executed."""

from __future__ import annotations

import random
import unittest

from faults import INJECTABLE
from faults.netns import inject_commands, netem_args, reset_commands, resolv_text, sample_params
from scripts.collect import plan

DEV = "strata-host"


class FaultCommandTests(unittest.TestCase):
    def test_every_command_targets_only_the_veth(self):
        rng = random.Random(0)
        for name in INJECTABLE:
            for _ in range(20):
                cmds = inject_commands(name, sample_params(name, rng), DEV) + reset_commands(DEV)
                for cmd in cmds:
                    self.assertEqual(cmd[0], "tc")
                    self.assertEqual(cmd[cmd.index("dev") + 1], DEV, cmd)
                    self.assertEqual(cmd.count("dev"), 1)

    def test_gateway_replies_excluded(self):
        cmds = inject_commands("high_latency", {"delay_ms": 200, "jitter_ms": 10}, DEV, "10.200.0.1")
        joined = [" ".join(c) for c in cmds]
        self.assertTrue(any("match ip src 10.200.0.1/32 flowid 1:1" in c for c in joined))
        self.assertTrue(any("netem delay 200ms 10ms limit 20000" in c for c in joined))
        self.assertTrue(any("match u32 0 0 flowid 1:3" in c for c in joined))

    def test_dns_slow_only_shapes_port_53(self):
        joined = [" ".join(c) for c in inject_commands("dns_slow", {"delay_ms": 800}, DEV)]
        self.assertTrue(any("match ip sport 53 0xffff flowid 1:3" in c for c in joined))
        self.assertFalse(any("match u32 0 0" in c for c in joined))

    def test_dns_failure_uses_no_tc(self):
        self.assertEqual(inject_commands("dns_failure", {}, DEV), [])
        self.assertIn("192.0.2.53", resolv_text("dns_failure"))
        self.assertNotIn("192.0.2.53", resolv_text(None))

    def test_netem_args(self):
        self.assertEqual(netem_args("packet_loss", {"loss_pct": 10}), ["loss", "10%", "limit", "10000"])
        self.assertEqual(netem_args("bandwidth_limit", {"rate_kbit": 1000}), ["rate", "1000kbit", "limit", "100"])

    def test_unknown_fault_rejected(self):
        with self.assertRaises(ValueError):
            inject_commands("weak_wifi", {}, DEV)


class PlanTests(unittest.TestCase):
    def test_sessions_cover_remaining_rows(self):
        tasks = plan({"normal": 45, "dns_slow": 20, "packet_loss": 0}, 20, random.Random(1))
        totals: dict[str, int] = {}
        for label, rows in tasks:
            self.assertLessEqual(rows, 20)
            totals[label] = totals.get(label, 0) + rows
        self.assertEqual(totals, {"normal": 45, "dns_slow": 20})
        self.assertEqual(len(tasks), 4)


if __name__ == "__main__":
    unittest.main()


class SafetyTests(unittest.TestCase):
    def test_namespace_resolvers(self):
        text = resolv_text(None)
        self.assertIn("nameserver 1.1.1.1", text)
        self.assertEqual(text.count("nameserver"), 1)  # matches how training data was collected
        self.assertNotIn("1.1.1.1", resolv_text("dns_failure"))

    def test_refuses_non_strata_devices(self):
        from faults.netns import NetnsFaultController
        for dev in ("enp0s6", "docker0", "veth28d58f8"):
            with self.assertRaises(RuntimeError):
                NetnsFaultController(dev=dev)
