"""Real measurements on Linux: ping, dig, traceroute, curl -> one feature row.

Read-only: this module never changes network settings. When `netns` is set, every
probe runs as `ip netns exec <netns> <cmd>` (needs root), so it measures the
namespace's path and sees the namespace's /etc/resolv.conf.

Quick check on the OCI host (after scripts/netns_up.sh):
    sudo .venv/bin/python -m collector.linux --netns strata --save-raw data/raw/samples
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import cycle
from pathlib import Path
from urllib.parse import urlparse

from collector import parsers
from collector.features import FeatureRow, empty_row

REQUIRED_TOOLS = ["ping", "dig", "traceroute", "curl", "ip"]


@dataclass
class CollectorConfig:
    netns: str | None = None
    ext_target: str = "8.8.8.8"  # pinged and traced by IP, so DNS faults don't affect it
    dns_alt: str = "9.9.9.9"  # primary = whatever the (namespace) resolv.conf says
    dns_names: list[str] = field(
        default_factory=lambda: ["example.com", "wikipedia.org", "github.com", "cloudflare.com", "naver.com"]
    )
    throughput_url: str = "https://speed.cloudflare.com/__down?bytes=1000000"
    throughput_ip: str | None = None  # pinned so DNS faults don't break the download
    ping_count: int = 10
    ping_interval: float = 0.2
    trace_max_hops: int = 15
    curl_timeout: float = 10.0

    @classmethod
    def from_env(cls, netns: str | None = None) -> "CollectorConfig":
        cfg = cls(netns=netns if netns is not None else (os.getenv("STRATA_NETNS") or None))
        cfg.ext_target = os.getenv("PING_TARGET", cfg.ext_target)
        cfg.dns_alt = os.getenv("DNS_ALT", cfg.dns_alt)
        if os.getenv("DNS_QUERY_NAMES"):
            cfg.dns_names = [n.strip() for n in os.environ["DNS_QUERY_NAMES"].split(",") if n.strip()]
        cfg.throughput_url = os.getenv("THROUGHPUT_URL", cfg.throughput_url)
        cfg.throughput_ip = os.getenv("THROUGHPUT_IP") or None
        return cfg


def missing_tools() -> list[str]:
    return [t for t in REQUIRED_TOOLS if shutil.which(t) is None]


class LinuxCollector:
    def __init__(self, cfg: CollectorConfig, save_raw: Path | None = None) -> None:
        self.cfg = cfg
        self.save_raw = save_raw
        self._names = cycle(cfg.dns_names)
        self.gateway: str | None = None
        self.last_raw: dict[str, str] = {}

    # -- process helpers --------------------------------------------------------

    def _argv(self, cmd: list[str]) -> list[str]:
        return ["ip", "netns", "exec", self.cfg.netns, *cmd] if self.cfg.netns else cmd

    def run(self, cmd: list[str], timeout: float) -> str:
        """Run a probe and return stdout+stderr. Never raises on probe failure."""
        try:
            proc = subprocess.run(self._argv(cmd), capture_output=True, text=True, timeout=timeout)
            return proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            return out.decode() if isinstance(out, bytes) else out

    # -- setup ------------------------------------------------------------------

    def prepare(self) -> None:
        """Find the gateway and pin the throughput host's IP. Call before injecting faults."""
        self.gateway = parsers.parse_default_gateway(self.run(["ip", "route", "show", "default"], 5))
        if self.cfg.throughput_ip is None:
            host = urlparse(self.cfg.throughput_url).hostname or ""
            out = self.run(["dig", "+short", "+tries=2", "+time=2", f"@{self.cfg.dns_alt}", host, "A"], 10)
            ips = [line for line in out.split() if line.count(".") == 3 and line.replace(".", "").isdigit()]
            self.cfg.throughput_ip = ips[0] if ips else None

    # -- probes -----------------------------------------------------------------

    def _ping(self, target: str) -> str:
        c = self.cfg
        deadline = int(c.ping_count * c.ping_interval + 3)
        cmd = ["ping", "-n", "-c", str(c.ping_count), "-i", str(c.ping_interval), "-W", "1", "-w", str(deadline), target]
        return self.run(cmd, deadline + 3)

    def _dig(self, server: str | None, name: str) -> str:
        cmd = ["dig", "+tries=1", "+time=2"]
        if server:
            cmd.append(f"@{server}")
        return self.run([*cmd, name, "A"], 6)

    def _trace(self) -> str:
        cmd = ["traceroute", "-n", "-I", "-q", "1", "-w", "1", "-m", str(self.cfg.trace_max_hops), self.cfg.ext_target]
        return self.run(cmd, 20)

    def _curl(self) -> str:
        c = self.cfg
        cmd = ["curl", "-sS", "-o", "/dev/null", "--max-time", str(c.curl_timeout), "-w", parsers.CURL_WRITE_OUT]
        if c.throughput_ip:
            u = urlparse(c.throughput_url)
            port = u.port or (443 if u.scheme == "https" else 80)
            cmd += ["--resolve", f"{u.hostname}:{port}:{c.throughput_ip}"]
        return self.run([*cmd, c.throughput_url], c.curl_timeout + 5)

    # -- one round --------------------------------------------------------------

    def measure(self) -> FeatureRow:
        if self.gateway is None:
            self.prepare()
        name = next(self._names)
        jobs = {
            "ext_ping": lambda: self._ping(self.cfg.ext_target),
            "dns_primary": lambda: self._dig(None, name),
            "dns_alt": lambda: self._dig(self.cfg.dns_alt, name),
            "traceroute": self._trace,
        }
        if self.gateway:
            jobs["gw_ping"] = lambda: self._ping(self.gateway)
        # Latency probes run together; throughput runs alone afterwards so it can't queue them.
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {k: pool.submit(fn) for k, fn in jobs.items()}
            raw = {k: f.result() for k, f in futures.items()}
        raw["curl"] = self._curl()
        self.last_raw = raw
        if self.save_raw:
            self._dump(raw)
        return build_row(raw, self.cfg.ext_target)

    def _dump(self, raw: dict[str, str]) -> None:
        self.save_raw.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for key, text in raw.items():
            (self.save_raw / f"{stamp}_{key}.txt").write_text(text)


