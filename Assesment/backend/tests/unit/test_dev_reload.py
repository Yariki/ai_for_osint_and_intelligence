"""scripts/dev_reload.py: the reloader of the dev stack's debug overlay."""

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev_reload.py"
_spec = importlib.util.spec_from_file_location("dev_reload", SCRIPT)
assert _spec is not None and _spec.loader is not None
dev_reload: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dev_reload)


def test_parse_splits_the_directories_from_the_command() -> None:
    roots, command = dev_reload.parse(["/src", "/lib", "--", "python", "-m", "x", "--", "y"])

    assert roots == ["/src", "/lib"]
    assert command == ["python", "-m", "x", "--", "y"]


@pytest.mark.parametrize("argv", [["/src"], ["--", "python"], ["/src", "--"]])
def test_parse_refuses_a_missing_part(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        dev_reload.parse(argv)


def test_snapshot_sees_a_changed_source_file_and_ignores_bytecode(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "module.py"
    source.parent.mkdir()
    source.write_text("x = 1\n")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "module.py").write_text("")
    (tmp_path / "pkg" / "notes.md").write_text("")

    before = dev_reload.snapshot([str(tmp_path)])
    os.utime(source, ns=(0, before[str(source)] + 1_000_000_000))
    after = dev_reload.snapshot([str(tmp_path)])

    assert list(before) == [str(source)]
    assert after != before


def wait_for(predicate: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.1)


def test_a_changed_file_restarts_the_command_and_sigterm_stops_it(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("x = 1\n")
    starts = tmp_path / "starts.log"
    # Each start appends its process id, then runs until it is stopped.
    child = (
        f"import os, time; open({str(starts)!r}, 'a').write(f'{{os.getpid()}}\\n'); time.sleep(60)"
    )
    reloader = subprocess.Popen(
        [sys.executable, str(SCRIPT), str(tmp_path), "--", sys.executable, "-c", child]
    )
    try:
        wait_for(lambda: starts.exists() and len(starts.read_text().split()) == 1)
        source.write_text("x = 2\n")
        os.utime(source, ns=(0, time.time_ns() + 1_000_000_000))
        wait_for(lambda: len(starts.read_text().split()) == 2)

        reloader.send_signal(signal.SIGTERM)
        assert reloader.wait(15) == 0
    finally:
        reloader.kill()

    first, second = (int(pid) for pid in starts.read_text().split())
    for pid in (first, second):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
