"""Scope rules: which IPv4 addresses may be scanned.

Pure functions with no I/O. The API, the orchestrator and the scanner all use
this module, so it imports nothing from ``ndm.db`` or ``ndm.api`` (C-3). The
never-scan list, the ceiling and the allow-non-public switch are parameters:
each caller passes its own configuration.

Everything here fails closed. Input that is malformed or ambiguous raises
``ScopeError``; nothing is repaired or normalized (NFR-SEC-5).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import Literal

Kind = Literal["address", "cidr", "range"]

MALFORMED = "malformed"
NON_PUBLIC = "non_public"
NEVER_SCAN = "never_scan"
CEILING_EXCEEDED = "ceiling_exceeded"
OUT_OF_SCOPE = "out_of_scope"


class ScopeError(ValueError):
    """One reason why an entry or a target is not acceptable."""

    def __init__(self, code: str, message: str, entry: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.entry = entry
        # Position of the entry in the submitted scope; set by validate_scope.
        self.index: int | None = None


class ScopeRejected(ValueError):
    """A scope was rejected. Holds every reason, so each can be shown on its entry."""

    def __init__(self, errors: list[ScopeError]) -> None:
        super().__init__("; ".join(error.message for error in errors))
        self.errors = errors


@dataclass(frozen=True)
class ScopeEntry:
    """A single address, a CIDR block or a start-end range, both ends included."""

    kind: Kind
    text: str
    start: IPv4Address
    end: IPv4Address

    @property
    def size(self) -> int:
        return int(self.end) - int(self.start) + 1


# --- Strict parsing ---

# ASCII digits only, no leading zeros: "01" could be read as octal or decimal.
_OCTET = r"(0|[1-9][0-9]{0,2})"
_ADDRESS_RE = re.compile(rf"{_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}")
_PREFIX_RE = re.compile(r"0|[1-9][0-9]?")


def _malformed(text: str, reason: str) -> ScopeError:
    return ScopeError(MALFORMED, f"{text!r} is not valid: {reason}", entry=text)


def _address(text: str, entry: str) -> IPv4Address:
    match = _ADDRESS_RE.fullmatch(text)
    if match is None:
        raise _malformed(
            entry, "an address must be four decimal numbers separated by dots, no leading zeros"
        )
    octets = [int(group) for group in match.groups()]
    if any(octet > 255 for octet in octets):
        raise _malformed(entry, "an address part is above 255")
    return IPv4Address(bytes(octets))


def parse_address(text: str) -> IPv4Address:
    """Parse one IPv4 address in dotted-decimal form."""
    return _address(text, text)


def parse_entry(text: str) -> ScopeEntry:
    """Parse ``a.b.c.d``, ``a.b.c.d/n`` or ``a.b.c.d-e.f.g.h``.

    Surrounding whitespace, host bits set in a CIDR block, leading zeros,
    a reversed range and mixed notations are errors.
    """
    if "/" in text and "-" in text:
        raise _malformed(text, "CIDR and range notation cannot be mixed")

    if "/" in text:
        address_text, _, prefix_text = text.partition("/")
        start = _address(address_text, text)
        if _PREFIX_RE.fullmatch(prefix_text) is None or int(prefix_text) > 32:
            raise _malformed(text, "the prefix length must be a number from 0 to 32")
        size = 1 << (32 - int(prefix_text))
        if int(start) % size != 0:
            raise _malformed(text, "the address has host bits set for this prefix length")
        return ScopeEntry("cidr", text, start, IPv4Address(int(start) + size - 1))

    if "-" in text:
        start_text, _, end_text = text.partition("-")
        start = _address(start_text, text)
        end = _address(end_text, text)
        if start > end:
            raise _malformed(text, "the range ends before it starts")
        return ScopeEntry("range", text, start, end)

    address = _address(text, text)
    return ScopeEntry("address", text, address, address)


# --- Non-public address space ---

# An explicit table. ipaddress.is_private and is_global are not used: what they
# mean has changed between Python versions.
_NON_PUBLIC = [
    # The ranges of FR-PRJ-3.
    ("0.0.0.0/8", "this network"),
    ("10.0.0.0/8", "private use"),
    ("100.64.0.0/10", "carrier-grade NAT"),
    ("127.0.0.0/8", "loopback"),
    ("169.254.0.0/16", "link-local"),
    ("172.16.0.0/12", "private use"),
    ("192.0.2.0/24", "documentation"),
    ("192.168.0.0/16", "private use"),
    ("198.51.100.0/24", "documentation"),
    ("203.0.113.0/24", "documentation"),
    ("224.0.0.0/4", "multicast"),
    ("240.0.0.0/4", "reserved, including broadcast"),
    # Further IANA special-purpose blocks.
    ("192.0.0.0/24", "IETF protocol assignments"),
    ("192.88.99.0/24", "deprecated 6to4 relay anycast"),
    ("198.18.0.0/15", "benchmarking"),
]

NON_PUBLIC_RANGES: tuple[tuple[ScopeEntry, str], ...] = tuple(
    (parse_entry(cidr), name) for cidr, name in _NON_PUBLIC
)


def _overlap(a: ScopeEntry, b: ScopeEntry) -> bool:
    return a.start <= b.end and b.start <= a.end


def non_public_range(entry: ScopeEntry) -> str | None:
    """Name the first non-public range that ``entry`` touches, if any.

    One shared address is enough: an entry that partly overlaps a non-public
    range is not public.
    """
    for block, name in NON_PUBLIC_RANGES:
        if _overlap(entry, block):
            return f"{block.text} ({name})"
    return None


# --- Checks ---


def check_entry(
    entry: ScopeEntry, *, never_scan: Sequence[ScopeEntry], allow_non_public: bool
) -> None:
    """Raise ``ScopeError`` unless every address of ``entry`` may be scanned at all."""
    if not allow_non_public:
        block = non_public_range(entry)
        if block is not None:
            raise ScopeError(
                NON_PUBLIC,
                f"{entry.text!r} is rejected: it overlaps the non-public range {block}",
                entry=entry.text,
            )
    for blocked in never_scan:
        if _overlap(entry, blocked):
            raise ScopeError(
                NEVER_SCAN,
                f"{entry.text!r} is rejected: it overlaps the never-scan entry {blocked.text!r}",
                entry=entry.text,
            )


def parse_never_scan(texts: Sequence[str]) -> list[ScopeEntry]:
    """Parse the operator's never-scan list. Raises ``ScopeError`` on the first bad entry."""
    return [parse_entry(text) for text in texts]


