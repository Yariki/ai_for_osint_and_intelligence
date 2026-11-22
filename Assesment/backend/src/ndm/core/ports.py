"""Port profiles and nominal service names.

The data files are in ``ndm/core/data``; their sources and licences are
recorded in ``PROVENANCE.md`` there.
"""

import csv
import enum
import re
from collections.abc import Sequence
from functools import cache
from importlib import resources

FIRST_PORT = 1
LAST_PORT = 65535


class PortProfile(enum.StrEnum):
    TOP_100 = "top-100"
    TOP_1000 = "top-1000"
    FULL = "full"


_LIST_FILES = {PortProfile.TOP_100: "top-100-tcp.txt", PortProfile.TOP_1000: "top-1000-tcp.txt"}


def _data_file(name: str) -> str:
    return resources.files("ndm.core").joinpath("data", name).read_text(encoding="utf-8")


@cache
def _port_list(profile: PortProfile) -> tuple[int, ...]:
    lines = _data_file(_LIST_FILES[profile]).splitlines()
    return tuple(int(line) for line in lines if line and not line.startswith("#"))


_PORT_ITEM = re.compile(r"([1-9][0-9]{0,4})(?:-([1-9][0-9]{0,4}))?")


def parse_port_list(text: str) -> list[int]:
    """Parse a custom port list such as ``22,80,8000-8100``.

    Strict, like address parsing: no spaces, no leading zeros, no reversed
    range. Raises ``ValueError`` and repairs nothing.
    """
    ports: set[int] = set()
    for item in text.split(","):
        match = _PORT_ITEM.fullmatch(item)
        if match is None:
            raise ValueError(f"{item!r} is not a port or a port range")
        low = int(match.group(1))
        high = int(match.group(2) or low)
        if not FIRST_PORT <= low <= high <= LAST_PORT:
            raise ValueError(f"{item!r} is outside 1-65535 or reversed")
        ports.update(range(low, high + 1))
    return sorted(ports)


def profile_ports(profile: PortProfile) -> Sequence[int]:
    """The TCP ports of a profile, in ascending order."""
    if profile is PortProfile.FULL:
        return range(FIRST_PORT, LAST_PORT + 1)
    return _port_list(profile)


@cache
def _service_names() -> dict[int, str]:
    reader = csv.DictReader(_data_file("iana-service-names.csv").splitlines())
    return {int(row["port"]): row["name"] for row in reader}


def service_name(port: int) -> str | None:
    """The IANA-registered service name of a TCP port, if it has one.

    This is the nominal assignment of the port number. It says nothing about
    what actually answers on that port of a host (FR-SCN-3).
    """
    return _service_names().get(port)
