"""Probe count and duration of a port scan, shown before launch (FR-CFG-9)."""

from dataclasses import dataclass

from ndm.core.ports import PortProfile, profile_ports


@dataclass(frozen=True)
class Estimate:
    probes: int
    """Probes before retries: one per host and port."""
    max_probes: int
    """Probes if no port answers and every probe is repeated ``retries`` times."""
    seconds: float
    """Sending time for ``probes`` at the scan's rate."""
    max_seconds: float
    """Sending time for ``max_probes`` at the scan's rate."""


def probe_count(hosts: int, ports: int) -> int:
    """Probes of a port scan before retries."""
    if hosts < 0 or ports < 0:
        raise ValueError("hosts and ports must not be negative")
    return hosts * ports


def estimate(hosts: int, ports: int, rate_pps: int, retries: int) -> Estimate:
    """Estimate a scan of ``hosts`` hosts on ``ports`` ports each.

    Only an unanswered probe is repeated, so the real numbers lie between the
    plain and the ``max_`` values: near the plain ones when closed ports answer
    with a reset, near the maximum against hosts that drop everything.
    """
    if rate_pps <= 0:
        raise ValueError("the rate must be positive")
    if retries < 0:
        raise ValueError("retries must not be negative")
    probes = probe_count(hosts, ports)
    max_probes = probes * (1 + retries)
    return Estimate(
        probes=probes,
        max_probes=max_probes,
        seconds=probes / rate_pps,
        max_seconds=max_probes / rate_pps,
    )


def estimate_profile(hosts: int, profile: PortProfile, rate_pps: int, retries: int) -> Estimate:
    """Estimate a scan with one of the built-in port profiles."""
    return estimate(hosts, len(profile_ports(profile)), rate_pps, retries)
