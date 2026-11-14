"""Real fault injection, confined to the strata network namespace.

WHAT THIS CHANGES (needs root, and scripts/netns_up.sh must have run first):
  * Link faults (high_latency, packet_loss, bandwidth_limit) and dns_slow:
    replaces the root qdisc on the HOST end of the veth pair (default `strata-host`) with
    `prio` + `netem`. Only traffic flowing from the host into the namespace is shaped.
    Replies from the gateway (10.200.0.1) are excluded, so "gateway fine, internet bad"
    stays visible. dns_slow only shapes packets with source port 53.
  * dns_failure: rewrites /etc/netns/<ns>/resolv.conf (seen only inside the namespace)
    to point at an unreachable resolver (192.0.2.53, TEST-NET-1).
  * reset: deletes that qdisc and restores the namespace resolv.conf.
It never touches the host's main interface, routes, or /etc/resolv.conf. Before every
change it checks that the target device is a veth named strata-* and is not the
default-route interface; otherwise it refuses. Uses tc only (no iptables/nft).

CLI (via scripts/fault.sh):
    sudo scripts/fault.sh inject high_latency delay_ms=200
    sudo scripts/fault.sh reset
    sudo scripts/fault.sh status
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from faults import INJECTABLE
from faults.sim import FaultState

NETNS = os.getenv("STRATA_NETNS", "strata")
VETH_HOST = os.getenv("STRATA_VETH_HOST", "strata-host")
GW_IP = os.getenv("STRATA_GW_IP", "10.200.0.1")
# Must match DNS_SERVERS in scripts/netns_env.sh.
DNS_SERVERS = os.getenv("STRATA_DNS_SERVERS", "1.1.1.1").replace(",", " ").split()
DEAD_RESOLVER = "192.0.2.53"
STATE_FILE = Path(os.getenv("STRATA_STATE_DIR", "/run/strata")) / "fault.json"

LINK_FAULTS = {"high_latency", "packet_loss", "bandwidth_limit"}


def sample_params(name: str, rng: random.Random | None = None) -> dict[str, float]:
    """Random severity per collection session, so the model sees a range, not one setting."""
    r = rng or random
    if name == "high_latency":
        delay = r.randint(60, 350)
        return {"delay_ms": delay, "jitter_ms": round(delay * r.uniform(0.03, 0.15))}
    if name == "packet_loss":
        return {"loss_pct": r.choice([3, 5, 8, 10, 15, 20, 25, 30])}
    if name == "bandwidth_limit":
        return {"rate_kbit": r.choice([500, 1000, 1500, 2000, 3000, 4000])}
    if name == "dns_slow":
        delay = r.randint(300, 1500)
        return {"delay_ms": delay, "jitter_ms": round(delay * 0.1)}
    return {}


def netem_args(name: str, p: dict[str, float]) -> list[str]:
    # Queue limits: delay needs room for a full bandwidth-delay product or netem drops
    # packets (which would look like packet_loss); a rate limit wants a short queue so
    # it doesn't build seconds of standing delay.
    if name in ("high_latency", "dns_slow"):
        return ["delay", f"{int(p['delay_ms'])}ms", f"{int(p.get('jitter_ms', 0))}ms", "limit", "20000"]
    if name == "packet_loss":
        return ["loss", f"{p['loss_pct']:g}%", "limit", "10000"]
    if name == "bandwidth_limit":
        return ["rate", f"{int(p['rate_kbit'])}kbit", "limit", "100"]
    raise ValueError(f"{name} has no netem form")


def reset_commands(dev: str = VETH_HOST) -> list[list[str]]:
    return [["tc", "qdisc", "del", "dev", dev, "root"]]


def inject_commands(name: str, params: dict[str, float], dev: str = VETH_HOST, gw_ip: str = GW_IP) -> list[list[str]]:
    """tc commands for one fault. Every command targets `dev` only."""
    if name == "dns_failure":
        return []  # resolv.conf change only
    tc = ["tc"]
    cmds = [
        # 3 bands; everything unfiltered goes to band 1:2 (unshaped).
        [*tc, "qdisc", "add", "dev", dev, "root", "handle", "1:", "prio", "bands", "3",
         "priomap", *["1"] * 16],
        # Gateway replies stay clean.
        [*tc, "filter", "add", "dev", dev, "parent", "1:", "protocol", "ip", "prio", "1",
         "u32", "match", "ip", "src", f"{gw_ip}/32", "flowid", "1:1"],
        [*tc, "qdisc", "add", "dev", dev, "parent", "1:3", "handle", "30:", "netem", *netem_args(name, params)],
    ]
    if name in LINK_FAULTS:
        match = ["match", "u32", "0", "0"]  # all other IP traffic
    elif name == "dns_slow":
        match = ["match", "ip", "sport", "53", "0xffff"]  # DNS replies only
    else:
        raise ValueError(f"unknown fault: {name}")
    cmds.append([*tc, "filter", "add", "dev", dev, "parent", "1:", "protocol", "ip", "prio", "2",
                 "u32", *match, "flowid", "1:3"])
    return cmds


def resolv_path(netns: str = NETNS) -> Path:
    return Path("/etc/netns") / netns / "resolv.conf"


def resolv_text(name: str | None) -> str:
    servers = [DEAD_RESOLVER] if name == "dns_failure" else DNS_SERVERS
    lines = ["# managed by strata", *(f"nameserver {s}" for s in servers), "options timeout:2 attempts:1"]
    return "\n".join(lines) + "\n"


def default_route_dev() -> str | None:
    out = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True).stdout.split()
    return out[out.index("dev") + 1] if "dev" in out else None


class NetnsFaultController:
    mode = "netns"

    def __init__(self, netns: str = NETNS, dev: str = VETH_HOST) -> None:
        self.netns = netns
        self.dev = dev
        self.state = FaultState()
        self._check_device()

    def _check_device(self) -> None:
        if not self.dev.startswith("strata-"):
            raise RuntimeError(f"refusing to shape {self.dev}: only strata-* veth devices are allowed")
        if self.dev == default_route_dev():
            raise RuntimeError(f"refusing to shape {self.dev}: it carries the default route")
        out = subprocess.run(["ip", "-d", "link", "show", "dev", self.dev], capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"{self.dev} not found; run scripts/netns_up.sh first")
        if " veth " not in out.stdout:
            raise RuntimeError(f"refusing to shape {self.dev}: not a veth device")
        if not resolv_path(self.netns).parent.is_dir():
            raise RuntimeError(f"/etc/netns/{self.netns} missing; run scripts/netns_up.sh first")

    @staticmethod
    def _run(cmd: list[str], check: bool = True) -> None:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")

    def _write_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"name": self.state.name, "params": self.state.params,
                                          "injected_at": self.state.injected_at}))

    def inject(self, name: str, params: dict[str, float] | None = None) -> FaultState:
        if name not in INJECTABLE:
            raise ValueError(f"unknown fault: {name}")
        self.reset()
        params = params if params is not None else sample_params(name)
        try:
            for cmd in inject_commands(name, params, self.dev):
                self._run(cmd)
            if name == "dns_failure":
                resolv_path(self.netns).write_text(resolv_text(name))
        except Exception:
            self.reset()
            raise
        self.state = FaultState(name=name, injected_at=time.time(), params=params)
        self._write_state()
        return self.state

    def reset(self) -> FaultState:
        for cmd in reset_commands(self.dev):
            self._run(cmd, check=False)  # "no qdisc" is fine
        resolv_path(self.netns).write_text(resolv_text(None))
        self.state = FaultState()
        self._write_state()
        return self.state


def _parse_kv(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        key, _, value = item.partition("=")
        out[key] = float(value)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Inject or reset faults inside the strata namespace.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    inj = sub.add_parser("inject")
    inj.add_argument("name", choices=INJECTABLE)
    inj.add_argument("params", nargs="*", help="key=value, e.g. delay_ms=200 (random if omitted)")
    sub.add_parser("reset")
    sub.add_parser("status")
    args = ap.parse_args()

    if args.cmd == "status":
        print(STATE_FILE.read_text() if STATE_FILE.exists() else '{"name": null}')
        subprocess.run(["tc", "qdisc", "show", "dev", VETH_HOST])
        print(resolv_path().read_text() if resolv_path().exists() else "(no namespace resolv.conf)")
        return

    if os.geteuid() != 0:
        sys.exit("run with sudo")
    ctl = NetnsFaultController()
    if args.cmd == "reset":
        ctl.reset()
        print("reset: qdisc removed, namespace resolv.conf restored")
    else:
        state = ctl.inject(args.name, _parse_kv(args.params) or None)
        print(f"injected {state.name} {state.params}")


if __name__ == "__main__":
    main()
