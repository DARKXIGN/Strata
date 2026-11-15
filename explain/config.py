"""Explanation settings: mode, OpenAI-compatible base URL, API key, model name.

Sources, later wins: .env (EXPLAIN_MODE, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
LLM_TIMEOUT), then config.local.json written by the settings screen. Both files are
gitignored. The API key never leaves this process except in the Authorization header
of the LLM request: public() returns only a masked form, and scrub() removes it from
any text that might reach logs or the UI.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
LOCAL_FILE = ROOT / "config.local.json"
MODES = ("template", "local", "api")
DEFAULT_LOCAL_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint


@dataclass(frozen=True)
class ExplainSettings:
    mode: str = "template"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout: float = 3.0

    def __repr__(self) -> str:  # keep the key out of reprs, tracebacks and logs
        return (f"ExplainSettings(mode={self.mode!r}, base_url={self.base_url!r}, model={self.model!r}, "
                f"api_key={mask(self.api_key)!r}, timeout={self.timeout})")

    __str__ = __repr__

    def ready(self) -> tuple[bool, str]:
        """Whether an LLM call can be attempted, and why not."""
        if self.mode == "template":
            return False, "template mode"
        if not self.base_url:
            return False, "no base URL"
        if not self.model:
            return False, "no model name"
        if self.mode == "api" and not self.api_key:
            return False, "no API key"
        return True, ""

    def public(self) -> dict:
        return {
            "mode": self.mode,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_set": bool(self.api_key),
            "api_key_masked": mask(self.api_key),
            "timeout": self.timeout,
        }


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) < 12:
        return "•" * 8
    return f"{key[:3]}…{key[-4:]}"


def scrub(text: str, key: str) -> str:
    """Remove the key (and obvious bearer tokens) from text bound for logs or the UI."""
    if key:
        text = text.replace(key, mask(key))
    return text


def validate(mode: str, base_url: str, model: str) -> str | None:
    if mode not in MODES:
        return f"mode must be one of {', '.join(MODES)}"
    if base_url:
        u = urlparse(base_url)
        if u.scheme not in ("http", "https") or not u.netloc:
            return "base URL must start with http:// or https://"
    if len(model) > 200:
        return "model name is too long"
    return None


def _from_env() -> ExplainSettings:
    try:
        timeout = float(os.getenv("LLM_TIMEOUT", "3") or 3)
    except ValueError:
        timeout = 3.0
    mode = (os.getenv("EXPLAIN_MODE", "template") or "template").strip()
    return ExplainSettings(
        mode=mode if mode in MODES else "template",
        base_url=os.getenv("LLM_BASE_URL", "").strip(),
        model=os.getenv("LLM_MODEL", "").strip(),
        api_key=os.getenv("LLM_API_KEY", "").strip(),
        timeout=timeout,
    )


def load(path: Path = LOCAL_FILE) -> ExplainSettings:
    settings = _from_env()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            fields = {k: data[k] for k in ("mode", "base_url", "model", "api_key") if isinstance(data.get(k), str)}
            settings = replace(settings, **fields)
            if settings.mode not in MODES:
                settings = replace(settings, mode="template")
        except (OSError, ValueError):
            pass  # unreadable local file: keep .env values
    return settings


def save(settings: ExplainSettings, path: Path = LOCAL_FILE) -> None:
    data = {k: v for k, v in asdict(settings).items() if k != "timeout"}
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
