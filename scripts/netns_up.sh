#!/usr/bin/env bash
# Create the isolated test network for Strata. Run with sudo on the OCI host.
#
# WHAT THIS CHANGES (all undone by scripts/netns_down.sh):
#   1. Creates network namespace "strata".
#   2. Creates a veth pair: st-host (host side, 10.200.0.1/24) <-> st-ns (inside the
#      namespace, 10.200.0.2/24), and a default route in the namespace via 10.200.0.1.
#   3. Writes /etc/netns/strata/resolv.conf (DNS config seen ONLY inside the namespace).
#   4. Sets net.ipv4.ip_forward=1 (host-wide sysctl). The old value is saved in
#      /run/strata/ip_forward and restored on teardown.
#   5. Adds iptables rules, each tagged with comment "strata":
#        nat/POSTROUTING  MASQUERADE 10.200.0.0/24 out of the main interface
#        filter/FORWARD   ACCEPT st-host -> main, and main -> st-host for replies only
#      Rules are not saved persistently; a reboot drops them.
#
# WHAT THIS DOES NOT CHANGE: the main interface's addresses, routes, qdiscs, MTU,
# the host's /etc/resolv.conf, or any existing iptables rule. Faults are injected
# later by faults/netns.py on st-host only.
set -euo pipefail

NS="${STRATA_NETNS:-strata}"
VETH_HOST="${STRATA_VETH_HOST:-st-host}"
VETH_NS="${STRATA_VETH_NS:-st-ns}"
GW_IP="${STRATA_GW_IP:-10.200.0.1}"
NS_IP="${STRATA_NS_IP:-10.200.0.2}"
SUBNET="${STRATA_SUBNET:-10.200.0.0/24}"
DNS_PRIMARY="${STRATA_DNS_PRIMARY:-1.1.1.1}"
STATE_DIR="${STRATA_STATE_DIR:-/run/strata}"
TAG="strata"

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
for tool in ip iptables sysctl; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

if ip netns list | grep -qw "$NS"; then
  echo "namespace $NS already exists. Run scripts/netns_down.sh first to recreate it." >&2
  exit 1
fi
if ip -4 route show | grep -q "${SUBNET%/*}"; then
  echo "subnet $SUBNET is already routed on this host; set STRATA_SUBNET etc. to another range." >&2
  exit 1
fi

MAIN_IF="$(ip -4 route show default | awk '/default/ {for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
[[ -n "$MAIN_IF" ]] || { echo "could not find the default-route interface" >&2; exit 1; }
echo "main interface (read only, used as NAT egress): $MAIN_IF"

# Undo partial setup if any step below fails.
trap 'echo "setup failed; rolling back" >&2; "$(dirname "$0")/netns_down.sh"' ERR

mkdir -p "$STATE_DIR"
sysctl -n net.ipv4.ip_forward > "$STATE_DIR/ip_forward"
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
mkdir -p "/etc/netns/$NS"
printf '# managed by strata\nnameserver %s\noptions timeout:2 attempts:1\n' "$DNS_PRIMARY" > "/etc/netns/$NS/resolv.conf"

# 4. forwarding
sysctl -qw net.ipv4.ip_forward=1

# 5. NAT and forward rules (inserted at the top so existing REJECT rules don't block them)
iptables -t nat -A POSTROUTING -s "$SUBNET" -o "$MAIN_IF" -m comment --comment "$TAG" -j MASQUERADE
iptables -I FORWARD 1 -i "$VETH_HOST" -o "$MAIN_IF" -m comment --comment "$TAG" -j ACCEPT
iptables -I FORWARD 1 -i "$MAIN_IF" -o "$VETH_HOST" -m conntrack --ctstate RELATED,ESTABLISHED \
  -m comment --comment "$TAG" -j ACCEPT

echo "namespace $NS ready. Connectivity check:"
ip netns exec "$NS" ping -n -c 2 -W 2 "$GW_IP" >/dev/null && echo "  gateway $GW_IP ok" || echo "  gateway $GW_IP FAILED"
ip netns exec "$NS" ping -n -c 2 -W 2 8.8.8.8 >/dev/null && echo "  internet (8.8.8.8) ok" || echo "  internet FAILED"
if command -v dig >/dev/null; then
  ip netns exec "$NS" dig +short +time=2 +tries=1 example.com A | head -1 | sed 's/^/  dns example.com -> /'
fi
