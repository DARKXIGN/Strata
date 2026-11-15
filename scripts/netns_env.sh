# Shared settings for netns_up.sh / netns_down.sh. Sourced, not executed.
# Defining every rule once here guarantees teardown deletes exactly what setup added.
#
# Profiles (must match faults/profiles.py):
#   (none)  strata    strata-host   / strata-ns     10.200.0.0/24   tag "strata"
#   a       strata-a  strata-a-host / strata-a-ns   10.200.1.0/24   tag "strata-a"
#   b       strata-b  strata-b-host / strata-b-ns   10.200.2.0/24   tag "strata-b"
#   c       strata-c  strata-c-host / strata-c-ns   10.200.3.0/24   tag "strata-c"
# Every profile has its own namespace, veth pair, subnet, iptables comment tag and state dir,
# so agents are set up, faulted and torn down independently.

# Resolver for the namespace only (host uses the 127.0.0.53 stub, unreachable from a netns).
# A single resolver on purpose: the training data was collected this way, and a second
# entry would let dig fall back on timeouts and change the dns_primary_* features.
DNS_SERVERS="${STRATA_DNS_SERVERS:-1.1.1.1}"
STATE_ROOT="${STRATA_STATE_DIR:-/run/strata}"
KNOWN_AGENTS=(a b c)

# load_profile [a|b|c]  -> sets NS VETH_HOST VETH_NS GW_IP NS_IP SUBNET TAG NETNS_ETC STATE_DIR
load_profile() {
  local agent="${1:-}"
  case "$agent" in
    "")
      # Default single namespace; .env-style overrides are honoured only here.
      NS="${STRATA_NETNS:-strata}"
      [[ "$NS" == "strata" ]] || { echo "STRATA_NETNS=$NS: use the agent argument (a|b|c) instead" >&2; return 1; }
      VETH_HOST="${STRATA_VETH_HOST:-strata-host}"   # Docker's veths are vethXXXX; ours are strata-*
      VETH_NS="${STRATA_VETH_NS:-strata-ns}"
      GW_IP="${STRATA_GW_IP:-10.200.0.1}"
      NS_IP="${STRATA_NS_IP:-10.200.0.2}"
      SUBNET="${STRATA_SUBNET:-10.200.0.0/24}"
      TAG="strata"
      ;;
    a|b|c)
      local idx
      case "$agent" in a) idx=1 ;; b) idx=2 ;; c) idx=3 ;; esac
      NS="strata-$agent"
      VETH_HOST="strata-$agent-host"
      VETH_NS="strata-$agent-ns"
      GW_IP="10.200.$idx.1"
      NS_IP="10.200.$idx.2"
      SUBNET="10.200.$idx.0/24"
      TAG="strata-$agent"
      ;;
    *)
      echo "unknown agent '$agent': use a, b, c, or nothing for the default namespace" >&2
      return 1
      ;;
  esac
  NETNS_ETC="/etc/netns/$NS"
  STATE_DIR="$STATE_ROOT/$NS"
}

# Exact-name checks: "strata" must not match "strata-a" and vice versa.
netns_exists() { ip netns list 2>/dev/null | awk '{print $1}' | grep -qx -- "$1"; }
tag_rules_left() { iptables-save 2>/dev/null | grep -qE -- "--comment \"?$1\"?( |$)"; }

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
