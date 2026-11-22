# Network Discovery Map — Task Graph, Phase 0

| | |
| --- | --- |
| Version | 1.0 |
| Date | 2026-11-15 |
| Status | Proposed; ready for assignment |
| Based on | Network Discovery Map — Delivery Plan v1.0, section 5 (Phase 0); Architecture v1.0, section 8 |
| Audience | Implementation team |

This document breaks Phase 0 (Scanner spike) of the delivery plan into tasks. The conventions, the field meanings and the rules of the graph are in the index, [network-discovery-map-task-graph.md](network-discovery-map-task-graph.md).

## 1. Scope and entry conditions

**Goal of the phase.** Prove the two assumptions the scanner design rests on before anything is built on them: pure-Python SYN scanning at 5,000 packets per second (R1), and host networking with a connection-tracking bypass (R2).

**Entry conditions.**

- The Linux lab host is available: it matches the deployment target and gives root for host preparation. A desktop Docker installation is not a substitute.
- TASK-001 (scaffold) is complete, for TASK-012 only. TASK-011 and TASK-014 can start at once.

**What is kept.** The lab and its manifest (TASK-011), the packet builder and reply matcher with their tests (TASK-012) and the `scanner-netinit` prototype (TASK-014) are carried into Phase 1B. Everything under `Assesment/spike/` is disposable.

## 2. Overview

| Task | Title | Depends on | Risk | Checks closed |
| --- | --- | --- | --- | --- |
| TASK-011 | Compose lab with targets, router and manifest | — | low | — |
| TASK-012 | Packet builder and reply matcher | TASK-001 | medium | Basis of 1B.3 |
| TASK-013 | Spike scanner and container privileges | TASK-011, TASK-012 | high | 0.6, 0.7 |
| TASK-014 | `scanner-netinit` prototype | — | high | — |
| TASK-015 | Spike measurements, report and Gate 0 | TASK-013, TASK-014 | medium | 0.1 to 0.5, 0.8; Gate 0 |

| Wave | Tasks that run in parallel |
| --- | --- |
| 1 | TASK-011, TASK-012, TASK-014 |
| 2 | TASK-013 |
| 3 | TASK-015 |

The whole phase runs in parallel with Phase 1A. No Phase 0 task lists a file that a Phase 1A task lists.

## 3. Tasks

### TASK-011 — Compose lab with targets, router and manifest

- **dependencies:** none
- **risk_level:** low

**Description.**

- A Compose lab network, kept for all later phases, with target containers that have fixed open-port sets. It includes a host that resets closed ports, a host that drops them, and addresses with no host.
- The targets sit one routed hop behind a router container, so unanswered addresses behave as on the Internet instead of failing address resolution on a shared segment. The scanned /24 does not contain the lab gateway.
- A manifest file lists the expected open ports per address. It is the single source of truth: the target containers read their port sets from it, so the lab and the expectation cannot drift apart.
- The lab addresses are non-public. The README states that the lab works only with the test switch on.

**expected_files.**

- `Assesment/deploy/lab/compose.lab.yaml`
- `Assesment/deploy/lab/manifest.yaml`
- `Assesment/deploy/lab/target/Dockerfile`
- `Assesment/deploy/lab/target/listen.py`
- `Assesment/deploy/lab/router/Dockerfile`
- `Assesment/deploy/lab/router/entrypoint.sh`
- `Assesment/deploy/lab/README.md`

**Acceptance.**

- The lab starts on the lab host from a clean checkout.
- From the host, a plain TCP connection succeeds to every port in the manifest, is refused on a closed port of the resetting host and times out on a closed port of the dropping host.
- A packet to an address with no host is routed and silently dropped; it does not fail address resolution on the host.

### TASK-012 — Packet builder and reply matcher

- **dependencies:** TASK-001
- **risk_level:** medium (kept code; its correctness decides acceptance criterion 2)

**Description.**

- Build a TCP SYN header from a template with the checksum computed in Python and no payload, and an ICMP echo request. Standard library only; no packet-crafting library.
- Stateless reply matching (AD5): the probe's sequence number is a keyed hash of the target address and port. A reply is valid only if its acknowledgement number equals that value plus one and its destination port maps to the chunk's source port. SYN/ACK means open, RST means closed.
- Pure functions with unit tests, imported unchanged by the production scanner in Phase 1B. Nothing here imports `ndm.db` or `ndm.api` (C-3).

**expected_files.**

- `Assesment/backend/src/ndm/scanner/__init__.py`
- `Assesment/backend/src/ndm/scanner/packet.py`
- `Assesment/backend/src/ndm/scanner/matching.py`
- `Assesment/backend/tests/unit/scanner/test_packet.py`
- `Assesment/backend/tests/unit/scanner/test_matching.py`

