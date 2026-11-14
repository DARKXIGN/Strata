"""Explanation service: template text immediately, LLM text once it arrives.

The measurement loop must never wait on an LLM. For a new diagnosis the service
returns the template right away and starts one background call; later ticks with the
same diagnosis reuse the LLM result. On failure or timeout the template stays and the
reason is reported; the call is retried after RETRY_SECONDS.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from collector.features import FeatureRow
from explain import config as cfg
from explain.llm import LLMError, explain_with_llm
from explain.templates import render

log = logging.getLogger("strata.explain")

REFRESH_SECONDS = 120  # re-explain a long-lasting diagnosis with fresh numbers
RETRY_SECONDS = 30


@dataclass
class _Entry:
    label: str
    at: float
    summary: str | None = None
    actions: list[str] | None = None
    error: str | None = None


class ExplainService:
    def __init__(self, settings: cfg.ExplainSettings | None = None) -> None:
        self._lock = threading.Lock()
        self._settings = settings or cfg.load()
        self._entry: _Entry | None = None
        self._inflight: str | None = None
        self._generation = 0  # bumps on settings change so stale replies are dropped

    @property
    def settings(self) -> cfg.ExplainSettings:
        return self._settings

    def update(self, settings: cfg.ExplainSettings) -> None:
        with self._lock:
            self._settings = settings
            self._entry = None
            self._inflight = None
            self._generation += 1

    def explain(self, label: str, probs: dict[str, float], row: FeatureRow, layer: str | None) -> dict:
        summary, actions = render(label, row)
        s = self._settings
        result = {"mode": "template", "requested": s.mode, "summary": summary, "actions": actions,
                  "pending": False, "fallback": None}
        ok, why = s.ready()
        if s.mode == "template":
            return result
        if not ok:
            result["fallback"] = why
            return result

        now = time.time()
        with self._lock:
            entry = self._entry if self._entry and self._entry.label == label else None
            if entry and entry.summary and now - entry.at < REFRESH_SECONDS:
                result.update(mode=s.mode, summary=entry.summary, actions=entry.actions)
                return result
            if entry and entry.error and now - entry.at < RETRY_SECONDS:
                result["fallback"] = entry.error
                return result
            if entry and entry.summary:  # stale but usable while a refresh runs
                result.update(mode=s.mode, summary=entry.summary, actions=entry.actions)
            if self._inflight != label:
                self._inflight = label
                gen = self._generation
                threading.Thread(target=self._call, args=(gen, s, label, dict(probs), dict(row), layer),
                                 daemon=True).start()
            result["pending"] = True
        return result

    def _call(self, gen: int, s: cfg.ExplainSettings, label: str, probs: dict, row: dict, layer: str | None) -> None:
        try:
            summary, actions = explain_with_llm(s, label, probs, row, layer)
            entry = _Entry(label, time.time(), summary=summary, actions=actions)
        except LLMError as exc:
            entry = _Entry(label, time.time(), error=str(exc))
            log.warning("LLM explanation failed, using template: %s", exc)
        except Exception as exc:  # never let a bug here reach the tick loop
            entry = _Entry(label, time.time(), error="unexpected error")
            log.warning("LLM explanation crashed: %s", cfg.scrub(repr(exc), s.api_key))
        with self._lock:
            if gen == self._generation:
                self._entry = entry
                if self._inflight == label:
                    self._inflight = None

    def test(self, settings: cfg.ExplainSettings) -> dict:
        """One synchronous call with sample data, for the settings screen."""
        ok, why = settings.ready()
        if not ok:
            return {"ok": False, "error": why}
        sample = {"gw_rtt_ms": 0.04, "ext_rtt_ms": 182.0, "ext_jitter_ms": 9.0, "ext_loss_pct": 0.0,
                  "dns_primary_ms": 150.0, "dns_primary_ok": 1.0, "dns_alt_ms": 152.0, "dns_alt_ok": 1.0,
                  "trace_jump_hop": 2.0, "throughput_mbps": 8.0}
        started = time.monotonic()
        try:
            summary, actions = explain_with_llm(settings, "high_latency", {"high_latency": 0.97, "normal": 0.02},
                                                sample, "isp")
        except LLMError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": cfg.scrub(f"unexpected error: {exc}", settings.api_key)}
        return {"ok": True, "seconds": round(time.monotonic() - started, 2), "summary": summary, "actions": actions}
