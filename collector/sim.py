"""Simulated measurements shaped by the currently active (simulated) fault."""

from __future__ import annotations

import random

from collector.features import FeatureRow, empty_row


def _noisy(base: float, spread: float) -> float:
    return max(0.0, random.gauss(base, spread))


def measure(fault: str | None, severity: float = 1.0) -> FeatureRow:
    """Return one fake feature row. `severity` is in [0.5, 1.5]."""
    s = severity
    row = empty_row()

    gw_rtt, gw_jit, gw_loss = _noisy(3, 1), _noisy(1, 0.4), 0.0
    ext_rtt, ext_jit, ext_loss = _noisy(28, 4), _noisy(3, 1), 0.0
    dns_p, dns_a = _noisy(22, 5), _noisy(25, 5)
    dns_p_ok = dns_a_ok = 1.0
    hops, first_hop, max_hop, jump = random.randint(9, 12), gw_rtt + _noisy(1, 0.5), ext_rtt + _noisy(5, 2), 0.0
    tput = _noisy(85, 10)
    wifi_sig, wifi_link = _noisy(85, 4), 573.5

    if fault == "high_latency":
        extra = 180 * s
        gw_rtt += extra
        ext_rtt += extra
        first_hop += extra
        max_hop += extra
        gw_jit += 5 * s
        ext_jit += 8 * s
        dns_p += extra
        dns_a += extra
        jump = 1.0
        tput *= 0.7
    elif fault == "packet_loss":
        gw_loss = min(100.0, _noisy(18 * s, 5))
        ext_loss = min(100.0, _noisy(20 * s, 5))
        ext_jit += 6 * s
        tput *= 0.4
        if random.random() < 0.2 * s:
            dns_p_ok = 0.0
    elif fault == "bandwidth_limit":
        tput = _noisy(2.5 / s, 0.5)
        ext_rtt += 15 * s
        ext_jit += 4 * s
    elif fault == "dns_slow":
        dns_p = _noisy(900 * s, 120)
        dns_a = _noisy(900 * s, 120)
    elif fault == "dns_failure":
        dns_p_ok = dns_a_ok = 0.0
        dns_p = dns_a = None
    elif fault == "weak_wifi":
        wifi_sig = _noisy(25 / s, 5)
        wifi_link = _noisy(40 / s, 8)
        gw_rtt += 25 * s
        gw_jit += 12 * s
        ext_rtt += 25 * s
        ext_jit += 12 * s
        gw_loss = _noisy(4 * s, 2)
        ext_loss = _noisy(5 * s, 2)
        tput *= 0.3

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
        trace_hops=float(hops),
        trace_first_hop_ms=first_hop,
        trace_max_hop_ms=max_hop,
        trace_jump_hop=jump,
        throughput_mbps=tput,
        wifi_signal_pct=min(100.0, wifi_sig),
        wifi_link_mbps=wifi_link,
    )
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
