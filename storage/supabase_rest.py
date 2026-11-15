"""Minimal Supabase (PostgREST) client over the standard library.

Why not supabase-py: version 2.34.1 accepts the new non-JWT keys but sends the key as
`Authorization: Bearer` as well as `apikey`; Supabase asks for new keys on `apikey` only,
since they are not JWTs. It would also add httpx, realtime, storage3 and auth packages for
four REST calls. Here the secret key goes only in the `apikey` header, only over HTTPS,
only to SUPABASE_URL (redirects are not followed, so the key can't be forwarded elsewhere).

Runs in the server process, i.e. the host network namespace, never inside a fault
namespace: probes alone are wrapped in `ip netns exec`, uploads are plain sockets here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

from storage.config import fingerprint

USER_AGENT = "strata-agent/1.0 (server; python-urllib)"  # Supabase rejects browser user agents


class StorageError(Exception):
    """Message is safe to log and show: never contains the key."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib API
        return None


class SupabaseRest:
    def __init__(self, url: str, key: str, timeout: float = 5.0, _allow_http: bool = False) -> None:
        u = urlparse(url)
        if u.scheme != "https" and not (_allow_http and u.scheme == "http"):
            raise ValueError("Supabase URL must be https://")
        self._base = url.rstrip("/") + "/rest/v1/"
        self._key = key
        self.timeout = timeout
        self.host = u.hostname
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self) -> str:
        return f"SupabaseRest(host={self.host!r}, key_fp={fingerprint(self._key)!r})"

    # -- transport --------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "[secret]") if self._key else text

    def _request(self, method: str, path: str, body=None, prefer: str | None = None):
        headers = {
            "apikey": self._key,  # new-style keys go here only, never Authorization: Bearer
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        req = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                payload = json.loads(exc.read().decode("utf-8", "replace") or "{}")
                detail = str(payload.get("message") or payload.get("hint") or "")[:160]
            except Exception:
                pass
            msg = f"HTTP {exc.code} from Supabase" + (f": {detail}" if detail else "")
            raise StorageError(self._scrub(msg)) from None
        except (TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise StorageError(self._scrub(f"could not reach Supabase ({type(reason).__name__}: {reason})")[:200]) from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise StorageError("Supabase returned non-JSON") from None

    # -- the four operations Strata needs -----------------------------------------

    def upsert_agent(self, agent: dict) -> None:
        self._request("POST", "agents?on_conflict=id", [agent],
                      prefer="resolution=merge-duplicates,return=minimal")

    def insert_diagnoses(self, rows: list[dict]) -> None:
        # Client-generated UUIDs + ignore-duplicates make retries idempotent.
        self._request("POST", "diagnoses?on_conflict=id", rows,
                      prefer="resolution=ignore-duplicates,return=minimal")

    def agents(self) -> list[dict]:
        return self._request("GET", "agents?select=id,name,region&order=id.asc") or []

    def history(self, agent_id: str, limit: int) -> list[dict]:
        q = (f"diagnoses?select=id,agent_id,ts,label,probs,anomaly_score"
             f"&agent_id=eq.{quote(agent_id, safe='')}&order=ts.desc&limit={int(limit)}")
        return self._request("GET", q) or []

    def recent(self, since_iso: str, limit: int = 10000) -> list[dict]:
        q = (f"diagnoses?select=agent_id,ts,label,anomaly_score"
             f"&ts=gte.{quote(since_iso, safe='')}&order=ts.asc&limit={int(limit)}")
        return self._request("GET", q) or []
