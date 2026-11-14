"""Explanation generation. Only the offline `template` mode exists for now."""

from __future__ import annotations

from collector.features import FeatureRow
from explain.templates import render


def explain(label: str, probs: dict[str, float], row: FeatureRow) -> dict:
    summary, actions = render(label, row)
    return {"mode": "template", "summary": summary, "actions": actions}
