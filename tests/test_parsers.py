"""Parser and row-building tests against captured-format fixtures. Run: python -m unittest -v"""

from __future__ import annotations

import unittest
from pathlib import Path

from collector import parsers
from collector.features import FEATURES
from collector.linux import build_row

FIX = Path(__file__).parent / "fixtures"


def fx(name: str) -> str:
    return (FIX / name).read_text()


class PingTests(unittest.TestCase):
    def test_ok(self):
        p = parsers.parse_ping(fx("ping_ok.txt"))
        self.assertEqual((p.sent, p.received, p.loss_pct), (5, 5, 0.0))
        self.assertAlmostEqual(p.avg_ms, 1.572)
        self.assertEqual(p.rtts, [1.62, 1.48, 1.55, 1.71, 1.50])
        # |1.48-1.62| + |1.55-1.48| + |1.71-1.55| + |1.50-1.71| = 0.58 over 4 gaps
        self.assertAlmostEqual(p.jitter_ms, 0.145)

    def test_loss_ignores_duplicates(self):
        p = parsers.parse_ping(fx("ping_loss.txt"))
        self.assertEqual((p.sent, p.received, p.loss_pct), (10, 7, 30.0))
        self.assertEqual(len(p.rtts), 7)
        self.assertAlmostEqual(p.avg_ms, 155.024)
        self.assertGreater(p.jitter_ms, 5)

    def test_unreachable(self):
        p = parsers.parse_ping(fx("ping_down.txt"))
        self.assertEqual(p.loss_pct, 100.0)
        self.assertIsNone(p.avg_ms)
        self.assertIsNone(p.jitter_ms)

    def test_empty_output(self):
        p = parsers.parse_ping("")
        self.assertEqual(p.loss_pct, 100.0)
        self.assertIsNone(p.avg_ms)


class DigTests(unittest.TestCase):
    def test_ok(self):
        d = parsers.parse_dig(fx("dig_ok.txt"))
        self.assertTrue(d.ok)
        self.assertEqual((d.status, d.query_ms, d.server), ("NOERROR", 4.0, "1.1.1.1"))

    def test_slow(self):
        d = parsers.parse_dig(fx("dig_slow.txt"))
        self.assertTrue(d.ok)
        self.assertEqual(d.query_ms, 1207.0)
        self.assertEqual(d.server, "9.9.9.9")

    def test_timeout(self):
        d = parsers.parse_dig(fx("dig_timeout.txt"))
        self.assertFalse(d.ok)
        self.assertIsNone(d.query_ms)

    def test_servfail_is_failure(self):
        d = parsers.parse_dig(fx("dig_servfail.txt"))
        self.assertFalse(d.ok)
        self.assertEqual(d.status, "SERVFAIL")


class TracerouteTests(unittest.TestCase):
    def test_ok(self):
        tr = parsers.parse_traceroute(fx("traceroute_ok.txt"), "8.8.8.8")
        self.assertTrue(tr.reached)
        self.assertEqual(tr.hop_count, 5)
        self.assertIsNone(tr.hops[1].rtt_ms)
        f = parsers.trace_features(tr)
        self.assertEqual(f["trace_hops"], 5.0)
        self.assertAlmostEqual(f["trace_first_hop_ms"], 0.061)
        self.assertAlmostEqual(f["trace_max_hop_ms"], 1.544)
        self.assertEqual(f["trace_jump_hop"], 0.0)

    def test_delay_jump_multi_probe(self):
        tr = parsers.parse_traceroute(fx("traceroute_delay.txt"), "8.8.8.8")
        self.assertEqual(tr.hops[3].addr, "72.14.204.18")
        self.assertAlmostEqual(tr.hops[3].rtt_ms, (203.900 + 204.100 + 202.800) / 3)
        self.assertAlmostEqual(tr.hops[2].rtt_ms, (202.020 + 201.511) / 2)
        self.assertEqual(tr.hops[4].addr, "8.8.8.8")  # "!H" annotation skipped
        f = parsers.trace_features(tr)
        self.assertEqual(f["trace_jump_hop"], 2.0)
        self.assertGreater(f["trace_max_hop_ms"], 200)

    def test_unreached(self):
        tr = parsers.parse_traceroute(fx("traceroute_unreached.txt"), "8.8.8.8")
        self.assertFalse(tr.reached)
        f = parsers.trace_features(tr)
        self.assertEqual(f["trace_hops"], 6.0)
        self.assertAlmostEqual(f["trace_max_hop_ms"], 0.072)
        self.assertEqual(f["trace_jump_hop"], 0.0)

    def test_empty(self):
        f = parsers.trace_features(parsers.parse_traceroute(""))
        self.assertTrue(all(v is None for v in f.values()))


