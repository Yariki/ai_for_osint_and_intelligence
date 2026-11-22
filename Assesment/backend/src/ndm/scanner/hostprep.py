"""Start-up checks of the host preparation (architecture section 15.3).

The operator prepares the host once. The scanner verifies what it can:

- the source-port range of SYN probes must lie outside the kernel's ephemeral
  range. If they overlap, a source port could belong to a real connection of
  the host, and the scanner **withdraws its SYN capability**;
- the receive-buffer limit should be large enough for a burst of replies. A
  low limit is a warning: the scan still runs, and dropped replies are counted.
"""

from dataclasses import dataclass, field

# Below this the capture socket's buffer is small for 5,000 replies per second.
RECOMMENDED_RECEIVE_BUFFER = 8 * 1024 * 1024


@dataclass
class HostPreparation:
    syn_allowed: bool
    warnings: list[str] = field(default_factory=list)


def _read_numbers(path: str) -> list[int] | None:
    try:
        with open(path) as file:
            return [int(part) for part in file.read().split()]
    except OSError, ValueError:
        return None


def check_host(source_ports: tuple[int, int], proc: str = "/proc/sys") -> HostPreparation:
    """Check the host's settings against what the scanner needs."""
    result = HostPreparation(syn_allowed=True)

    ephemeral = _read_numbers(f"{proc}/net/ipv4/ip_local_port_range")
    if ephemeral is None or len(ephemeral) != 2:
        # It cannot be verified, so it is not assumed.
        result.syn_allowed = False
        result.warnings.append(
            "net.ipv4.ip_local_port_range cannot be read; SYN scanning is switched off"
        )
    elif source_ports[0] <= ephemeral[1] and ephemeral[0] <= source_ports[1]:
        result.syn_allowed = False
        result.warnings.append(
            f"the source-port range {source_ports[0]}-{source_ports[1]} overlaps "
            f"net.ipv4.ip_local_port_range ({ephemeral[0]}-{ephemeral[1]}); "
            "SYN scanning is switched off"
        )

    limit = _read_numbers(f"{proc}/net/core/rmem_max")
    if limit is None or len(limit) != 1:
        result.warnings.append("net.core.rmem_max cannot be read")
    elif limit[0] < RECOMMENDED_RECEIVE_BUFFER:
        result.warnings.append(
            f"net.core.rmem_max is {limit[0]}; raise it to at least "
            f"{RECOMMENDED_RECEIVE_BUFFER} so that replies are not dropped at the full rate"
        )
    return result
