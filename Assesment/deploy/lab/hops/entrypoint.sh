#!/bin/sh
# A router of the lab's chain: forwards the far network one way and the lab
# host's network the other way. Forwarding is switched on by Compose.
#
#   LAB_FORWARD   the network that lies ahead, for example 10.77.25.0/24
#   LAB_NEXT      the next router toward it, or "direct:<own address>" when the
#                 network is on a segment of this router
#   LAB_BACK      the router back toward the lab host; empty when this router
#                 is on the lab host's own segment
#   LAB_SILENT    "true": forward everything and send no ICMP error at all,
#                 like a router that hides itself from traceroute
set -eu

: "${LAB_FORWARD:?}"
: "${LAB_NEXT:?}"
LAB_BACK="${LAB_BACK:-}"
LAB_SILENT="${LAB_SILENT:-false}"
LAB_HOST_NETWORK="${LAB_HOST_NETWORK:-10.77.0.0/24}"

case "$LAB_NEXT" in
    direct:*)
        own="${LAB_NEXT#direct:}"
        interface="$(ip -o -4 addr show | awk -v address="$own" '$4 ~ "^" address "/" { print $2 }')"
        [ -n "$interface" ] || { echo "no interface has the address $own" >&2; exit 1; }
        ip route replace "$LAB_FORWARD" dev "$interface"
        ;;
    *)
        ip route replace "$LAB_FORWARD" via "$LAB_NEXT"
        ;;
esac

if [ -n "$LAB_BACK" ]; then
    ip route replace "$LAB_HOST_NETWORK" via "$LAB_BACK"
fi

if [ "$LAB_SILENT" = "true" ]; then
    # Time exceeded is what traceroute reads; unreachable would give it away too.
    iptables -A OUTPUT -p icmp --icmp-type time-exceeded -j DROP
    iptables -A OUTPUT -p icmp --icmp-type destination-unreachable -j DROP
fi

touch /tmp/ready
echo "lab hop ready: $LAB_FORWARD via $LAB_NEXT, back via ${LAB_BACK:-its own segment}, silent=$LAB_SILENT"
exec sleep infinity
