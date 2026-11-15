"""Storage settings, read from .env (loaded by config.py) and nowhere else.

    SUPABASE_URL          https://<project-ref>.supabase.co
    SUPABASE_SECRET_KEY   sb_secret_...   (new-style secret key; maps to service_role)

If either is missing or invalid, uploading is silently off and the server runs exactly as
before; diagnoses are still kept in the local SQLite history so the dashboard cards work.
Legacy JWT keys (anon / service_role, "eyJ...") and publishable keys are rejected.

The key is never logged, returned by an API, or put in an error message. Where a key has
to be identified, only a short SHA-256 fingerprint is shown (Supabase's own guidance).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "strata.sqlite"


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8] if key else ""


@dataclass(frozen=True)
class StorageConfig:
    url: str = ""
    key: str = field(default="", repr=False)
    agent_id: str = "strata"
    agent_name: str = "strata"
    region: str = "default"
    db_path: Path = DEFAULT_DB
    record_seconds: float = 10.0  # one row per agent per this many seconds (plus label changes)
    flush_seconds: float = 5.0
    batch_size: int = 200
    upload_enabled: bool = False
    reason: str = "SUPABASE_URL / SUPABASE_SECRET_KEY not set"

    def __repr__(self) -> str:  # the key never appears in reprs or tracebacks
        return (f"StorageConfig(url_host={urlparse(self.url).hostname!r}, key_fp={fingerprint(self.key)!r}, "
                f"agent_id={self.agent_id!r}, region={self.region!r}, upload_enabled={self.upload_enabled})")

    __str__ = __repr__

    def public(self) -> dict:
        return {
            "upload_enabled": self.upload_enabled,
            "reason": self.reason,
            "supabase_host": urlparse(self.url).hostname if self.upload_enabled else None,
            "key_fingerprint": fingerprint(self.key) if self.upload_enabled else None,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "region": self.region,
            "record_seconds": self.record_seconds,
        }


def check_key(key: str) -> str | None:
    """Why this key can't be used for uploads, or None if it looks like a secret key."""
    if not key:
        return "SUPABASE_SECRET_KEY not set"
    if key.startswith("eyJ"):
        return "legacy JWT key (anon/service_role) is not supported; create an sb_secret_ key"
    if key.startswith("sb_publishable_"):
        return "publishable key cannot write; use an sb_secret_ key"
    if not key.startswith("sb_secret_") or len(key) < 20:
        return "SUPABASE_SECRET_KEY must look like sb_secret_..."
    return None


def check_url(url: str) -> str | None:
    if not url:
        return "SUPABASE_URL not set"
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return "SUPABASE_URL must be an https:// URL"  # a secret key never travels over plain HTTP
    return None


def load(env: dict[str, str] | None = None) -> StorageConfig:
    e = os.environ if env is None else env
    netns = e.get("STRATA_NETNS", "strata") or "strata"
    agent_id = (e.get("STRATA_AGENT_ID") or netns).strip()
    url = (e.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (e.get("SUPABASE_SECRET_KEY") or "").strip()
    problem = None if not (url or key) else (check_url(url) or check_key(key))
    enabled = bool(url and key) and problem is None
    if not (url or key):
        problem = "SUPABASE_URL / SUPABASE_SECRET_KEY not set"
    if not key and e.get("SUPABASE_SERVICE_KEY"):
        problem = "SUPABASE_SERVICE_KEY is not used; set SUPABASE_SECRET_KEY (sb_secret_...) instead"
    return StorageConfig(
        url=url if enabled else "",
        key=key if enabled else "",  # an unusable key is dropped right away
        agent_id=agent_id,
        agent_name=(e.get("STRATA_AGENT_NAME") or agent_id).strip(),
        region=(e.get("STRATA_REGION") or "default").strip(),
        db_path=Path(e.get("STRATA_DB_PATH") or DEFAULT_DB),
        record_seconds=max(1.0, _float_env(e, "STRATA_RECORD_SECONDS", 10.0)),
        upload_enabled=enabled,
        reason="ok" if enabled else (problem or "disabled"),
    )


def _float_env(e, name: str, default: float) -> float:
    try:
        return float(e.get(name) or default)
    except ValueError:
        return default
