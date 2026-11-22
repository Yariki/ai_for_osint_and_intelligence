#!/bin/sh
# Lab router: the routed hop between the lab host and the scanned network.
#
# Forwarding itself is switched on by Compose (sysctl net.ipv4.ip_forward).
# This script adds the route to the scanned network and makes the router
# silent about addresses that have no host.
set -eu

: "${LAB_SCANNED_NETWORK:?}"   # for example 10.77.3.0/24
: "${LAB_TARGETS_SIDE:?}"      # this router's address on the targets segment

# The interface that faces the targets is the one that holds LAB_TARGETS_SIDE.
interface="$(ip -o -4 addr show | awk -v address="$LAB_TARGETS_SIDE" '$4 ~ "^" address "/" { print $2 }')"
[ -n "$interface" ] || { echo "no interface has the address $LAB_TARGETS_SIDE" >&2; exit 1; }

# The scanned network is on that segment: the router asks for each address
# there directly. Only the targets container answers, for the manifest's hosts.
ip route replace "$LAB_SCANNED_NETWORK" dev "$interface"

# When nothing answers for an address, a router would report "host unreachable"
# to the sender. On the Internet such addresses are mostly just silent, so the
# lab router keeps quiet too: the packet is routed and silently dropped.
iptables -A OUTPUT -p icmp --icmp-type destination-unreachable -j DROP

touch /tmp/ready
echo "lab router ready: $LAB_SCANNED_NETWORK via $interface"

# Stay alive; forwarding is done by the kernel.
exec sleep infinity
