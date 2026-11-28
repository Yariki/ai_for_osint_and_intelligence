"""Run a command, and run it again whenever a source file changes.

For the debug overlay of the dev stack (deploy/compose.debug.yaml) only. It is
started under debugpy, which stays in this process and follows every child it
starts: an attached debugger survives a reload, and breakpoints apply to the
new process at once. Compose's "--watch" restarts the whole container
instead, which would end the debug session.

    python scripts/dev_reload.py /src -- python -m ndm.orchestrator.main

Standard library only: the images have no watchfiles, and the dependencies
stay as they are locked. Files are polled, which also works on a bind mount.
"""

import os
import signal
import subprocess
import sys
import time
from types import FrameType

POLL_SECONDS = 1.0
STOP_TIMEOUT_SECONDS = 10.0
WATCHED_SUFFIXES = (".py", ".yaml", ".csv", ".txt")


def parse(argv: list[str]) -> tuple[list[str], list[str]]:
    """The directories to watch and the command, from ``DIR... -- COMMAND...``."""
    if "--" not in argv:
        raise SystemExit("usage: dev_reload.py DIR... -- COMMAND...")
    split = argv.index("--")
    roots, command = argv[:split], argv[split + 1 :]
    if not roots or not command:
        raise SystemExit("usage: dev_reload.py DIR... -- COMMAND...")
    return roots, command


def snapshot(roots: list[str]) -> dict[str, int]:
    """The modification time of every watched file under ``roots``."""
    mtimes: dict[str, int] = {}
    for root in roots:
        for directory, subdirectories, names in os.walk(root):
            subdirectories[:] = [name for name in subdirectories if name != "__pycache__"]
            for name in names:
                if name.endswith(WATCHED_SUFFIXES):
                    path = os.path.join(directory, name)
                    try:
                        mtimes[path] = os.stat(path).st_mtime_ns
                    except FileNotFoundError:
                        pass
    return mtimes


def stop(process: subprocess.Popen[bytes]) -> None:
    """Ask the process to stop, and kill it if it has not after a while."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def log(message: str) -> None:
    print(f"dev_reload: {message}", flush=True)


def on_stop_signal(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(0)


def main() -> None:
    roots, command = parse(sys.argv[1:])
    # As process 1 of a container, Python would ignore SIGTERM without a handler.
    signal.signal(signal.SIGTERM, on_stop_signal)

    process = subprocess.Popen(command)
    seen = snapshot(roots)
    exit_reported = False
    try:
        while True:
            time.sleep(POLL_SECONDS)
            current = snapshot(roots)
            if current != seen:
                seen = current
                log("a source file changed, restarting")
                stop(process)
                process = subprocess.Popen(command)
                exit_reported = False
            elif process.poll() is not None and not exit_reported:
                log(f"exited with status {process.returncode}; waiting for a change")
                exit_reported = True
    except KeyboardInterrupt:
        pass
    finally:
        stop(process)


if __name__ == "__main__":
    main()
