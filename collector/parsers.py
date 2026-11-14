"""Parsers for Linux tool output: iputils ping, BIND dig, traceroute, curl -w.

Pure functions: text in, numbers out. Tested against fixtures in tests/fixtures/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PingResult:
    sent: int = 0
    received: int = 0
    loss_pct: float = 100.0
    avg_ms: float | None = None
    jitter_ms: float | None = None
    rtts: list[float] = field(default_factory=list)


@dataclass
class DigResult:
    ok: bool = False
    status: str | None = None
    query_ms: float | None = None
    server: str | None = None


@dataclass
class Hop:
    number: int
    addr: str | None
    rtt_ms: float | None  # mean of the probes that answered


@dataclass
class TraceResult:
    hops: list[Hop] = field(default_factory=list)
    reached: bool = False

    @property
    def hop_count(self) -> int:
        return self.hops[-1].number if self.hops else 0


@dataclass
class CurlResult:
    http_code: int = 0
    bytes: float = 0.0
    seconds: float = 0.0

    @property
    def mbps(self) -> float | None:
        if self.bytes <= 0 or self.seconds <= 0:
            return None
        return self.bytes * 8 / self.seconds / 1e6


# --- ping -------------------------------------------------------------------

_PING_REPLY = re.compile(r"icmp_seq=(\d+).*?time[=<]([\d.]+)\s*ms")
_PING_STATS = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received.*?([\d.]+)% packet loss")
_PING_RTT = re.compile(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")


def jitter(rtts: list[float]) -> float | None:
    """Mean absolute difference between consecutive RTTs (RFC 3550 style, unsmoothed)."""
    if len(rtts) < 2:
        return 0.0 if rtts else None
    diffs = [abs(b - a) for a, b in zip(rtts, rtts[1:])]
    return sum(diffs) / len(diffs)


def parse_ping(text: str) -> PingResult:
    res = PingResult()
    seen: dict[int, float] = {}
    for m in _PING_REPLY.finditer(text):
        seen.setdefault(int(m.group(1)), float(m.group(2)))  # ignore DUP! replies
    res.rtts = [seen[k] for k in sorted(seen)]

    stats = _PING_STATS.search(text)
    if stats:
        res.sent = int(stats.group(1))
        res.received = int(stats.group(2))
        res.loss_pct = float(stats.group(3))
    elif res.rtts:
        res.sent = res.received = len(res.rtts)
        res.loss_pct = 0.0

    rtt = _PING_RTT.search(text)
    if rtt:
        res.avg_ms = float(rtt.group(2))
    elif res.rtts:
        res.avg_ms = sum(res.rtts) / len(res.rtts)

    if res.received == 0:
        res.avg_ms = None
        res.jitter_ms = None
    else:
        res.jitter_ms = jitter(res.rtts)
        if res.jitter_ms is None and rtt:
            res.jitter_ms = float(rtt.group(4))
    return res


# --- dig --------------------------------------------------------------------

_DIG_STATUS = re.compile(r"->>HEADER<<-.*?status: ([A-Z]+)")
_DIG_TIME = re.compile(r";; Query time: (\d+) msec")
_DIG_SERVER = re.compile(r";; SERVER: ([^#\s]+)#")


def parse_dig(text: str) -> DigResult:
    res = DigResult()
    status = _DIG_STATUS.search(text)
    if not status:
        # timeout, "no servers could be reached", connection refused, ...
        return res
    res.status = status.group(1)
    res.ok = res.status == "NOERROR"
    qt = _DIG_TIME.search(text)
    if qt:
        res.query_ms = float(qt.group(1))
    sv = _DIG_SERVER.search(text)
    if sv:
        res.server = sv.group(1)
    return res


# --- traceroute ---------------------------------------------------------------

_HOP_LINE = re.compile(r"^\s*(\d+)\s+(.*)$")
_TOKEN = re.compile(r"\*|!\S*|[\d.]+\s*ms|[0-9a-fA-F:.]+")


def parse_traceroute(text: str, target: str | None = None) -> TraceResult:
    """Parse `traceroute -n` output. Handles `*`, multiple probes and multiple addresses per hop."""
    res = TraceResult()
    for line in text.splitlines():
        if line.startswith("traceroute"):
            continue
        m = _HOP_LINE.match(line)
        if not m:
            continue
        number, rest = int(m.group(1)), m.group(2)
        addr: str | None = None
        times: list[float] = []
        for tok in _TOKEN.findall(rest):
            if tok.endswith("ms"):
                times.append(float(tok[:-2].strip()))
            elif tok == "*" or tok.startswith("!"):
                continue
            elif addr is None:
                addr = tok
        rtt = sum(times) / len(times) if times else None
        res.hops.append(Hop(number=number, addr=addr, rtt_ms=rtt))
    if target and res.hops:
        res.reached = any(h.addr == target for h in res.hops[-1:])
    return res


def trace_features(tr: TraceResult, jump_threshold_ms: float = 20.0) -> dict[str, float | None]:
    """hop count, first hop delay, max hop delay, and the hop where delay jumps (0 = no jump)."""
    answered = [h for h in tr.hops if h.rtt_ms is not None]
    first = tr.hops[0].rtt_ms if tr.hops else None
    max_ms = max((h.rtt_ms for h in answered), default=None)
    jump_hop, best = 0, jump_threshold_ms
    for prev, cur in zip(answered, answered[1:]):
        delta = cur.rtt_ms - prev.rtt_ms
        if delta >= best:
            jump_hop, best = cur.number, delta
    return {
        "trace_hops": float(tr.hop_count) if tr.hops else None,
        "trace_first_hop_ms": first,
        "trace_max_hop_ms": max_ms,
        "trace_jump_hop": float(jump_hop) if tr.hops else None,
    }


# --- curl -------------------------------------------------------------------

CURL_WRITE_OUT = "%{http_code} %{size_download} %{time_total}\\n"


def parse_curl(text: str) -> CurlResult:
    """Parse the last line written by `curl -w CURL_WRITE_OUT`."""
    for line in reversed(text.strip().splitlines()):
        parts = line.split()
        if len(parts) == 3:
            try:
                return CurlResult(int(parts[0]), float(parts[1]), float(parts[2]))
            except ValueError:
                continue
    return CurlResult()


# --- ip route -----------------------------------------------------------------

_DEFAULT_ROUTE = re.compile(r"^default via (\S+)", re.M)


def parse_default_gateway(text: str) -> str | None:
    m = _DEFAULT_ROUTE.search(text)
    return m.group(1) if m else None
