#!/usr/bin/env bash
# Inject or reset one fault inside the strata namespace. Run with sudo.
#
# WHAT THIS CHANGES (details in faults/netns.py):
#   inject <link fault>  replaces the root qdisc on strata-host (host end of the veth pair)
#                        with prio + netem. Gateway replies are not shaped.
#   inject dns_slow      same, but only packets with source port 53 are delayed.
#   inject dns_failure   points /etc/netns/strata/resolv.conf at 192.0.2.53 (unreachable).
#   reset                deletes the strata-host qdisc and restores the namespace resolv.conf.
# Never touches the main interface (enp0s6), iptables, or the host's /etc/resolv.conf.
#
# Examples:
#   sudo scripts/fault.sh inject high_latency delay_ms=200 jitter_ms=20
#   sudo scripts/fault.sh inject packet_loss loss_pct=10
#   sudo scripts/fault.sh inject bandwidth_limit rate_kbit=1000
#   sudo scripts/fault.sh inject dns_slow delay_ms=800
#   sudo scripts/fault.sh inject dns_failure
#   sudo scripts/fault.sh reset
#   scripts/fault.sh status
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
cd "$ROOT"
exec "$PY" -m faults.netns "$@"
