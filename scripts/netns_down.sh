#!/usr/bin/env bash
# Remove everything scripts/netns_up.sh created, then verify nothing is left.
# Safe to run any number of times. Run with sudo.
#
#   sudo scripts/netns_down.sh          # default namespace "strata"
#   sudo scripts/netns_down.sh b        # agent namespace "strata-b" only
#   sudo scripts/netns_down.sh all      # default + strata-a, strata-b, strata-c
#
# WHAT THIS CHANGES, per namespace (default profile shown; agents use their own names/tag):
#   1. Deletes the three iptables rules netns_up.sh added, by exact spec (iptables -D),
#      only while `iptables -C` still finds them. Docker's chains and rules are untouched,
#      and so are the rules of any other strata namespace (different spec and tag).
#   2. Deletes the "strata" namespace and the strata-host/strata-ns veth pair (with any
#      fault qdisc on strata-host).
#   3. Deletes /etc/netns/strata/ and /run/strata/strata/ (and /run/strata if then empty).
# It does not change net.ipv4.ip_forward, the main interface, the host's DNS, or saved
# firewall rules. Exit code is 1 if anything is still left afterwards.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "all" ]]; then
  set -- "" a b c
fi
if [[ $# -gt 1 ]]; then  # several namespaces: tear each down, report overall result
  rc=0
  for agent in "$@"; do
    echo "== ${agent:-default}"
    "$HERE/netns_down.sh" "$agent" || rc=1
  done
  exit $rc
fi

source "$HERE/netns_env.sh"
load_profile "${1:-}" || exit 2

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

MAIN_IF="$(detect_main_if)"
set_rule_specs "$MAIN_IF"
CHAINS=(DOCKER-USER FORWARD)

# 1. exact rules only
while iptables -t nat -C POSTROUTING "${NAT_SPEC[@]}" 2>/dev/null; do
  iptables -t nat -D POSTROUTING "${NAT_SPEC[@]}" && echo "deleted nat/POSTROUTING MASQUERADE $SUBNET -o $MAIN_IF"
done
for chain in "${CHAINS[@]}"; do
  for name in FWD_OUT_SPEC FWD_IN_SPEC; do
    declare -n spec="$name"
    while iptables -C "$chain" "${spec[@]}" 2>/dev/null; do
      iptables -D "$chain" "${spec[@]}" && echo "deleted $chain ${spec[*]:0:4}"
    done
    unset -n spec
  done
done

# 2. namespace + veth (+ qdisc)
if ip link show "$VETH_HOST" >/dev/null 2>&1; then
  tc qdisc del dev "$VETH_HOST" root 2>/dev/null
fi
ip netns del "$NS" 2>/dev/null && echo "deleted namespace $NS"
ip link del "$VETH_HOST" 2>/dev/null && echo "deleted $VETH_HOST"

# 3. files
[[ -d "$NETNS_ETC" ]] && rm -rf "$NETNS_ETC" && echo "deleted $NETNS_ETC"
[[ -d "$STATE_DIR" ]] && rm -rf "$STATE_DIR" && echo "deleted $STATE_DIR"
rmdir "$STATE_ROOT" 2>/dev/null  # only if no other namespace still has state there

# Verify
echo "verification ($NS):"
left=0
check() {  # check <description> <command that succeeds if the item still exists>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "  LEFT  $desc"; left=1; else echo "  gone  $desc"; fi
}
check "nat/POSTROUTING MASQUERADE $SUBNET -o $MAIN_IF" iptables -t nat -C POSTROUTING "${NAT_SPEC[@]}"
for chain in "${CHAINS[@]}"; do
  check "$chain accept $VETH_HOST -> $MAIN_IF" iptables -C "$chain" "${FWD_OUT_SPEC[@]}"
  check "$chain accept $MAIN_IF -> $VETH_HOST (replies)" iptables -C "$chain" "${FWD_IN_SPEC[@]}"
done
check "any rule tagged '$TAG'" tag_rules_left "$TAG"
check "namespace $NS" netns_exists "$NS"
check "veth $VETH_HOST" ip link show "$VETH_HOST"
check "veth $VETH_NS" ip link show "$VETH_NS"
check "$NETNS_ETC" test -e "$NETNS_ETC"
check "$STATE_DIR" test -e "$STATE_DIR"
echo "  ip_forward unchanged: $(sysctl -n net.ipv4.ip_forward)"

if [[ $left -eq 0 ]]; then echo "teardown done: nothing left"; else echo "teardown INCOMPLETE: see LEFT above"; fi
exit $left
