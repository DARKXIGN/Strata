"""Feature schema: one row per measurement round. Missing values are None."""

from __future__ import annotations

FEATURES: list[str] = [
    # ping to default gateway
    "gw_rtt_ms",
    "gw_jitter_ms",
    "gw_loss_pct",
    # ping to external host
    "ext_rtt_ms",
    "ext_jitter_ms",
    "ext_loss_pct",
    # DNS lookups against two resolvers
    "dns_primary_ms",
    "dns_primary_ok",
    "dns_alt_ms",
    "dns_alt_ok",
    # traceroute
    "trace_hops",
    "trace_first_hop_ms",
    "trace_max_hop_ms",
    "trace_jump_hop",
    # throughput
    "throughput_mbps",
    # wifi
    "wifi_signal_pct",
    "wifi_link_mbps",
]

FeatureRow = dict[str, float | None]


def empty_row() -> FeatureRow:
    return {name: None for name in FEATURES}
