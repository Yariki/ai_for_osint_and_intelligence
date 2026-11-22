"""Lab targets: one container that plays every host of the lab manifest.

For each host in the manifest it
  - adds the host's address to the container's interface,
  - listens on the host's open ports (a connection is accepted and closed at
    once; nothing is sent or read),
  - sets up how closed ports and ICMP echo behave.

It also routes its replies back through the lab router, so the path is the
same in both directions.

Run as root inside the lab only: it changes addresses, routes and firewall
rules of its own network namespace.
"""

import ipaddress
import os
import selectors
import socket
import subprocess
import sys

import yaml

MANIFEST = os.environ.get("LAB_MANIFEST", "/lab/manifest.yaml")
# A section of the manifest to play instead of the whole of it, for example "chain".
SECTION = os.environ.get("LAB_MANIFEST_SECTION")
ROUTER = os.environ["LAB_ROUTER"]  # the router's address on the targets segment
INTERFACE = os.environ.get("LAB_INTERFACE", "eth0")


def run(*command: str) -> None:
    subprocess.run(command, check=True)


def load_hosts() -> list[dict]:
    with open(MANIFEST) as file:
        manifest = yaml.safe_load(file)
    if SECTION:
        manifest = manifest[SECTION]
    network = ipaddress.ip_network(manifest["scanned_network"])
    hosts = manifest["hosts"]
    seen = set()
    for host in hosts:
        address = ipaddress.ip_address(host["address"])
        # A wrong manifest must stop the lab, not produce a lab that differs from it.
        if address not in network:
            sys.exit(f"manifest: {address} is outside the scanned network {network}")
        if address in seen:
            sys.exit(f"manifest: {address} is listed twice")
        if host["closed_ports"] not in ("reset", "drop"):
            sys.exit(f"manifest: {address} has an unknown closed_ports value")
        if any(not 1 <= port <= 65535 for port in host["open_ports"]):
            sys.exit(f"manifest: {address} has a port outside 1-65535")
        seen.add(address)
    return hosts


def configure_network(hosts: list[dict]) -> None:
    for host in hosts:
        address = host["address"]
        run("ip", "addr", "add", f"{address}/32", "dev", INTERFACE)

        if host["closed_ports"] == "drop":
            # Only SYNs to open ports get in; everything else to this address vanishes.
            for port in host["open_ports"]:
                run("iptables", "-A", "INPUT", "-d", address, "-p", "tcp", "--dport", str(port),
                    "-j", "ACCEPT")  # fmt: skip
            if host["ping"]:
                run("iptables", "-A", "INPUT", "-d", address, "-p", "icmp",
                    "--icmp-type", "echo-request", "-j", "ACCEPT")  # fmt: skip
            run("iptables", "-A", "INPUT", "-d", address, "-j", "DROP")
        elif not host["ping"]:
            run("iptables", "-A", "INPUT", "-d", address, "-p", "icmp",
                "--icmp-type", "echo-request", "-j", "DROP")  # fmt: skip

    # Replies leave through the lab router, as the probes arrived.
    run("ip", "route", "replace", "default", "via", ROUTER)


def serve(hosts: list[dict]) -> None:
    selector = selectors.DefaultSelector()
    count = 0
    for host in hosts:
        for port in host["open_ports"]:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host["address"], port))
            listener.listen(1024)
            listener.setblocking(False)
            selector.register(listener, selectors.EVENT_READ)
            count += 1

    # The container's health check waits for this file.
    with open("/tmp/ready", "w") as ready:
        ready.write(f"{count}\n")
    print(f"lab targets ready: {len(hosts)} hosts, {count} open ports", flush=True)

    while True:
        for key, _ in selector.select():
            try:
                connection, _peer = key.fileobj.accept()
                connection.close()
            except OSError:
                # A half-open probe that was reset before it was accepted.
                pass


def main() -> None:
    hosts = load_hosts()
    configure_network(hosts)
    serve(hosts)


if __name__ == "__main__":
    main()
