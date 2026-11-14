"""OpenAI-compatible chat call that rewrites a diagnosis in plain words.

The classifier has already decided the cause; the LLM only explains it. Works with
any server that implements POST {base_url}/chat/completions (OpenAI, Ollama, vLLM,
LM Studio, ...). Standard library only.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from collector.features import FeatureRow
from explain.config import ExplainSettings, scrub

LABEL_TEXT = {
    "normal": "no fault (normal)",
    "high_latency": "high latency on the link",
    "packet_loss": "packet loss on the link",
    "bandwidth_limit": "bandwidth limit on the link",
    "dns_slow": "slow DNS resolver",
    "dns_failure": "DNS resolver failure",
}

KEY_MEASUREMENTS = [
    ("gw_rtt_ms", "gateway round trip, ms"),
    ("gw_loss_pct", "gateway packet loss, %"),
    ("ext_rtt_ms", "internet round trip, ms"),
    ("ext_jitter_ms", "internet jitter, ms"),
    ("ext_loss_pct", "internet packet loss, %"),
    ("dns_primary_ms", "configured DNS lookup, ms"),
    ("dns_primary_ok", "configured DNS answered (1 yes, 0 no)"),
    ("dns_alt_ms", "alternate DNS lookup, ms"),
    ("dns_alt_ok", "alternate DNS answered (1 yes, 0 no)"),
    ("trace_jump_hop", "hop where delay jumps (0 = none)"),
    ("throughput_mbps", "download speed, Mbps"),
]

SYSTEM_PROMPT = (
    "You explain a home or office network diagnosis to a non-expert. "
    "A trained classifier has already determined the cause; state that cause as given and "
    "never replace or second-guess it. Support it with the measurements provided, quoting a "
    "number or two. Write in plain English without jargon. "
    'Reply with only a JSON object: {"summary": "2 or 3 sentences", '
    '"actions": ["2 or 3 short, concrete actions the user can take"]}.'
)


class LLMError(Exception):
    """Raised with a message that is safe to show (never contains the key)."""


def build_messages(label: str, probs: dict[str, float], row: FeatureRow, layer: str | None) -> list[dict]:
    top = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:3]
    facts = {
        "diagnosis": LABEL_TEXT.get(label, label),
        "affected_layer": layer or "none",
        "confidence": {LABEL_TEXT.get(k, k): round(v, 2) for k, v in top},
        "measurements": {
            name: round(row[key], 1) for key, name in KEY_MEASUREMENTS if row.get(key) is not None
        },
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(facts)},
    ]


def parse_reply(text: str) -> tuple[str, list[str]]:
    """Pull {"summary", "actions"} out of the model reply, tolerating code fences and chatter."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise LLMError("reply was not JSON")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise LLMError("reply was not valid JSON") from exc
    summary = data.get("summary")
    actions = data.get("actions")
    if not isinstance(summary, str) or not summary.strip():
        raise LLMError("reply had no summary")
    if not isinstance(actions, list):
        raise LLMError("reply had no actions")
    actions = [a.strip()[:200] for a in actions if isinstance(a, str) and a.strip()][:3]
    if len(actions) < 2:
        raise LLMError("reply had fewer than 2 actions")
    return summary.strip()[:700], actions


def chat(settings: ExplainSettings, messages: list[dict]) -> str:
    """POST to {base_url}/chat/completions and return the reply text."""
    url = settings.base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": settings.model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 300,
        "stream": False,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=settings.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise LLMError(f"HTTP {exc.code} from the LLM server") from None
    except (TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            raise LLMError(f"timed out after {settings.timeout:g} s") from None
        raise LLMError(scrub(f"could not reach the LLM server ({reason})", settings.api_key)) from None
    except ValueError:
        raise LLMError("LLM server returned non-JSON") from None
    if time.monotonic() - started > settings.timeout:
        raise LLMError(f"timed out after {settings.timeout:g} s")
    try:
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected response shape (no choices[0].message.content)") from None


def explain_with_llm(settings: ExplainSettings, label: str, probs: dict[str, float], row: FeatureRow,
                     layer: str | None) -> tuple[str, list[str]]:
    return parse_reply(chat(settings, build_messages(label, probs, row, layer)))
