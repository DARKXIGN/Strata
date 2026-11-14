"""Model input: the measured features plus comparison features, as a float vector.

Comparisons carry most of the signal (CLAUDE.md): "gateway fine but internet slow",
"IP works but name lookup fails". Missing values become NaN, which LightGBM handles.
Wi-Fi columns are dropped: the OCI host has no Wi-Fi.
"""

from __future__ import annotations

import math
from typing import Mapping

BASE = [
    "gw_rtt_ms",
    "gw_jitter_ms",
    "gw_loss_pct",
    "ext_rtt_ms",
    "ext_jitter_ms",
    "ext_loss_pct",
    "dns_primary_ms",
    "dns_primary_ok",
    "dns_alt_ms",
    "dns_alt_ok",
    "trace_hops",
    "trace_first_hop_ms",
    "trace_max_hop_ms",
    "trace_jump_hop",
    "throughput_mbps",
]

DERIVED = [
    "ext_minus_gw_ms",  # delay added beyond the gateway
    "ext_loss_minus_gw_loss",  # loss added beyond the gateway
    "dns_max_ms",  # slowest successful lookup
    "dns_over_rtt_ms",  # lookup time beyond the network round trip
    "dns_fail_count",  # 0, 1 or 2 resolvers failed
    "dns_primary_only_fail",  # primary failed while the alternate answered
]

MODEL_FEATURES = BASE + DERIVED


def _num(value) -> float:
    if value is None or value == "" or value == "None":
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _sub(a: float, b: float) -> float:
    return a - b if not (math.isnan(a) or math.isnan(b)) else math.nan


def vectorize(row: Mapping[str, object]) -> list[float]:
    """Feature row (dict from the collector or a CSV row) -> list ordered as MODEL_FEATURES."""
    v = {k: _num(row.get(k)) for k in BASE}

    dns_ms = [x for x in (v["dns_primary_ms"], v["dns_alt_ms"]) if not math.isnan(x)]
    p_ok, a_ok = v["dns_primary_ok"], v["dns_alt_ok"]
    fails = sum(1 for ok in (p_ok, a_ok) if ok == 0)

    d = {
        "ext_minus_gw_ms": _sub(v["ext_rtt_ms"], v["gw_rtt_ms"]),
        "ext_loss_minus_gw_loss": _sub(v["ext_loss_pct"], v["gw_loss_pct"]),
        "dns_max_ms": max(dns_ms) if dns_ms else math.nan,
        "dns_over_rtt_ms": _sub(min(dns_ms), v["ext_rtt_ms"]) if dns_ms else math.nan,
        "dns_fail_count": float(fails) if not (math.isnan(p_ok) and math.isnan(a_ok)) else math.nan,
        "dns_primary_only_fail": 1.0 if (p_ok == 0 and a_ok == 1) else (math.nan if math.isnan(p_ok) else 0.0),
    }
    return [v[k] for k in BASE] + [d[k] for k in DERIVED]
