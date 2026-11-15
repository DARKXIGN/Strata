"""Fault control across agents on one host, so one dashboard can fault any agent.

The dashboard talks to one agent's server. For the other agents, that server forwards
the request to their own server, which injects the fault in its own namespace and keeps
its own state (so its detection timing and reset-on-exit still work).

Targets come only from configuration, never from the request:
    STRATA_PEERS="strata-a=http://127.0.0.1:8000,strata-b=http://127.0.0.1:8001,..."
If unset and this server is agent strata-a/b/c, the documented layout is assumed
(a=8000, b=8001, c=8002 on 127.0.0.1). A single-agent server has no peers.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

DEFAULT_LAYOUT = {
    "strata-a": "http://127.0.0.1:8000",
    "strata-b": "http://127.0.0.1:8001",
    "strata-c": "http://127.0.0.1:8002",
}


class PeerError(Exception):
    """Safe to show: names the agent, not internals."""


def parse_peers(text: str) -> dict[str, str]:
    peers: dict[str, str] = {}
    for item in filter(None, (p.strip() for p in text.split(","))):
        agent_id, _, url = item.partition("=")
        agent_id, url = agent_id.strip(), url.strip().rstrip("/")
        u = urlparse(url)
        if not agent_id or u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError(f"bad STRATA_PEERS entry {item!r}: use id=http://host:port")
        peers[agent_id] = url
    return peers


def load_peers(self_id: str, env: dict[str, str] | None = None) -> dict[str, str]:
    """Other agents' control URLs (self excluded)."""
    e = os.environ if env is None else env
    if e.get("STRATA_PEERS"):
        peers = parse_peers(e["STRATA_PEERS"])
    elif re.fullmatch(r"strata-[abc]", self_id):
        peers = dict(DEFAULT_LAYOUT)
    else:
        peers = {}
    peers.pop(self_id, None)
    return peers


class PeerControl:
    def __init__(self, self_id: str, peers: dict[str, str], timeout: float = 2.0) -> None:
        self.self_id = self_id
        self.peers = peers
        self.timeout = timeout
        # Local peers: never send these through an HTTP proxy from the environment.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def knows(self, agent_id: str) -> bool:
        return agent_id == self.self_id or agent_id in self.peers

    def _call(self, agent_id: str, method: str, path: str) -> dict:
        url = self.peers[agent_id] + path
        req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("detail")
            except ValueError:
                detail = None
            raise PeerError(f"{agent_id}: HTTP {exc.code}" + (f" ({detail})" if detail else "")) from None
        except (OSError, ValueError):
            raise PeerError(f"{agent_id} is not reachable (is its server running?)") from None

    def status(self, agent_id: str) -> dict:
        """Reachability and active fault of one peer."""
        try:
            s = self._call(agent_id, "GET", "/api/status")
        except PeerError as exc:
            return {"id": agent_id, "name": agent_id, "reachable": False, "active_fault": None, "error": str(exc)}
        agent = s.get("agent") or {}
        return {
            "id": agent_id,
            "name": agent.get("name") or agent_id,
            "reachable": True,
            "active_fault": s.get("active_fault"),
            "mode": "sim" if s.get("sim_mode") else "real",
        }

    def status_all(self) -> list[dict]:
        if not self.peers:
            return []
        with ThreadPoolExecutor(max_workers=len(self.peers)) as pool:
            return list(pool.map(self.status, sorted(self.peers)))

    def inject(self, agent_id: str, name: str) -> dict:
        return self._call(agent_id, "POST", f"/api/faults/{name}")

    def reset(self, agent_id: str) -> dict:
        return self._call(agent_id, "POST", "/api/faults/reset")
