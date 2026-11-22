from ipaddress import IPv4Address

import pytest

from ndm.core import scope
from ndm.core.scope import (
    ScopeEntry,
    ScopeError,
    ScopeRejected,
    check_entry,
    check_target,
    count_addresses,
    covers,
    parse_address,
    parse_entry,
    parse_never_scan,
    validate_scope,
)


def entries(*texts: str) -> list[ScopeEntry]:
    return [parse_entry(text) for text in texts]


# --- Parsing of valid forms ---


def test_single_address() -> None:
    entry = parse_entry("8.8.8.8")

    assert entry == ScopeEntry("address", "8.8.8.8", IPv4Address("8.8.8.8"), IPv4Address("8.8.8.8"))
    assert entry.size == 1


def test_cidr_block() -> None:
    entry = parse_entry("8.8.8.0/24")

    assert (entry.kind, entry.text) == ("cidr", "8.8.8.0/24")
    assert (str(entry.start), str(entry.end)) == ("8.8.8.0", "8.8.8.255")
    # A /24 counts as 256 addresses, including .0 and .255 (FR-CFG-1).
    assert entry.size == 256


def test_range() -> None:
    entry = parse_entry("8.8.4.250-8.8.5.10")

    assert (entry.kind, entry.text) == ("range", "8.8.4.250-8.8.5.10")
    assert (str(entry.start), str(entry.end)) == ("8.8.4.250", "8.8.5.10")
    assert entry.size == 17


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("0.0.0.0/0", "0.0.0.0", "255.255.255.255"),
        ("8.8.8.8/32", "8.8.8.8", "8.8.8.8"),
        ("8.8.8.8/31", "8.8.8.8", "8.8.8.9"),
        ("8.8.8.8-8.8.8.8", "8.8.8.8", "8.8.8.8"),
        ("255.255.255.255", "255.255.255.255", "255.255.255.255"),
    ],
)
def test_edge_forms_are_parsed(text: str, start: str, end: str) -> None:
    entry = parse_entry(text)

    assert (str(entry.start), str(entry.end)) == (start, end)


# --- Check 1A.2: strict parsing (NFR-SEC-5) ---

MALFORMED = [
    # Host bits set.
    "8.8.8.1/24",
    "8.8.8.4/29",
    "8.8.8.128/24",
    # Leading zeros.
    "08.8.8.8",
    "8.8.8.008",
    "010.1.1.1",
    "8.8.8.0/024",
    "8.8.8.0/00",
    "8.8.8.1-8.8.8.010",
    # Reversed range.
    "8.8.8.10-8.8.8.1",
    "9.0.0.0-8.255.255.255",
    # Mixed notations.
    "8.8.8.0/24-8.8.9.0",
    "8.8.8.0-8.8.9.0/24",
    "8.8.8.0/255.255.255.0",
    "8.8.8.1-10",
    "8.8.8.1-8.8.8.5-8.8.8.9",
    "8.8.8.0/24/25",
    # Not dotted decimal with four parts.
    "8.8.8",
    "8.8.8.8.8",
    "8.8.8.",
    ".8.8.8",
    "8..8.8",
    "134744072",
    "0x8.8.8.8",
    "8.8.8.8e0",
    "+8.8.8.8",
    "8.8.8.-8",
    "::ffff:8.8.8.8",
    "example.org",
    # Out of range.
    "8.8.8.256",
    "8.8.8.999",
    "8.8.8.1000",
    "8.8.8.0/33",
    "8.8.8.0/-1",
    "8.8.8.0/",
    "/24",
    "8.8.8.8-",
    "-8.8.8.8",
    # Whitespace is not trimmed.
    "",
    " ",
    " 8.8.8.8",
    "8.8.8.8 ",
    "8.8.8.8\n",
    "8.8.8.1 - 8.8.8.5",
    "8.8.8.0 /24",
    "8.8.8.8,8.8.4.4",
    # Digits that are not ASCII.
    "８.8.8.8",
    "8.8.8.٨",
    "8.8.8.0/２4",
    # En dash instead of a hyphen.
    "8.8.8.1–8.8.8.5",
]


@pytest.mark.parametrize("text", MALFORMED)
def test_malformed_entry_is_rejected(text: str) -> None:
    with pytest.raises(ScopeError) as caught:
        parse_entry(text)

    assert caught.value.code == scope.MALFORMED
    assert caught.value.entry == text


@pytest.mark.parametrize("text", ["8.8.8.0/24", "8.8.8.1-8.8.8.5", "08.8.8.8", "8.8.8.8 ", ""])
def test_parse_address_accepts_a_single_address_only(text: str) -> None:
    with pytest.raises(ScopeError):
        parse_address(text)


def test_parse_address() -> None:
    assert parse_address("8.8.4.4") == IPv4Address("8.8.4.4")


def test_valid_entry_is_never_normalized() -> None:
    # The original text is kept exactly; nothing is rewritten into another form.
    assert parse_entry("8.8.8.0-8.8.8.255").kind == "range"
    assert parse_entry("8.8.8.0-8.8.8.255").text == "8.8.8.0-8.8.8.255"


