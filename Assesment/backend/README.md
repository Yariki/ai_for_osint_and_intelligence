
You can now run the scanner on its own against one IP. I added [backend/scripts/scan_one.py](scripts/scan_one.py), which builds one chunk for a single address and passes it to the scanner's real `Worker`, so its own scope check runs too. Each message the scanner would normally send to the broker is printed as a JSON line instead. The orchestrator, API and database aren't involved.

## Usage

From `Assesment/`, with the lab running (`docker compose -f deploy/lab/compose.lab.yaml up -d`):

```sh
# SYN scan, inside the dev scanner container (host network + NET_RAW)
docker compose -f deploy/compose.dev.yaml run --rm \
  -v "$PWD/backend/scripts:/scripts:ro" scanner \
  python-netraw /scripts/scan_one.py 10.77.3.10 --ports 22,80,443 --syn
```

Options:
- `--ports`: a list like `22,80,8000-8100`, or one of the profiles `top-100` (the default), `top-1000` or `full`.
- `--syn`: SYN scan. Without it you get a connect scan.
- `--discovery`: runs host discovery (ICMP echo plus TCP probes to `--ports`) instead of a port scan.
- `--pps`, `--timeout-ms`, `--retries`: the same settings a scan has.

Two variations:
- **Connect mode:** use `python` instead of `python-netraw`. The container then runs without raw sockets, the same as a scanner that lacks `NET_RAW`.
- **Skip the dependencies:** add `--no-deps` and Compose won't start RabbitMQ and the firewall-rule container. The SYN run above did start both.

Because the code is mounted from `backend/src`, scanner changes apply on the next run without a rebuild.

## Tested against the lab

I started the lab (it wasn't running) and ran both modes:

| Run | Target | Result |
| --- | --- | --- |
| SYN, ports 22,80,443,8080 | 10.77.3.10 | open: 22, 80, 443; closed: 1 |
| Connect, top-100 | 10.77.3.20 (filtering host) | open: 443, 8443; filtered: 98 |

Both match the lab manifest.

## Things to know

- **Leftovers:** the lab containers and the dev RabbitMQ are still running. `docker compose -f deploy/compose.dev.yaml down` stops RabbitMQ.
- **Warning:** every run warns that `net.core.rmem_max` is 4194304. It's harmless for one host. To remove it, run `sudo sysctl -w net.core.rmem_max=8388608`; I can't, since I don't have sudo here.
- **Public addresses:** private addresses like the lab or `127.0.0.1` are accepted only because the dev stack sets `NDM_ALLOW_NON_PUBLIC_TARGETS=true`. A public address needs `-e NDM_DEV_EGRESS_ADDRESS=` (empty), so probes leave from the host's own address instead of the lab's.
- **Lint:** I haven't run ruff or mypy on the script, because `uv` isn't installed here.
- **With results stored:** if you want the results saved and shown on the map, run the full dev stack and create a project with a `/32` scope, e.g. `10.77.3.10/32`.
