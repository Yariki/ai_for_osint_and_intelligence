#!/bin/sh
# scanner-netinit: keeps the scanner's probes out of connection tracking.
#
#     install-rules.sh            install the two rules (the default)
#     install-rules.sh remove     remove them
#     install-rules.sh status     show them
#
# Why: Docker loads the kernel's connection tracking, which then also tracks
# the SYN packets the scanner sends from raw sockets. Unanswered SYNs stay in
# the table for about two minutes; at 5,000 packets per second that fills the
# table and disturbs all networking on the host (architecture section 8.5).
#
# The two rules exempt exactly the scanner's traffic, which is recognizable by
# its source-port range:
#   - packets the host sends from that range (the probes);
#   - packets the host receives to that range (the replies).
#
# Installing is idempotent: a rule that is already there is not added again.
set -eu

# The operator applies the rules by hand: nothing to do here.
if [ "${NDM_NETINIT_SKIP:-false}" = "true" ]; then
    echo "NDM_NETINIT_SKIP is set: no rule is installed. Apply the rules by hand (see README)."
    exit 0
fi

# The scanner's source-port range, as low-high (the form of the scanner's own
# setting, NDM_SOURCE_PORT_RANGE) or low:high. It must match the scanner's
# setting and lie outside net.ipv4.ip_local_port_range.
PORTS="$(printf '%s' "${NDM_SCANNER_SOURCE_PORTS:-61000-61999}" | tr '-' ':')"
# "iptables" (the nftables back end on current systems) or "iptables-legacy".
# Use the back end the host itself uses; see the README.
IPTABLES="${NDM_IPTABLES:-iptables}"
# Marks the rules as ours, so they can be found and removed.
COMMENT="ndm-scanner-notrack"

case "$PORTS" in
    *[!0-9:]* | *:*:* | :* | *: | "") echo "invalid port range '$PORTS'; expected low:high" >&2; exit 2 ;;
    *:*) ;;
    *) echo "invalid port range '$PORTS'; expected low:high" >&2; exit 2 ;;
esac

# The two rules, each as the arguments that follow "-A <chain>" or "-D <chain>".
rule_out="-p tcp --sport $PORTS -m comment --comment $COMMENT -j CT --notrack"
rule_in="-p tcp --dport $PORTS -m comment --comment $COMMENT -j CT --notrack"

present() {
    # shellcheck disable=SC2086  # the rule is a list of arguments
    "$IPTABLES" -w -t raw -C "$1" $2 2>/dev/null
}

install() {
    if present "$1" "$2"; then
        echo "already present: raw $1 $2"
    else
        # shellcheck disable=SC2086
        "$IPTABLES" -w -t raw -A "$1" $2
        echo "installed:       raw $1 $2"
    fi
}

remove() {
    # A loop, in case the rule was added more than once by other means.
    while present "$1" "$2"; do
        # shellcheck disable=SC2086
        "$IPTABLES" -w -t raw -D "$1" $2
        echo "removed:         raw $1 $2"
    done
}

case "${1:-install}" in
    install)
        install OUTPUT "$rule_out"
        install PREROUTING "$rule_in"
        ;;
    remove)
        remove OUTPUT "$rule_out"
        remove PREROUTING "$rule_in"
        ;;
    status)
        "$IPTABLES" -w -t raw -S | grep -- "$COMMENT" || echo "no ndm-scanner rules are installed"
        ;;
    *)
        echo "usage: $0 [install|remove|status]" >&2
        exit 2
        ;;
esac