def _merged(entries: Sequence[ScopeEntry]) -> list[tuple[int, int]]:
    """The entries as sorted, disjoint intervals; overlapping and adjacent ones are joined."""
    intervals: list[tuple[int, int]] = []
    for start, end in sorted((int(entry.start), int(entry.end)) for entry in entries):
        if intervals and start <= intervals[-1][1] + 1:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
        else:
            intervals.append((start, end))
    return intervals


def count_addresses(entries: Sequence[ScopeEntry]) -> int:
    """Number of distinct addresses in the entries."""
    return sum(end - start + 1 for start, end in _merged(entries))


def validate_scope(
    texts: Sequence[str],
    *,
    never_scan: Sequence[ScopeEntry],
    ceiling: int,
    allow_non_public: bool,
) -> list[ScopeEntry]:
    """Parse and check a whole project scope.

    Returns the entries in the order given. Raises ``ScopeRejected`` with one
    error per bad entry, and one more if the scope holds more than ``ceiling``
    distinct addresses.
    """
    entries: list[ScopeEntry] = []
    errors: list[ScopeError] = []
    for index, text in enumerate(texts):
        try:
            entry = parse_entry(text)
            check_entry(entry, never_scan=never_scan, allow_non_public=allow_non_public)
        except ScopeError as error:
            error.index = index
            errors.append(error)
        else:
            entries.append(entry)

    if not errors:
        total = count_addresses(entries)
        if total > ceiling:
            errors.append(
                ScopeError(
                    CEILING_EXCEEDED,
                    f"the scope holds {total} addresses; the ceiling is {ceiling}",
                )
            )
    if errors:
        raise ScopeRejected(errors)
    return entries


def covers(scope: Sequence[ScopeEntry], target: ScopeEntry) -> bool:
    """True if every address of ``target`` is inside ``scope``. An empty scope covers nothing."""
    start, end = int(target.start), int(target.end)
    return any(low <= start and end <= high for low, high in _merged(scope))


def check_target(
    target: ScopeEntry,
    *,
    scope: Sequence[ScopeEntry],
    never_scan: Sequence[ScopeEntry],
    allow_non_public: bool,
) -> None:
    """Raise ``ScopeError`` unless ``target`` may be probed under ``scope``.

    The public-address table and the never-scan list are checked again here,
    and not only when the scope was saved: either may have changed since.
    """
    check_entry(target, never_scan=never_scan, allow_non_public=allow_non_public)
    if not covers(scope, target):
        raise ScopeError(
            OUT_OF_SCOPE,
            f"{target.text!r} is rejected: it is not inside the project scope",
            entry=target.text,
        )
