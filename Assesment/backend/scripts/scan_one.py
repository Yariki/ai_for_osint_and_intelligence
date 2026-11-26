"""Development only: run the scanner's worker on one address, without the broker.

It builds one chunk for the address, gives it to the same ``Worker`` the
scanner process uses (scope check included), and prints every message the
worker would publish, as JSON lines. No broker, orchestrator or database.

In the dev scanner container (host network, NET_RAW, the dev settings), from
Assesment/:

    docker compose -f deploy/compose.dev.yaml run --rm \
        -v "$PWD/backend/scripts:/scripts:ro" scanner \
        python-netraw /scripts/scan_one.py 10.77.3.10 --ports 22,80,443 --syn

Options:
    --ports       a port list (22,80,8000-8100) or a profile (top-100, top-1000, full);
                  default top-100
    --syn         SYN scan (needs NET_RAW); otherwise connect scan
    --discovery   a discovery chunk (ICMP echo and TCP probes to --ports) instead of a port scan
    --pps, --timeout-ms, --retries   as in a scan's settings

A non-public address (the lab, 127.0.0.1) needs NDM_ALLOW_NON_PUBLIC_TARGETS=true,
which the dev stack sets.
"""

import argparse
import asyncio
import os
from ipaddress import IPv4Address

from ndm.core.messages import Chunk, ChunkPhase, Discovery, Message, Technique
from ndm.core.ports import PortProfile, parse_port_list, profile_ports
from ndm.core.settings import Settings
from ndm.scanner.main import Worker


def ports(text: str) -> list[int]:
    if text in set(PortProfile):
        return list(profile_ports(PortProfile(text)))
    return parse_port_list(text)


def build_chunk(args: argparse.Namespace) -> Chunk:
    target = IPv4Address(args.address)
    port_list = ports(args.ports)
    phase = ChunkPhase.DISCOVERY if args.discovery else ChunkPhase.PORT_SCAN
    return Chunk(
        scan_id=1,
        chunk_seq=1,
        phase=phase,
        technique=Technique.SYN if args.syn else Technique.CONNECT,
        targets=[target],
        ports=[] if args.discovery else port_list,
        discovery=Discovery(icmp_echo=True, tcp_ports=port_list) if args.discovery else None,
        pps=args.pps,
        timeout_ms=args.timeout_ms,
        retries=args.retries,
        seed=1,
        attestation_id=1,
        # The scope is the one address: the worker's own scope check still runs.
        scope=[(target, target)],
    )


async def scan(chunk: Chunk) -> None:
    async def publish(message: Message) -> None:
        print(message.model_dump_json(), flush=True)

    worker = Worker(Settings(), publish)
    worker.open_sockets()
    print(f"# syn_capable={worker.syn_capable} icmp_capable={worker.icmp_capable}", flush=True)
    try:
        await worker.handle_chunk(chunk)
    finally:
        worker.close()


def main() -> None:
    # Set by the VS Code task that debugs this script in the scanner container
    # (.vscode/tasks.json): wait here until the debugger attaches.
    if debug_port := os.environ.get("SCAN_ONE_DEBUG_PORT"):
        import debugpy

        debugpy.listen(("127.0.0.1", int(debug_port)))
        print(f"debugpy listening on 127.0.0.1:{debug_port}", flush=True)
        debugpy.wait_for_client()

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("address", nargs="?", default="")
    parser.add_argument("--ports", default="top-100")
    parser.add_argument("--syn", action="store_true")
    parser.add_argument("--discovery", action="store_true")
    parser.add_argument("--pps", type=int, default=1000)
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--retries", type=int, default=1)
    asyncio.run(scan(build_chunk(parser.parse_args())))


if __name__ == "__main__":
    main()