def build_row(raw: dict[str, str], ext_target: str | None = None) -> FeatureRow:
    """Turn raw tool outputs (keyed by probe name) into a feature row."""
    row = empty_row()

    for prefix, key in (("gw", "gw_ping"), ("ext", "ext_ping")):
        if key in raw:
            p = parsers.parse_ping(raw[key])
            row[f"{prefix}_rtt_ms"] = p.avg_ms
            row[f"{prefix}_jitter_ms"] = p.jitter_ms
            row[f"{prefix}_loss_pct"] = p.loss_pct

    for prefix, key in (("dns_primary", "dns_primary"), ("dns_alt", "dns_alt")):
        if key in raw:
            d = parsers.parse_dig(raw[key])
            row[f"{prefix}_ms"] = d.query_ms if d.ok else None
            row[f"{prefix}_ok"] = 1.0 if d.ok else 0.0

    if "traceroute" in raw:
        row.update(parsers.trace_features(parsers.parse_traceroute(raw["traceroute"], ext_target)))

    if "curl" in raw:
        c = parsers.parse_curl(raw["curl"])
        row["throughput_mbps"] = c.mbps if c.http_code == 200 else None

    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run real measurement rounds and print feature rows.")
    ap.add_argument("--netns", default=os.getenv("STRATA_NETNS", "strata"), help="namespace ('' = current)")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--save-raw", type=Path, help="directory to store raw tool output")
    args = ap.parse_args()

    if missing := missing_tools():
        raise SystemExit(f"missing tools: {', '.join(missing)} (see the setup commands)")
    col = LinuxCollector(CollectorConfig.from_env(args.netns or None), args.save_raw)
    col.prepare()
    print(f"gateway={col.gateway} throughput_ip={col.cfg.throughput_ip}")
    for _ in range(args.rounds):
        t0 = time.time()
        row = col.measure()
        print(json.dumps({"seconds": round(time.time() - t0, 2), **row}))


if __name__ == "__main__":
    main()
