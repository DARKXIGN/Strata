# Shared settings for netns_up.sh / netns_down.sh. Sourced, not executed.
# Defining every rule once here guarantees teardown deletes exactly what setup added.

NS="${STRATA_NETNS:-strata}"
VETH_HOST="${STRATA_VETH_HOST:-strata-host}"   # Docker's veths are vethXXXX; ours are strata-*
VETH_NS="${STRATA_VETH_NS:-strata-ns}"
GW_IP="${STRATA_GW_IP:-10.200.0.1}"
NS_IP="${STRATA_NS_IP:-10.200.0.2}"
SUBNET="${STRATA_SUBNET:-10.200.0.0/24}"
# Resolvers for the namespace only (host uses the 127.0.0.53 stub, unreachable from a netns).
DNS_SERVERS="${STRATA_DNS_SERVERS:-1.1.1.1 8.8.8.8}"
STATE_DIR="${STRATA_STATE_DIR:-/run/strata}"
NETNS_ETC="/etc/netns/$NS"
TAG="strata"

# Interface that carries the default route (enp0s6 on the OCI host). Read only:
# it is used as the NAT/forward egress match and never gets a qdisc.
detect_main_if() {
  if [[ -n "${STRATA_MAIN_IF:-}" ]]; then echo "$STRATA_MAIN_IF"; return; fi
  if [[ -f "$STATE_DIR/main_if" ]]; then cat "$STATE_DIR/main_if"; return; fi
  ip -4 route show default | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}'
}

# Forward rules go into DOCKER-USER: FORWARD's policy is DROP and it ends in REJECT,
# and Docker jumps to DOCKER-USER first. Without Docker, fall back to the top of FORWARD.
detect_fwd_chain() {
  if iptables -L DOCKER-USER -n >/dev/null 2>&1; then echo DOCKER-USER; else echo FORWARD; fi
}

set_rule_specs() {
  local main_if="$1"
  NAT_SPEC=(-s "$SUBNET" -o "$main_if" -m comment --comment "$TAG" -j MASQUERADE)
  FWD_OUT_SPEC=(-i "$VETH_HOST" -o "$main_if" -m comment --comment "$TAG" -j ACCEPT)
  FWD_IN_SPEC=(-i "$main_if" -o "$VETH_HOST" -m conntrack --ctstate RELATED,ESTABLISHED
               -m comment --comment "$TAG" -j ACCEPT)
}
