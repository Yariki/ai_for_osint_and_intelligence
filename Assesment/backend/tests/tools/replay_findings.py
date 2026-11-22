"""The findings-replay harness: a scan of public addresses with no packet sent.

Lab targets are in private address space, and no reference dataset knows them.
To test enrichment and the map, a scan must find hosts at public addresses.
This harness takes the scanner's place on the broker and answers every chunk
from a recorded description of which hosts exist, so a scan of public ranges
runs from the queue to completion while nothing is probed.

On the lab host, with the product stack running:

    docker compose stop scanner          # the real scanner must not run
    cd Assesment/backend
    NDM_BROKER_URL=amqp://ndm:<password>@127.0.0.1:5672/ \\
        uv run python -m tests.tools.replay_findings --vantage-point lab --delay 0.3

Then give a project the scope of the recorded scan (printed at start) and
launch a scan of it. The harness refuses to start while a scanner consumes the
chunk queue: a real scanner would probe the addresses.

In tests the same recorded scan is used without a broker: ``load()`` gives the
world, and the stub scanner's ``respond`` turns chunks into results.
"""

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any

import aio_pika

from ndm.core import broker
from tests.tools.stub_scanner import StubScanner, World

PUBLIC_SCAN = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "public_scan.json"
HEARTBEAT_SECONDS = 10


@dataclass(frozen=True)
class ReplayScan:
    scope: list[str]
    """The ranges to put into the project scope and to scan."""
    world: World
    """Which addresses have a host, and what it answers."""
    reference: dict[str, dict[str, Any]]
    """Made-up reference data per network, for tests that build their own datasets."""

    @property
    def targets(self) -> list[IPv4Address]:
        return [address for network in self.scope for address in IPv4Network(network)]


def load(path: Path = PUBLIC_SCAN) -> ReplayScan:
    data = json.loads(path.read_text())
    return ReplayScan(scope=data["scope"], world=data["world"], reference=data["reference"])


class ScannerPresent(Exception):
    """A scanner is consuming the chunk queue. Replaying beside it would let it probe."""


async def start(url: str, vantage_point: str, world: World, delay: float = 0.0) -> StubScanner:
    """Take the scanner's place on the broker. Returns the running harness."""
    connection = await aio_pika.connect_robust(url)
    try:
        channel = await connection.channel()
        await broker.declare_topology(channel, vantage_point)
        queue = await channel.declare_queue(broker.chunk_queue(vantage_point), passive=True)
        if queue.declaration_result.consumer_count:
            raise ScannerPresent(
                f"A scanner is consuming the chunks of vantage point '{vantage_point}'. "
                "Stop it first: it would send real probes to the replayed addresses."
            )
    finally:
        await connection.close()
    harness = StubScanner(url=url, world=world, vantage_point=vantage_point, delay=delay)
    await harness.start()
    return harness


async def run(url: str, vantage_point: str, scan: ReplayScan, delay: float) -> None:
    harness = await start(url, vantage_point, scan.world, delay)
    print(f"Replaying as vantage point '{vantage_point}'. Scope to scan: {', '.join(scan.scope)}")
    try:
        while True:
            await harness.heartbeat()
            await asyncio.sleep(HEARTBEAT_SECONDS)
    finally:
        await harness.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vantage-point", default="default")
    parser.add_argument("--delay", type=float, default=0.3, help="seconds before each result")
    parser.add_argument("--recorded", type=Path, default=PUBLIC_SCAN)
    arguments = parser.parse_args()
    url = os.environ.get("NDM_BROKER_URL")
    if not url:
        parser.error("set NDM_BROKER_URL to the broker as this host reaches it")
    try:
        asyncio.run(run(url, arguments.vantage_point, load(arguments.recorded), arguments.delay))
    except ScannerPresent as refusal:
        raise SystemExit(str(refusal)) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