**Acceptance.**

- Checksums match known reference vectors, and a built SYN segment carries no payload.
- A reply is accepted only with the expected acknowledgement number and source-port mapping; unrelated segments are ignored. This is the pass condition of check 1B.3.

### TASK-013 — Spike scanner and container privileges

- **dependencies:** TASK-011, TASK-012
- **risk_level:** high (raw-socket privilege on the host network)

**Description.**

- A disposable spike scanner: raw TCP send, a capture socket with a kernel packet filter that passes only TCP segments addressed to the scanner's source-port range, token-bucket pacing, read-out of the kernel drop counter, ICMP echo.
- A second receive path on a raw TCP socket, so the two can be compared in check 0.8.
- A start-up guard: when the source-port range overlaps the kernel's ephemeral range, the scanner reports no SYN capability.
- The container set-up of architecture section 8.4: a non-root user, all capabilities dropped except `NET_RAW`, the `cap_net_raw` file capability on the image's own copy of the interpreter, no `no-new-privileges`, host networking.

**expected_files.**

- `Assesment/spike/scanner/spike_scan.py`
- `Assesment/spike/scanner/Dockerfile`
- `Assesment/spike/compose.spike.yaml`
- `Assesment/spike/README.md`

**Acceptance.**

- One top-1000 scan of the lab /24 at 5,000 packets per second completes and reports the manifest's open ports. The three formal runs belong to TASK-015.
- Check 0.6: the process runs with a non-zero user ID and exactly one effective capability, and SYN scanning works.
- Check 0.7: with a source-port range that overlaps the ephemeral range, the scanner reports no SYN capability.

### TASK-014 — `scanner-netinit` prototype

- **dependencies:** none
- **risk_level:** high (`NET_ADMIN`; changes the host's firewall rules)

**Description.**

- A one-shot container on the host network with `NET_ADMIN` that installs the two `NOTRACK` rules for the scanner's source-port range (proposed 61000–61999) and exits.
- Installing twice leaves each rule in place once.
- The README gives the equivalent rules for an operator who applies them by hand, the command that removes them, and the firewall back end the rules were tested with.

**expected_files.**

- `Assesment/deploy/scanner-netinit/Dockerfile`
- `Assesment/deploy/scanner-netinit/install-rules.sh`
- `Assesment/deploy/scanner-netinit/README.md`

**Acceptance.**

- After one run and after a second run, each of the two rules is present exactly once, and the container has exited with status 0.
- The documented removal restores the rule set that was there before.

### TASK-015 — Spike measurements, report and Gate 0

- **dependencies:** TASK-013, TASK-014
- **risk_level:** medium

**Description.**

- Run checks 0.1 to 0.8 on the lab host from a script, so the runs can be repeated.
- Write the spike report:
  - measured rates, drops and processor load;
  - connection-tracking counts with and without the rules;
  - the choice between raw and capture socket for receiving, with its reason;
  - the host-preparation steps that proved necessary;
  - a go or no-go statement.
- Put Q4 to the product owner together with the confirmed host-preparation list.
- On no-go, Phase 1B does not start. A native-language scanner is raised with the product owner as a deviation under C-1, and the scanner tasks of Phase 1B are re-estimated. Phase 1A continues unchanged.

**expected_files.**

- `Assesment/spike/run-checks.sh`
- `Assesment/docs/spike/phase-0-report.md`

**Acceptance.**

- Check 0.1: a top-1000 scan of the lab /24 with "all targets" (256,000 probes) at 5,000 packets per second reports exactly the manifest's open ports, in three consecutive runs.
- Check 0.2: the kernel drop counter of the capture socket is zero in all three runs.
- Checks 0.3, 0.4, 0.5 and 0.8 are done and their results are in the report. A highest exact rate below 10,000 packets per second is reported as a risk, not as a failure.
- The product owner has answered Q4.

## 4. Coverage of Phase 0

| Phase 0 check | Task |
| --- | --- |
| 0.1 Accuracy at the ceiling | TASK-015 |
| 0.2 Own replies not dropped | TASK-015 |
| 0.3 Headroom | TASK-015 |
| 0.4 Connection tracking | TASK-015, with the rules of TASK-014 |
| 0.5 Half-open teardown, no payload | TASK-015 |
| 0.6 Privileges | TASK-013 |
| 0.7 Source-port guard | TASK-013 |
| 0.8 Receive path | TASK-015, with both receive paths of TASK-013 |
| Gate 0 | TASK-015 |

The common completion criteria of delivery plan section 2.2 apply from Phase 1A onward and do not apply to this phase.
