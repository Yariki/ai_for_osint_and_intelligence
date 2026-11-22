"""What one chunk has found so far.

The probing code records into this; the worker drains the new findings every
500 ms and publishes them (AD4), and reads the counts when the chunk is done.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import IPv4Address

from ndm.core.messages import Evidence, HostCounts, HostFinding


@dataclass
class ChunkResults:
    probes_sent: int = 0
    closed: Counter[IPv4Address] = field(default_factory=Counter)
    filtered: Counter[IPv4Address] = field(default_factory=Counter)
    _new: dict[IPv4Address, HostFinding] = field(default_factory=dict)

    def _finding(self, address: IPv4Address) -> HostFinding:
        return self._new.setdefault(address, HostFinding(address=address))

    def reachable(self, address: IPv4Address, probe: str, rtt_ms: float) -> None:
        """A reply showed that the host is there. ``probe`` is ``icmp`` or ``tcp:<port>``."""
        # The time the reply was received: the 2-second budget of the live view starts here.
        self._finding(address).evidence[probe] = Evidence(at=datetime.now(UTC), rtt_ms=rtt_ms)

    def open_port(self, address: IPv4Address, port: int, rtt_ms: float) -> None:
        self.reachable(address, f"tcp:{port}", rtt_ms)
        self._finding(address).open_ports.append(port)

    def drain(self) -> list[HostFinding]:
        """The findings since the last call."""
        findings, self._new = list(self._new.values()), {}
        return findings

    def counts(self, targets: list[IPv4Address]) -> list[HostCounts]:
        return [
            HostCounts(address=target, closed=self.closed[target], filtered=self.filtered[target])
            for target in targets
        ]
