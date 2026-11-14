"""Pre-written explanations per diagnosis label. Works fully offline."""

from __future__ import annotations

from collector.features import FeatureRow


def _f(row: FeatureRow, key: str) -> str:
    value = row.get(key)
    return "n/a" if value is None else f"{value:.0f}"


def render(label: str, row: FeatureRow) -> tuple[str, list[str]]:
    if label == "normal":
        return (
            f"Everything looks healthy. Round trip to the internet is about {_f(row, 'ext_rtt_ms')} ms "
            "and name lookups are answering normally.",
            ["No action needed.", "Keep monitoring for changes."],
        )
    if label == "high_latency":
        return (
            f"Packets are arriving, but slowly: round trip is about {_f(row, 'ext_rtt_ms')} ms, "
            "far above the usual range. The delay appears from the first hop onward, so it sits on the link.",
            [
                "Pause large uploads or downloads on this network.",
                "Restart the router to clear queued traffic.",
                "If it persists, contact your ISP with this timestamp.",
            ],
        )
    if label == "packet_loss":
        return (
            f"About {_f(row, 'ext_loss_pct')}% of packets never come back. "
            "Lost packets force retransmissions, which makes pages stall and calls break up.",
            [
                "Check cables and connectors between device and router.",
                "Move closer to the router or switch to a wired connection.",
                "Report sustained loss to your ISP.",
            ],
        )
    if label == "bandwidth_limit":
        return (
            f"The connection is up but throughput is only {_f(row, 'throughput_mbps')} Mbps. "
            "Something on the link is capping the available bandwidth.",
            [
                "Check whether another device is saturating the connection.",
                "Look for a data cap or throttling on your plan.",
                "Run the test again on a wired connection to compare.",
            ],
        )
    if label == "dns_slow":
        return (
            f"The network itself is fast, but looking up names takes about {_f(row, 'dns_primary_ms')} ms. "
            "Every new site waits on this before it can even start loading.",
            [
                "Switch to a faster public DNS resolver.",
                "Restart the router to refresh its DNS cache.",
            ],
        )
    if label == "dns_failure":
        return (
            "Hosts answer when reached by IP address, but name lookups fail. "
            "The internet connection works; the DNS resolver does not.",
            [
                "Change the DNS server to a known public resolver.",
                "Check the DNS settings on the router or device.",
                "Flush the local DNS cache and try again.",
            ],
        )
    return ("The measurements look unusual, but the cause is unclear.", ["Run the diagnosis again."])
