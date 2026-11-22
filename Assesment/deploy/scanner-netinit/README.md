# scanner-netinit

A one-shot container that installs two firewall rules on the host and exits. The rules keep the scanner's probes out of the kernel's connection tracking.

> **Status: run on 2026-11-18 and 19** (Docker 29.1.3, iptables 1.8.11 with nf_tables). The two rules are present exactly once after one install and after two; `remove` takes out those two and leaves the rest of the table as it was; `status` lists them without sudo. In the stack, `scanner-netinit` ran once and exited 0 (check 1B.17), and with the rules the connection-tracking count stayed flat (check 0.4, `docs/spike/phase-0-report.md`).

## Why it is needed

Docker loads the kernel's connection tracking. From then on the kernel also tracks the SYN packets the scanner sends from raw sockets. An unanswered SYN stays in the table for about two minutes. At 5,000 packets per second against filtered targets that is about 600,000 entries, more than the usual limit, and a full table disturbs all networking on the host.

The rules exempt only the scanner's traffic. It is recognizable by its source-port range, 61000–61999 by default.

## The rules

```sh
iptables -t raw -A OUTPUT     -p tcp --sport 61000:61999 -m comment --comment ndm-scanner-notrack -j CT --notrack
iptables -t raw -A PREROUTING -p tcp --dport 61000:61999 -m comment --comment ndm-scanner-notrack -j CT --notrack
```

The first covers the probes the host sends, the second the replies it receives. `-j CT --notrack` is the current form of the older `-j NOTRACK`.

## In the product stack

`deploy/compose.yaml` runs it as the one-shot service `scanner-netinit` at every start of the stack; the scanner starts only after it has exited with status 0. It takes its port range from `NDM_SOURCE_PORT_RANGE` in `deploy/.env`, the same value the scanner uses. Nothing has to be done by hand.

```sh
docker compose logs scanner-netinit       # one line per rule
```

To apply the rules yourself instead, set `NDM_NETINIT_SKIP=true` in `deploy/.env`. The service then installs nothing and exits with status 0, and the rules of "Apply the rules by hand" below must be present before a scan starts.

## Run it on its own

```sh
cd Assesment/deploy/scanner-netinit
docker build -t ndm-scanner-netinit:local .

docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local
```

It needs the host network (the rules belong to the host) and the `NET_ADMIN` capability, and nothing else. It prints one line per rule and exits with status 0. Running it again changes nothing: a rule that is already present is not added a second time.

| Setting | Default | Meaning |
| --- | --- | --- |
| `NDM_SCANNER_SOURCE_PORTS` | `61000-61999` | The scanner's source-port range, as `low-high` or `low:high`. Must match the scanner and lie outside `net.ipv4.ip_local_port_range`. In the product stack, Compose sets it from `NDM_SOURCE_PORT_RANGE`. |
| `NDM_NETINIT_SKIP` | `false` | `true`: install nothing and exit with status 0. |
| `NDM_IPTABLES` | `iptables` | `iptables` for the nftables back end, `iptables-legacy` for the legacy one. |

Use the back end the host uses. On the host, `iptables --version` prints `(nf_tables)` or `(legacy)`.

## See and remove the rules

```sh
docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local status
docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local remove
```

The rules are not persistent: they are gone after a reboot, and the container installs them again when the stack starts.

## Apply the rules by hand

An operator who prefers not to run a container with `NET_ADMIN` can apply the same rules as root:

```sh
# install
iptables -t raw -A OUTPUT     -p tcp --sport 61000:61999 -m comment --comment ndm-scanner-notrack -j CT --notrack
iptables -t raw -A PREROUTING -p tcp --dport 61000:61999 -m comment --comment ndm-scanner-notrack -j CT --notrack

# remove
iptables -t raw -D OUTPUT     -p tcp --sport 61000:61999 -m comment --comment ndm-scanner-notrack -j CT --notrack
iptables -t raw -D PREROUTING -p tcp --dport 61000:61999 -m comment --comment ndm-scanner-notrack -j CT --notrack
```

## Check the acceptance on the lab host

```sh
sudo iptables-save -t raw > before.txt

docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local; echo "exit status: $?"
docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local; echo "exit status: $?"
sudo iptables -t raw -S | grep -c ndm-scanner-notrack      # expected: 2

docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local remove
sudo iptables-save -t raw > after.txt
diff <(grep -v '^#' before.txt | sed 's/\[[0-9:]*\]//') <(grep -v '^#' after.txt | sed 's/\[[0-9:]*\]//') && echo "rule set restored"
```

## Tested with

| Host | Kernel | Docker | Back end | Result |
| --- | --- | --- | --- | --- |
| _to be filled in on the lab host_ | | | | |
