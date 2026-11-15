"""Rule-based placeholder classifier. Replaced by LightGBM in a later step."""

from __future__ import annotations

import math

from collector.features import FeatureRow
from faults import LABELS


def _v(row: FeatureRow, key: str, default: float = 0.0) -> float:
    value = row.get(key)
    return default if value is None else float(value)


def predict_proba(row: FeatureRow) -> dict[str, float]:
    ext_rtt = _v(row, "ext_rtt_ms")
    ext_loss = _v(row, "ext_loss_pct")
    tput = _v(row, "throughput_mbps", 100.0)
    dns_ok = min(_v(row, "dns_primary_ok", 1.0), _v(row, "dns_alt_ok", 1.0))
    dns_ms = max(_v(row, "dns_primary_ms"), _v(row, "dns_alt_ms"))

    scores = {
        "normal": 2.0,
        "high_latency": (ext_rtt - 80) / 20,
        "packet_loss": (ext_loss - 2) / 2,
        "bandwidth_limit": (20 - tput) / 4,
        "dns_slow": (dns_ms - 300) / 100 if dns_ok else -5,
        "dns_failure": 6.0 if dns_ok == 0 and ext_loss < 10 else -5,
    }
    # Link-level latency also slows DNS; don't double count.
    if ext_rtt > 100:
        scores["dns_slow"] -= 4

    exp = {k: math.exp(max(-20.0, min(20.0, v))) for k, v in scores.items()}
    total = sum(exp.values())
    return {label: round(exp[label] / total, 4) for label in LABELS}


def predict(row: FeatureRow) -> tuple[str, dict[str, float]]:
    probs = predict_proba(row)
    return max(probs, key=probs.get), probs