class CurlTests(unittest.TestCase):
    def test_ok(self):
        c = parsers.parse_curl(fx("curl_ok.txt"))
        self.assertEqual(c.http_code, 200)
        self.assertAlmostEqual(c.mbps, 1_000_000 * 8 / 0.183402 / 1e6)

    def test_partial_download_still_measures(self):
        c = parsers.parse_curl("curl: (28) Operation timed out\n" + fx("curl_timeout_partial.txt"))
        self.assertAlmostEqual(c.mbps, 262144 * 8 / 10.001233 / 1e6)

    def test_failure(self):
        self.assertIsNone(parsers.parse_curl(fx("curl_fail.txt")).mbps)
        self.assertIsNone(parsers.parse_curl("").mbps)


class RouteTests(unittest.TestCase):
    def test_gateway(self):
        self.assertEqual(parsers.parse_default_gateway(fx("ip_route.txt")), "10.200.0.1")
        self.assertIsNone(parsers.parse_default_gateway("10.0.0.0/24 dev eth0"))


class BuildRowTests(unittest.TestCase):
    def test_dns_failure_shape(self):
        row = build_row({
            "gw_ping": fx("ping_ok.txt"),
            "ext_ping": fx("ping_ok.txt"),
            "dns_primary": fx("dig_timeout.txt"),
            "dns_alt": fx("dig_ok.txt"),
            "traceroute": fx("traceroute_ok.txt"),
            "curl": fx("curl_ok.txt"),
        }, "8.8.8.8")
        self.assertEqual(set(row), set(FEATURES))
        self.assertEqual(row["dns_primary_ok"], 0.0)
        self.assertIsNone(row["dns_primary_ms"])
        self.assertEqual(row["dns_alt_ok"], 1.0)
        self.assertEqual(row["dns_alt_ms"], 4.0)
        self.assertEqual(row["ext_loss_pct"], 0.0)
        self.assertGreater(row["throughput_mbps"], 40)
        self.assertIsNone(row["wifi_signal_pct"])

    def test_latency_shape(self):
        row = build_row({
            "gw_ping": fx("ping_ok.txt"),
            "ext_ping": fx("ping_loss.txt"),
            "traceroute": fx("traceroute_delay.txt"),
            "curl": fx("curl_fail.txt"),
        })
        self.assertLess(row["gw_rtt_ms"], 5)
        self.assertGreater(row["ext_rtt_ms"], 100)
        self.assertEqual(row["trace_jump_hop"], 2.0)
        self.assertIsNone(row["throughput_mbps"])
        self.assertIsNone(row["dns_primary_ok"])  # probe missing -> unknown, not failed

    def test_without_traceroute(self):
        row = build_row({"ext_ping": fx("ping_ok.txt"), "traceroute": ""})
        self.assertIsNone(row["trace_hops"])
        self.assertIsNone(row["trace_jump_hop"])


class MissingToolTests(unittest.TestCase):
    def test_round_survives_missing_binaries(self):
        from collector.linux import CollectorConfig, LinuxCollector
        col = LinuxCollector(CollectorConfig(netns=None, throughput_ip="192.0.2.1"))
        col.gateway = "192.0.2.254"
        col.has_traceroute = False
        col._argv = lambda cmd: ["/nonexistent/" + cmd[0], *cmd[1:]]
        row = col.measure()
        self.assertEqual(set(row), set(FEATURES))
        self.assertIsNone(row["trace_hops"])
        self.assertEqual(row["ext_loss_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()


class RealCaptureTests(unittest.TestCase):
    """Outputs captured on the OCI host inside the strata namespace (Nov 2026)."""

    def test_round_matches_logged_row(self):
        row = build_row({
            "ext_ping": fx("oci_ping_ext.txt"),
            "dns_alt": fx("oci_dig_alt.txt"),
            "traceroute": fx("oci_traceroute.txt"),
            "curl": fx("oci_curl.txt"),
        }, "8.8.8.8")
        # Values the collector printed for this same round on the host.
        self.assertEqual(row["ext_rtt_ms"], 33.544)
        self.assertEqual(row["ext_loss_pct"], 0.0)
        self.assertEqual(row["dns_alt_ms"], 3.0)
        self.assertEqual(row["trace_hops"], 7.0)
        self.assertEqual(row["trace_first_hop_ms"], 0.033)
        self.assertEqual(row["trace_max_hop_ms"], 36.148)
        self.assertEqual(row["trace_jump_hop"], 4.0)
        self.assertEqual(row["throughput_mbps"], 112.293)
