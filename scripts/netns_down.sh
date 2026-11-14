#!/usr/bin/env bash
# Remove everything scripts/netns_up.sh created. Safe to run more than once.
#
# WHAT THIS CHANGES:
#   1. Deletes the "strata" namespace. This also deletes the st-host/st-ns veth pair
#      and any fault qdisc on it.
#   2. Deletes /etc/netns/strata/ (namespace-only resolv.conf).
#   3. Deletes only the iptables rules tagged with comment "strata".
#   4. Restores net.ipv4.ip_forward to the value saved by netns_up.sh.
#   5. Deletes /run/strata/ (state files).
# It does not touch the main interface or any rule it did not create.
set -uo pipefail

NS="${STRATA_NETNS:-strata}"
VETH_HOST="${STRATA_VETH_HOST:-st-host}"
STATE_DIR="${STRATA_STATE_DIR:-/run/strata}"
TAG="strata"

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

# 1. namespace + veth (+ qdisc)
tc qdisc del dev "$VETH_HOST" root 2>/dev/null
ip netns del "$NS" 2>/dev/null && echo "deleted namespace $NS"
ip link del "$VETH_HOST" 2>/dev/null && echo "deleted $VETH_HOST"

# 2. namespace DNS
rm -rf "/etc/netns/$NS"

# 3. tagged iptables rules only (delete by rule number, highest first)
for table in nat filter; do
  for chain in POSTROUTING FORWARD; do
    [[ $table == nat && $chain == FORWARD ]] && continue
    [[ $table == filter && $chain == POSTROUTING ]] && continue
    iptables -t "$table" -L "$chain" --line-numbers -n 2>/dev/null \
      | awk -v tag="/* $TAG */" 'index($0, tag) {print $1}' | sort -rn \
      | while read -r num; do
          iptables -t "$table" -D "$chain" "$num" && echo "deleted $table/$chain rule $num"
        done
  done
done

# 4. forwarding
if [[ -f "$STATE_DIR/ip_forward" ]]; then
  sysctl -qw "net.ipv4.ip_forward=$(cat "$STATE_DIR/ip_forward")"
  echo "restored net.ipv4.ip_forward=$(cat "$STATE_DIR/ip_forward")"
fi

# 5. state
rm -rf "$STATE_DIR"
echo "teardown done"
