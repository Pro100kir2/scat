"""Provider probes: distributed reachability checks of VPN endpoints from ISP networks.

Strictly separate from the VLESS scanner. The scanner answers "does this config work?";
probes only answer "can this endpoint be reached from this network?" (TCP/TLS/HTTP).
"""