# --- Check 1A.1: public-address rule ---

# Written by hand, independently of the table in the module:
# (block, first, last, public address just before, public address just after).
# None where there is no address on that side or it belongs to another block.
BOUNDARIES = [
    ("0.0.0.0/8", "0.0.0.0", "0.255.255.255", None, "1.0.0.0"),
    ("10.0.0.0/8", "10.0.0.0", "10.255.255.255", "9.255.255.255", "11.0.0.0"),
    ("100.64.0.0/10", "100.64.0.0", "100.127.255.255", "100.63.255.255", "100.128.0.0"),
    ("127.0.0.0/8", "127.0.0.0", "127.255.255.255", "126.255.255.255", "128.0.0.0"),
    ("169.254.0.0/16", "169.254.0.0", "169.254.255.255", "169.253.255.255", "169.255.0.0"),
    ("172.16.0.0/12", "172.16.0.0", "172.31.255.255", "172.15.255.255", "172.32.0.0"),
    ("192.0.0.0/24", "192.0.0.0", "192.0.0.255", "191.255.255.255", "192.0.1.0"),
    ("192.0.2.0/24", "192.0.2.0", "192.0.2.255", "192.0.1.255", "192.0.3.0"),
    ("192.88.99.0/24", "192.88.99.0", "192.88.99.255", "192.88.98.255", "192.88.100.0"),
    ("192.168.0.0/16", "192.168.0.0", "192.168.255.255", "192.167.255.255", "192.169.0.0"),
    ("198.18.0.0/15", "198.18.0.0", "198.19.255.255", "198.17.255.255", "198.20.0.0"),
    ("198.51.100.0/24", "198.51.100.0", "198.51.100.255", "198.51.99.255", "198.51.101.0"),
    ("203.0.113.0/24", "203.0.113.0", "203.0.113.255", "203.0.112.255", "203.0.114.0"),
    ("224.0.0.0/4", "224.0.0.0", "239.255.255.255", "223.255.255.255", None),
    ("240.0.0.0/4", "240.0.0.0", "255.255.255.255", None, None),
]


def is_rejected_as_non_public(text: str) -> bool:
    try:
        check_entry(parse_entry(text), never_scan=[], allow_non_public=False)
    except ScopeError as error:
        assert error.code == scope.NON_PUBLIC
        return True
    return False


def test_the_table_holds_exactly_the_expected_blocks() -> None:
    assert sorted(block.text for block, _ in scope.NON_PUBLIC_RANGES) == sorted(
        row[0] for row in BOUNDARIES
    )


@pytest.mark.parametrize(("block", "first", "last", "before", "after"), BOUNDARIES)
def test_non_public_block_boundaries(
    block: str, first: str, last: str, before: str | None, after: str | None
) -> None:
    assert is_rejected_as_non_public(first)
    assert is_rejected_as_non_public(last)
    assert is_rejected_as_non_public(block)
    assert is_rejected_as_non_public(f"{first}-{last}")
    if before is not None:
        assert not is_rejected_as_non_public(before)
    if after is not None:
        assert not is_rejected_as_non_public(after)


@pytest.mark.parametrize(("block", "first", "last", "before", "after"), BOUNDARIES)
def test_partial_overlap_is_rejected_as_a_whole(
    block: str, first: str, last: str, before: str | None, after: str | None
) -> None:
    # A range that is public except for one address at its end.
    if before is not None:
        assert is_rejected_as_non_public(f"{before}-{first}")
    if after is not None:
        assert is_rejected_as_non_public(f"{last}-{after}")


@pytest.mark.parametrize(
    "text",
    [
        "10.0.0.0/7",  # 10.0.0.0 - 11.255.255.255
        "192.167.0.0-192.169.0.0",  # spans 192.168.0.0/16
        "0.0.0.0/0",
        "128.0.0.0/1",  # holds multicast and reserved space
        "100.0.0.0/8",  # holds the CGNAT block
        "255.255.255.255",  # broadcast
    ],
)
def test_entry_that_contains_non_public_space_is_rejected(text: str) -> None:
    assert is_rejected_as_non_public(text)


@pytest.mark.parametrize("text", ["8.8.8.0/24", "1.0.0.0-9.255.255.255", "193.0.0.0/8"])
def test_public_entry_is_accepted(text: str) -> None:
    assert not is_rejected_as_non_public(text)


def test_switch_allows_non_public_entries() -> None:
    check_entry(parse_entry("10.0.0.0/24"), never_scan=[], allow_non_public=True)


# --- Never-scan list ---


def test_never_scan_address_is_rejected() -> None:
    never_scan = parse_never_scan(["8.8.8.8", "9.9.9.0/24"])

    for text in ["8.8.8.8", "8.8.8.0/24", "8.8.8.1-8.8.8.9", "9.9.9.255", "9.9.8.0/23"]:
        with pytest.raises(ScopeError) as caught:
            check_entry(parse_entry(text), never_scan=never_scan, allow_non_public=False)
        assert caught.value.code == scope.NEVER_SCAN

    check_entry(parse_entry("8.8.8.9"), never_scan=never_scan, allow_non_public=False)


