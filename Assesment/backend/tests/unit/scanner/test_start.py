"""The scanner launcher picks python-netraw only when NET_RAW is in the bounding set."""

import os
import sys

import pytest

from ndm.scanner import start

# CapBnd of a container with every default Docker capability (NET_RAW included).
DEFAULT = "Name:\tpython\nCapEff:\t0000000000000000\nCapBnd:\t00000000a80425fb\n"
# Compose's scanner: cap_drop ALL, cap_add NET_RAW.
NET_RAW_ONLY = "CapBnd:\t0000000000002000\n"
# cap_drop NET_RAW (check 1B.11), or cap_drop ALL without cap_add.
WITHOUT = "CapBnd:\t00000000a80405fb\n"
NONE = "CapBnd:\t0000000000000000\n"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(DEFAULT, True), (NET_RAW_ONLY, True), (WITHOUT, False), (NONE, False), ("", False)],
)
def test_bounding_set_has_net_raw(status: str, expected: bool) -> None:
    assert start.bounding_set_has(status, start.CAP_NET_RAW) is expected


def test_with_net_raw_the_capable_interpreter_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os.path, "exists", lambda path: path == start.NETRAW_PYTHON)

    assert start.interpreter(NET_RAW_ONLY) == start.NETRAW_PYTHON


def test_without_net_raw_the_ordinary_interpreter_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os.path, "exists", lambda path: True)

    assert start.interpreter(WITHOUT) == sys.executable


def test_without_the_capable_copy_the_ordinary_interpreter_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside the scanner image there is no python-netraw."""
    monkeypatch.setattr(os.path, "exists", lambda path: False)

    assert start.interpreter(NET_RAW_ONLY) == sys.executable


def test_main_runs_the_scanner_on_the_chosen_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(start, "interpreter", lambda status: "/chosen/python")
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))

    start.main()

    assert calls == [("/chosen/python", ["/chosen/python", "-m", "ndm.scanner.main"])]
