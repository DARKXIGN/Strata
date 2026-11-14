"""Fault classes and the network layer each one belongs to."""

from __future__ import annotations

LABELS: list[str] = [
    "normal",
    "high_latency",
    "packet_loss",
    "bandwidth_limit",
    "dns_slow",
    "dns_failure",
]

# Strata layers, top (closest to user) to bottom. "wifi" is shown but never diagnosed
# (the OCI test host has no Wi-Fi).
LAYERS: list[str] = ["device", "wifi", "router", "isp", "dns", "server"]

# Layer flagged on the dashboard for each label. Link faults show on the ISP/link layer.
LABEL_LAYER: dict[str, str | None] = {
    "normal": None,
    "high_latency": "isp",
    "packet_loss": "isp",
    "bandwidth_limit": "isp",
    "dns_slow": "dns",
    "dns_failure": "dns",
}

INJECTABLE: list[str] = [label for label in LABELS if label != "normal"]
