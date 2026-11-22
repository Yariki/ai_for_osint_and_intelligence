"""Start the scanner on the interpreter that suits the container's capabilities.

The scanner image has a copy of the interpreter, ``python-netraw``, that carries
cap_net_raw as an effective file capability. The kernel refuses to run such a
file when the container's bounding set lacks NET_RAW ("operation not
permitted"), so a scanner started without the capability would not start at
all, instead of falling back to connect scanning (check 1B.11).

This launcher runs on the ordinary interpreter, reads the bounding set, and
replaces itself with ``python-netraw`` only when NET_RAW is in it. Otherwise it
starts the scanner on the ordinary interpreter, which then reports that it
cannot SYN-scan.

    python -m ndm.scanner.start
"""

import os
import sys

CAP_NET_RAW = 13
NETRAW_PYTHON = "/usr/local/bin/python-netraw"


def bounding_set_has(status: str, capability: int) -> bool:
    """Whether the ``CapBnd`` line of a /proc/<pid>/status text holds ``capability``."""
    for line in status.splitlines():
        if line.startswith("CapBnd:"):
            return bool(int(line.split()[1], 16) >> capability & 1)
    return False


def interpreter(status: str) -> str:
    """The interpreter to run the scanner with."""
    if bounding_set_has(status, CAP_NET_RAW) and os.path.exists(NETRAW_PYTHON):
        return NETRAW_PYTHON
    return sys.executable


def main() -> None:
    with open("/proc/self/status") as status:
        python = interpreter(status.read())
    os.execv(python, [python, "-m", "ndm.scanner.main"])


if __name__ == "__main__":
    main()
