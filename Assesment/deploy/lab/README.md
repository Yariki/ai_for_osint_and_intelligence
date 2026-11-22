# Compose lab

Scan targets with known open ports, for the lab and load checks of every phase.

**The lab works only with the test switch on.** All lab addresses are private (10.77.0.0/16), and the product refuses to scan private addresses. A scanner, orchestrator and API that are pointed at the lab must run with `NDM_ALLOW_NON_PUBLIC_TARGETS=true`. Never set that switch on a real installation.

## What it is

```text
lab host ── transit 10.77.0.0/24 ── router ── targets segment 10.77.2.0/24 ── targets container
10.77.0.1                       10.77.0.2 / 10.77.2.2                         hosts in 10.77.3.0/24
```

- **Scanned network: 10.77.3.0/24.** This is the range a scan targets.
- **Targets container.** One container plays every host of the manifest: it holds their addresses, listens on their open ports and sets how their closed ports behave.
- **Router.** The targets sit one routed hop behind it. A probe to an address that has no host is forwarded by the router and then silently dropped, as on the Internet. Without the router, such a probe would fail address resolution on the host and never leave it.
- **The lab gateway (10.77.0.1) is not inside the scanned /24.**

## The manifest

[manifest.yaml](manifest.yaml) lists every lab host with its open ports. It is the single source of truth:

- the targets container builds the hosts from it, and refuses to start if it is wrong;
- accuracy checks read it as the expected result.

To change the lab, change the manifest and restart the targets container. Nothing about the hosts is repeated in the Compose file.

| Kind of target | In the manifest |
| --- | --- |
| Host that resets closed ports | `closed_ports: reset`, for example 10.77.3.10 |
| Host that drops closed ports | `closed_ports: drop`, for example 10.77.3.20 |
| Host that does not answer ping | `ping: false` (10.77.3.20) |
| Host with no open port | 10.77.3.30 |
| First and last address of the /24 | 10.77.3.0 and 10.77.3.255 |
| Address with no host | every address of 10.77.3.0/24 that is not listed |

## The router chain

For the traceroute checks of Phase 3, one more target sits behind three routers:

```text
lab host ── hop1 10.77.0.21 ── hop2 (silent) ── hop3 10.77.23.3 ── 10.77.25.10
```

- **hop2 is silent.** It forwards every packet and sends no ICMP error, so a traceroute shows it as a hop without an answer.
- **Every hop is in private space** and outside the far network 10.77.25.0/24. A scan or traceroute of 10.77.25.10 must never send a packet to a hop (I2).
- The expected hop sequence is in the `chain` section of [manifest.yaml](manifest.yaml); the `far-target` container plays that section's host, with the lab target image.

```sh
traceroute -n 10.77.25.10       # 10.77.0.21, *, 10.77.23.3, 10.77.25.10
nc -vz -w 3 10.77.25.10 443     # open
```

To see that hop2 stays silent, capture on it while a traceroute runs:

```sh
docker compose -f compose.lab.yaml exec hop2 sh -c 'apk add -q tcpdump && tcpdump -ni any icmp'
```

It shows the probes passing through, and no ICMP message sent by hop2 itself.

## The lab resolver

The `resolver` service answers reverse lookups for the scanned network from [resolver/lab.zone](resolver/lab.zone). The names in that file are what a scan of the lab must store as reverse DNS. Two hosts have no name on purpose, and one has two.

It is published on `10.77.0.1:1053`, and `lab.env` points the product at it. It forwards no query. (Not 5353: that is mDNS, which `avahi-daemon` holds on most Linux desktops, and the lab would not start.)

```sh
dig +short -p 1053 @10.77.0.1 -x 10.77.3.10     # web.lab.test. and www.lab.test.
dig -p 1053 @10.77.0.1 -x 10.77.3.30            # status: NXDOMAIN
```

## Requirements

A Linux host with Docker Engine and the Compose plugin, and the right to run containers with `NET_ADMIN`. A desktop Docker installation is not a substitute: there the "host" is a virtual machine, and its networking behaves differently.

## Start and stop

```sh
cd Assesment/deploy/lab
docker compose -f compose.lab.yaml up -d --build
docker compose -f compose.lab.yaml ps        # router and targets must be healthy
docker compose -f compose.lab.yaml down
```

Starting the lab adds one route to the host's routing table, through the one-shot `host-route` service: `10.77.3.0/24 via 10.77.0.2`. The route goes away when the lab is stopped. To add or remove it by hand:

```sh
sudo ip route replace 10.77.3.0/24 via 10.77.0.2
sudo ip route del 10.77.3.0/24
```

## Scan the lab with the product stack

[lab.env](lab.env) holds the settings the product stack needs for the lab: the test switch, the address probes leave from (10.77.0.1) and the vantage point name `lab`. Use it on top of the installation's own `.env`, from `Assesment/deploy`:

```sh
docker compose -f lab/compose.lab.yaml up -d --build
docker compose --env-file .env --env-file lab/lab.env up -d --build
```

In the application, give the project the scope `10.77.3.0/24` and scan it. The expected result is the manifest.

The lab suite runs the lab checks of every phase against this stack. It is skipped unless `NDM_LAB=1`:

```sh
cd Assesment/backend
NDM_LAB=1 NDM_LAB_USERNAME=<account> NDM_LAB_PASSWORD=<password> uv run pytest tests/lab
```

Some of its tests capture packets and need `sudo` without a password for `tcpdump`; without it they are skipped.

## Check that the lab works

Run these from the lab host.

```sh
# An open port: the connection succeeds.
nc -vz -w 3 10.77.3.10 443

# A closed port of a resetting host: refused at once.
nc -vz -w 3 10.77.3.10 81

# A closed port of a dropping host: no answer, times out.
nc -vz -w 3 10.77.3.20 81

# An address with no host: no answer, times out. It must not say "No route to host".
nc -vz -w 3 10.77.3.77 80

# The probe to that address was routed: the route goes through the router.
ip route get 10.77.3.77        # expected: "10.77.3.77 via 10.77.0.2 dev br-..."
```

To check every port of the manifest in one go:

```sh
python3 - <<'EOF'
import socket, yaml
for host in yaml.safe_load(open("manifest.yaml"))["hosts"]:
    for port in host["open_ports"]:
        socket.create_connection((host["address"], port), timeout=3).close()
print("every manifest port accepts connections")
EOF
```
