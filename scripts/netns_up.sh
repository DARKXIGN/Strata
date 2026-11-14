#!/usr/bin/env bash
# Create the isolated test network for Strata. Run with sudo on the OCI host.
#
# WHAT THIS CHANGES (all undone by scripts/netns_down.sh):
#   1. Creates network namespace "strata".
#   2. Creates a veth pair strata-host (host side, 10.200.0.1/24) <-> strata-ns (inside
#      the namespace, 10.200.0.2/24) and a default route in the namespace via 10.200.0.1.
#   3. Writes /etc/netns/strata/resolv.conf with 1.1.1.1 and 8.8.8.8. Only processes run
#      with `ip netns exec strata` see it.
#   4. Adds exactly three iptables rules (iptables command only, comment "strata"):
#        nat/POSTROUTING  -s 10.200.0.0/24 -o <main if> -j MASQUERADE     (appended)
#        DOCKER-USER      -i strata-host -o <main if> -j ACCEPT           (inserted at top)
#        DOCKER-USER      -i <main if> -o strata-host RELATED,ESTABLISHED (inserted at top)
#
# WHAT THIS DOES NOT CHANGE:
#   * the main interface (enp0s6): no address, route, qdisc or MTU change
#   * net.ipv4.ip_forward: only checked; setup stops if it is 0
#   * the host's /etc/resolv.conf and systemd-resolved
#   * Docker's chains and rules, or any other existing rule
#   * saved firewall rules: nothing runs netfilter-persistent save or writes
#     /etc/iptables/rules.v4, so a reboot drops these rules. Do not run
#     `netfilter-persistent save` while the namespace is up.
set -euo pipefail
source "$(dirname "$0")/netns_env.sh"

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
for tool in ip iptables sysctl; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

if ip netns list | grep -qw "$NS"; then
  echo "namespace $NS already exists. Run sudo scripts/netns_down.sh first." >&2
  exit 1
fi
if ip link show "$VETH_HOST" >/dev/null 2>&1; then
  echo "$VETH_HOST already exists. Run sudo scripts/netns_down.sh first." >&2
  exit 1
fi
if ip -4 route show | grep -q "${SUBNET%/*}"; then
  echo "subnet $SUBNET is already routed on this host; set STRATA_SUBNET etc. to another range." >&2
  exit 1
fi
if [[ "$(sysctl -n net.ipv4.ip_forward)" != "1" ]]; then
  echo "net.ipv4.ip_forward is 0. This script does not change it; enable it yourself if you accept that." >&2
  exit 1
fi

MAIN_IF="$(detect_main_if)"
[[ -n "$MAIN_IF" ]] || { echo "could not find the default-route interface" >&2; exit 1; }
FWD_CHAIN="$(detect_fwd_chain)"
set_rule_specs "$MAIN_IF"
echo "main interface (read only): $MAIN_IF   forward rules go to: $FWD_CHAIN"

# Undo partial setup if any step below fails.
trap 'echo "setup failed; rolling back" >&2; "$(dirname "$0")/netns_down.sh"' ERR

mkdir -p "$STATE_DIR"
echo "$MAIN_IF" > "$STATE_DIR/main_if"

# 1-2. namespace and veth pair
ip netns add "$NS"
ip link add "$VETH_HOST" type veth peer name "$VETH_NS"
ip link set "$VETH_NS" netns "$NS"
ip addr add "$GW_IP/24" dev "$VETH_HOST"
ip link set "$VETH_HOST" up
ip netns exec "$NS" ip link set lo up
ip netns exec "$NS" ip addr add "$NS_IP/24" dev "$VETH_NS"
ip netns exec "$NS" ip link set "$VETH_NS" up
ip netns exec "$NS" ip route add default via "$GW_IP"

# 3. namespace-only DNS
mkdir -p "$NETNS_ETC"
{
  echo "# managed by strata"
  for s in $DNS_SERVERS; do echo "nameserver $s"; done
  echo "options timeout:2 attempts:1"
} > "$NETNS_ETC/resolv.conf"

# 4. NAT and forward rules
iptables -t nat -A POSTROUTING "${NAT_SPEC[@]}"
iptables -I "$FWD_CHAIN" 1 "${FWD_OUT_SPEC[@]}"
iptables -I "$FWD_CHAIN" 1 "${FWD_IN_SPEC[@]}"

trap - ERR
echo "namespace $NS ready. Connectivity check:"
ip netns exec "$NS" ping -n -c 2 -W 2 "$GW_IP" >/dev/null && echo "  gateway $GW_IP ok" || echo "  gateway $GW_IP FAILED"
ip netns exec "$NS" ping -n -c 2 -W 2 8.8.8.8 >/dev/null && echo "  internet (8.8.8.8) ok" || echo "  internet FAILED"
if command -v dig >/dev/null; then
  ans="$(ip netns exec "$NS" dig +short +time=2 +tries=1 example.com A | head -1)"
  [[ -n "$ans" ]] && echo "  dns example.com -> $ans" || echo "  dns FAILED"
fi
command -v traceroute >/dev/null || echo "  note: traceroute is not installed (sudo apt install -y traceroute)"
