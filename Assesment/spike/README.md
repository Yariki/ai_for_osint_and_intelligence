# Phase 0 spike

Everything in this directory is disposable. It exists to answer two questions before the scanner is built: can pure-Python SYN scanning hold 5,000 packets per second with exact results (R1), and does host networking with a connection-tracking bypass work (R2)?

What is kept from Phase 0 is not here: the lab (`deploy/lab`), the packet builder and reply matcher (`backend/src/ndm/scanner`), and the `scanner-netinit` prototype (`deploy/scanner-netinit`). The spike imports the kept code; it does not copy it.

> **Status: run on 2026-11-18** with `run-checks.sh` on the lab. Gate 0 passed; the results and the choices they led to are in `docs/spike/phase-0-report.md`.

## What the spike scanner does

- Sends TCP SYN probes from a raw TCP socket, paced by a token bucket.
- Receives replies on a capture socket with a kernel packet filter that passes only TCP segments addressed to the scanner's source-port range.
- Has a second receive path on a raw TCP socket (`--receive raw`), to compare the two (check 0.8).
- Matches replies without keeping state: the probe's sequence number is a keyed hash of the target, and a reply counts only if it acknowledges that number plus one.
- Reads the kernel's count of packets dropped at the receive socket.
- Refuses to SYN-scan when its source-port range overlaps the kernel's ephemeral range.
- Sends ICMP echo with `--ping`.
- Prints a JSON report. With `--manifest` it compares the result with the lab manifest.

## Run it

Requirements: the Linux lab host, with the lab started (`deploy/lab/README.md`).

```sh
cd Assesment/spike
docker compose -f compose.spike.yaml build

# One top-1000 scan of the lab /24 at 5,000 packets per second (256,000 probes).
docker compose -f compose.spike.yaml run --rm spike --manifest /lab/manifest.yaml --ports top-1000 --rate 5000
```

Exit status 0 and `"exact": true` mean that the scan reported exactly the manifest's open ports among the probed ports. `"kernel_receive_drops"` must be 0.

| Option | Default | Meaning |
| --- | --- | --- |
| `--manifest FILE` | none | Lab manifest; gives the targets and the expected result. |
| `--targets CIDR` | the manifest's network | Range to scan. |
| `--ports` | `top-1000` | `top-100`, `top-1000`, `full`, or a comma-separated list. |
| `--rate N` | `5000` | Packets per second. |
| `--receive capture\|raw` | `capture` | Receive path. |
| `--source-ports A-B` | `61000-61999` | Source-port range of the scanner. |
| `--wait S` | `3` | Seconds to wait for late replies after the last probe. |
| `--max-seconds S` | none | Stop sending after this long (for the connection-tracking run). |
| `--ping` | off | Also send ICMP echo to every target. |

## The checks of TASK-013

**One scan at the ceiling.** The command above. Expected: `"exact": true`, `"achieved_rate_pps"` close to 5000.

**Check 0.6, privileges.** The report starts with the process's user ID and effective capabilities.

```sh
docker compose -f compose.spike.yaml run --rm spike --manifest /lab/manifest.yaml --ports top-100 | head -8
```

Expected: `"uid": 10003` and `"effective_capabilities": ["NET_RAW"]` — a non-zero user ID and exactly one capability — and the scan works (`"syn_capability": true`).

**Check 0.7, source-port guard.** Give a source-port range inside the kernel's ephemeral range (`cat /proc/sys/net/ipv4/ip_local_port_range`, usually 32768–60999).

```sh
docker compose -f compose.spike.yaml run --rm spike --manifest /lab/manifest.yaml --source-ports 40000-40999
```

Expected: `"syn_capability": false`, the reason, exit status 3, and no packet sent.

The measured runs (three consecutive runs, headroom, connection tracking, capture inspection, receive-path comparison) belong to TASK-015 and are driven by `run-checks.sh`.
