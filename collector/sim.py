"""Simulated measurements shaped by the currently active (simulated) fault.

Baselines and fault effects mirror the rows collected on the OCI host
(data/raw/main.csv, Nov 2026), so the model trained on real data also reads sim
rows sensibly on the laptop. Sim rows are never used for training or reported accuracy.
"""

from __future__ import annotations

import random

from collector.features import FeatureRow, empty_row


def _noisy(base: float, spread: float) -> float:
    return max(0.0, random.gauss(base, spread))


def _ping_loss(true_pct: float, count: int = 10) -> float:
    """Loss as 10 pings would measure it: a binomial draw, so low loss often reads 0%."""
    lost = sum(random.random() < true_pct / 100 for _ in range(count))
    return 100.0 * lost / count


def measure(fault: str | None, severity: float = 1.0) -> FeatureRow:
    """Return one fake feature row. `severity` is roughly in [0.7, 1.3]."""
    s = severity
    row = empty_row()

    gw_rtt, gw_jit, gw_loss = _noisy(0.044, 0.005), _noisy(0.01, 0.003), 0.0
    ext_rtt, ext_jit, ext_loss = _noisy(33.54, 0.02), _noisy(0.04, 0.02), 0.0
    dns_p, dns_a = float(random.choice([3, 4, 4])), float(random.choice([3, 3, 4]))
    dns_p_ok = dns_a_ok = 1.0
    hops, first_hop, jump = 7.0, random.choice([0.03, 0.04, 0.04, 0.8]), 4.0
    max_hop = _noisy(36.2, 0.2)
    tput = random.triangular(97, 118, 110)  # real normal: p10 96, median 109, p90 116

    if fault == "high_latency":
        # Injected past the gateway: gateway and first hop stay clean, jump at hop 2.
        extra = 150 * s
        jit = extra * 0.06
        ext_rtt += _noisy(extra, jit / 2)
        ext_jit = _noisy(jit, jit / 3)
        dns_p += extra + random.gauss(0, jit)
        dns_a += extra + random.gauss(0, jit)
        max_hop = ext_rtt + _noisy(5, 2)
        jump = 2.0
        tput = _noisy(1300 / ext_rtt, 1.5)  # TCP throughput falls with RTT
    elif fault == "packet_loss":
        true_loss = 12 * s
        ext_loss = _ping_loss(true_loss)
        tput = _noisy(max(5.0, 107 - 4.5 * true_loss), 20)
        if random.random() < true_loss / 80:
            dns_p_ok, dns_p = 0.0, None
        if random.random() < true_loss / 80:
            dns_a_ok, dns_a = 0.0, None
    elif fault == "bandwidth_limit":
        tput = _noisy(2.4 / s, 0.6)
    elif fault == "dns_slow":
        delay = 650 * s
        dns_p = _noisy(delay, delay * 0.1)
        dns_a = _noisy(delay, delay * 0.1)
    elif fault == "dns_failure":
        # Only the configured (primary) resolver is broken; the alternate still answers.
        dns_p_ok = 0.0
        dns_p = None

    row.update(
        gw_rtt_ms=gw_rtt,
        gw_jitter_ms=gw_jit,
        gw_loss_pct=gw_loss,
        ext_rtt_ms=ext_rtt,
        ext_jitter_ms=ext_jit,
        ext_loss_pct=ext_loss,
        dns_primary_ms=dns_p,
        dns_primary_ok=dns_p_ok,
        dns_alt_ms=dns_a,
        dns_alt_ok=dns_a_ok,
        trace_hops=hops,
        trace_first_hop_ms=first_hop,
        trace_max_hop_ms=max_hop,
        trace_jump_hop=jump,
        throughput_mbps=tput,
        # Wi-Fi is not measured on the OCI host; keep sim rows shaped like real ones.
        wifi_signal_pct=None,
        wifi_link_mbps=None,
    )
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}
