"""Network-namespace profiles: one per agent. Must match scripts/netns_env.sh.

    strata    (default, single agent)  strata-host / strata-ns    10.200.0.0/24
    strata-a                           strata-a-host / strata-a-ns  10.200.1.0/24
    strata-b                           strata-b-host / strata-b-ns  10.200.2.0/24
    strata-c                           strata-c-host / strata-c-ns  10.200.3.0/24

Only the default profile honours STRATA_VETH_HOST / STRATA_GW_IP overrides from .env,
so an agent namespace can never be pointed at another agent's (or any other) device.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

DEFAULT_NETNS = "strata"
AGENTS = {"a": 1, "b": 2, "c": 3}


@dataclass(frozen=True)
class Profile:
    netns: str
    veth_host: str
    veth_ns: str
    gw_ip: str
    subnet: str
    tag: str  # iptables comment on this namespace's rules

    @property
    def agent(self) -> str | None:
        m = re.fullmatch(r"strata-([abc])", self.netns)
        return m.group(1) if m else None


def profile(netns: str | None = None) -> Profile:
    netns = netns or os.getenv("STRATA_NETNS", DEFAULT_NETNS) or DEFAULT_NETNS
    if netns == DEFAULT_NETNS:
        return Profile(
            netns=netns,
            veth_host=os.getenv("STRATA_VETH_HOST", "strata-host"),
            veth_ns=os.getenv("STRATA_VETH_NS", "strata-ns"),
            gw_ip=os.getenv("STRATA_GW_IP", "10.200.0.1"),
            subnet="10.200.0.0/24",
            tag="strata",
        )
    m = re.fullmatch(r"strata-([abc])", netns)
    if not m:
        raise ValueError(f"unknown namespace {netns!r}: use strata or strata-a/b/c")
    letter, idx = m.group(1), AGENTS[m.group(1)]
    return Profile(
        netns=netns,
        veth_host=f"strata-{letter}-host",
        veth_ns=f"strata-{letter}-ns",
        gw_ip=f"10.200.{idx}.1",
        subnet=f"10.200.{idx}.0/24",
        tag=netns,
    )


def from_agent(agent: str | None) -> Profile:
    """'a' / 'b' / 'c' -> agent profile; None or '' -> default single namespace."""
    if not agent:
        return profile(DEFAULT_NETNS)
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}: use a, b or c")
    return profile(f"strata-{agent}")