def test_never_scan_list_applies_with_the_switch_on() -> None:
    with pytest.raises(ScopeError) as caught:
        check_entry(
            parse_entry("10.0.0.5"), never_scan=entries("10.0.0.0/24"), allow_non_public=True
        )

    assert caught.value.code == scope.NEVER_SCAN


def test_invalid_never_scan_entry_is_an_error() -> None:
    with pytest.raises(ScopeError):
        parse_never_scan(["8.8.8.8", "8.8.8.1/24"])


# --- Whole scope and ceiling ---


def test_valid_scope_is_returned_in_order() -> None:
    result = validate_scope(
        ["8.8.8.0/24", "1.1.1.1", "9.9.9.1-9.9.9.9"],
        never_scan=[],
        ceiling=65_536,
        allow_non_public=False,
    )

    assert [entry.text for entry in result] == ["8.8.8.0/24", "1.1.1.1", "9.9.9.1-9.9.9.9"]


def test_every_bad_entry_is_reported_with_its_position() -> None:
    with pytest.raises(ScopeRejected) as caught:
        validate_scope(
            ["8.8.8.0/24", "10.0.0.0/8", "8.8.8.1/24", "9.9.9.9"],
            never_scan=entries("9.9.9.9"),
            ceiling=65_536,
            allow_non_public=False,
        )

    assert [(error.index, error.code, error.entry) for error in caught.value.errors] == [
        (1, scope.NON_PUBLIC, "10.0.0.0/8"),
        (2, scope.MALFORMED, "8.8.8.1/24"),
        (3, scope.NEVER_SCAN, "9.9.9.9"),
    ]


def test_count_addresses_counts_each_address_once() -> None:
    assert count_addresses([]) == 0
    assert count_addresses(entries("8.8.8.0/24", "8.8.8.0/25", "8.8.8.200-8.8.9.9")) == 266
    assert count_addresses(entries("1.0.0.0/8")) == 16_777_216


def test_scope_at_the_ceiling_is_accepted_and_one_above_is_rejected() -> None:
    at_ceiling = ["8.8.0.0/16"]  # 65,536 addresses
    validate_scope(at_ceiling, never_scan=[], ceiling=65_536, allow_non_public=False)

    with pytest.raises(ScopeRejected) as caught:
        validate_scope(
            [*at_ceiling, "9.9.9.9"], never_scan=[], ceiling=65_536, allow_non_public=False
        )

    (error,) = caught.value.errors
    assert (error.code, error.index) == (scope.CEILING_EXCEEDED, None)


def test_overlapping_entries_do_not_count_twice_towards_the_ceiling() -> None:
    validate_scope(
        ["8.8.8.0/24", "8.8.8.0/24", "8.8.8.0-8.8.8.255"],
        never_scan=[],
        ceiling=256,
        allow_non_public=False,
    )


def test_empty_scope_is_valid() -> None:
    assert validate_scope([], never_scan=[], ceiling=1, allow_non_public=False) == []


# --- Containment of a target in a scope ---


def test_covers() -> None:
    project_scope = entries("8.8.8.0/24", "8.8.9.0/24", "1.1.1.1")

    assert covers(project_scope, parse_entry("8.8.8.77"))
    assert covers(project_scope, parse_entry("8.8.8.0/25"))
    assert covers(project_scope, parse_entry("1.1.1.1"))
    # Two adjacent entries cover a target that spans both.
    assert covers(project_scope, parse_entry("8.8.8.0/23"))
    assert covers(project_scope, parse_entry("8.8.8.250-8.8.9.5"))

    assert not covers(project_scope, parse_entry("8.8.10.0"))
    assert not covers(project_scope, parse_entry("8.8.7.255-8.8.8.1"))
    assert not covers(project_scope, parse_entry("8.8.8.0/22"))
    assert not covers(project_scope, parse_entry("1.1.1.0/31"))


def test_empty_scope_covers_nothing() -> None:
    assert not covers([], parse_entry("8.8.8.8"))


def test_check_target() -> None:
    project_scope = entries("8.8.8.0/24", "10.0.0.0/24")
    never_scan = entries("8.8.8.8")

    def code(text: str, allow_non_public: bool = False) -> str | None:
        try:
            check_target(
                parse_entry(text),
                scope=project_scope,
                never_scan=never_scan,
                allow_non_public=allow_non_public,
            )
        except ScopeError as error:
            return error.code
        return None

    assert code("8.8.8.1") is None
    assert code("8.8.8.0/25") == scope.NEVER_SCAN  # holds 8.8.8.8
    assert code("8.8.8.8") == scope.NEVER_SCAN
    assert code("8.8.4.4") == scope.OUT_OF_SCOPE
    assert code("8.8.8.128-8.8.9.1") == scope.OUT_OF_SCOPE
    # In the scope, but non-public: allowed only with the test switch.
    assert code("10.0.0.5") == scope.NON_PUBLIC
    assert code("10.0.0.5", allow_non_public=True) is None
    assert code("10.0.1.5", allow_non_public=True) == scope.OUT_OF_SCOPE
