"""Lab target for check 5.5: every address of a /24 answers on every TCP port.

- A local route takes the whole network: the kernel accepts packets to every
  address of it and answers ICMP echo for each.
- A NAT rule sends every TCP connection to that network to one listening
  socket. The kernel completes the handshake for any port, so a SYN scan sees
  SYN-ACK and a connect scan a connection, on all 65,535 ports.
- The listener accepts and closes at once; nothing is sent or read.

Run as root inside the lab only: it changes a route and the NAT table of its
own network namespace.
"""

import asyncio
import ipaddress
import os
import subprocess

NETWORK = ipaddress.ip_network(os.environ.get("LAB_ALLOPEN_NETWORK", "10.77.4.0/24"))
LISTEN_PORT = int(os.environ.get("LAB_ALLOPEN_PORT", "9999"))
BACKLOG = 4096


def run(*command: str) -> None:
    subprocess.run(command, check=True)


def prepare() -> None:
    run("ip", "route", "replace", "local", str(NETWORK), "dev", "lo")
    run(
        "iptables", "-t", "nat", "-A", "PREROUTING", "-d", str(NETWORK), "-p", "tcp",
        "-j", "REDIRECT", "--to-ports", str(LISTEN_PORT),
    )  # fmt: skip


async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.close()


async def main() -> None:
    prepare()
    server = await asyncio.start_server(accept, "0.0.0.0", LISTEN_PORT, backlog=BACKLOG)
    with open("/tmp/ready", "w") as ready:
        ready.write("ready\n")
    print(f"all-ports-open target ready: {NETWORK}, every TCP port", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
