# Phase 0 — Scanner Spike Report and Gate 0

| | |
| --- | --- |
| Task | TASK-015 |
| Run | 2026-11-18, 22:50 to 23:25 UTC, `spike/run-checks.sh` |
| Audience | Product owner (Gate 0, question Q4); the team |
| Result | **Go.** Checks 0.1 and 0.2 passed; checks 0.3 to 0.8 are done and recorded below. |

## 1. Where it ran

| | |
| --- | --- |
| Host | Linux 7.0.0-43-generic, x86_64; Docker 29.1.3; iptables 1.8.11 (nf_tables) |
| Lab | `deploy/lab`, on the same host: the targets one routed hop behind a router container, on Linux bridges |
| Scanner | `spike/scanner`, image `ndm-spike:local`, host network, user 10003, capabilities `NET_RAW` only |
| Kernel settings | ephemeral ports 32768–60999; `net.core.rmem_max` 4,194,304 (left at the distribution's value); `nf_conntrack_max` 262,144 |
| Source ports | 61000–61999, with the two `scanner-netinit` rules installed (except where 0.4 says otherwise) |

**What this does not measure.** The lab's targets are containers on the scanning host, so probes and replies cross Linux bridges, not a physical network: there is no loss, queueing or latency of a real link. The rates below are what the scanner process and the host's network stack sustain; a real network can only lower them. The raw results are in `spike/results/20261118T225005Z/` (not committed: they include a packet capture).

## 2. Results

| Check | Requirement | Result |
| --- | --- | --- |
| 0.1 Accuracy at the ceiling | Three consecutive top-1000 scans of the /24, all targets (256,000 probes), at 5,000 pps report exactly the manifest's open ports | **Passed.** All three exact (33 open ports on 7 hosts; nothing missed, nothing false). Achieved 5,001, 5,001 and 5,000 pps. |
| 0.2 Own replies not dropped | Kernel drop counter of the capture socket is 0 in all three runs | **Passed.** 0, 0, 0. |
| 0.3 Headroom | Highest rate that still gives exact results | **40,047 pps**, the highest rate tried: exact with no drop at every step (table below). Well above the 10,000 pps risk threshold. |
| 0.4 Connection tracking | Five minutes at 5,000 pps against addresses that do not answer: flat with the rules; growth measured without | **Done.** With the rules: 32 before, peak 45, over 1,499,823 probes. Without them: about 5,000 entries a second, one per probe; stopped at 148,660 after 30 seconds (table below). |
| 0.5 Half-open teardown, no payload | Every SYN/ACK followed by a reset; no TCP payload to a target | **Passed.** 120 SYN/ACKs, 120 resets from the host; 0 packets with payload. |
| 0.6 Privileges | Non-zero user, exactly one capability, SYN works | **Passed.** uid 10003, effective capabilities `NET_RAW`, SYN capability true. |
| 0.7 Source-port guard | Overlap with the ephemeral range gives no SYN capability | **Passed.** With 40000–40999: exit 3, SYN capability false, reason "the source-port range overlaps net.ipv4.ip_local_port_range". |
| 0.8 Receive path | Raw and capture sockets compared with the host firewall active | **Done.** Both exact, 0 drops, 6,005 replies matched each; processor load 0.119 and 0.120. Choice below. |

**0.3, the ladder.** Each step is one top-1000 scan of the /24 with all targets. Load is the scanner's processor time per second of wall-clock time, as a share of one core.

| Requested pps | Achieved pps | Exact | Drops | Load |
| --- | --- | --- | --- | --- |
| 5,000 | 5,000 | yes | 0 | 0.117 |
| 7,500 | 7,500 | yes | 0 | 0.149 |
| 10,000 | 10,004 | yes | 0 | 0.188 |
| 15,000 | 14,968 | yes | 0 | 0.234 |
| 20,000 | 19,904 | yes | 0 | 0.292 |
| 30,000 | 29,628 | yes | 0 | 0.355 |
| 40,000 | 40,047 | yes | 0 | 0.408 |

The script stops at 40,000; no failure point was reached. At the product's ceiling of 5,000 pps the scanner uses about an eighth of one core.

**0.4, without the rules.** Samples every 5 seconds of the host's connection-tracking count, from the start of the scan:

| Time | Entries |
| --- | --- |
| 0 s | 36 |
| 5 s | 23,319 |
| 10 s | 48,372 |
| 20 s | 98,513 |
| 25 s | 123,579 |
| 30 s | 148,660 |

The count grows by one entry per unanswered probe, and the entries live for about two minutes, so at 5,000 pps the table's 262,144 entries would be full in about 52 seconds; a full table disrupts all networking on the host. The five-minute run the check describes would have done that, so the script stops a run without the rules once the count passes half the limit. The growth is measured; the full table was not reached on purpose. With the rules, the count stayed between 32 and 45 for the whole five minutes.

## 3. The receive path

**Choice: the capture socket** (`AF_PACKET`), as the architecture proposed (section 8.5).

Both paths were exact with no drops and the same load here. The reason for the choice is not speed but independence: the capture socket sees replies before the host firewall's input rules, so it does not depend on how a given host's firewall treats packets that connection tracking no longer follows (the `NOTRACK` rules make the replies untracked; a firewall that drops untracked input would blind a raw TCP socket). The capture socket also gives the kernel's drop counter, which checks 0.2, 1B.10 and the product's `receive_drops` rely on. The firewall here was the host's own (Docker's rules on nf_tables); its filter rules could not be listed without root.

## 4. Host preparation

What the spike shows each step is for:

| Step | Needed? | Evidence |
| --- | --- | --- |
| The two `NOTRACK` rules for the scanner's source ports (`scanner-netinit`, or by hand) | **Yes** | 0.4: without them the connection-tracking table fills in under a minute at the default ceiling. |
| A scanner source-port range outside the kernel's ephemeral range | **Yes**, and the default already is (61000–61999 against 32768–60999) | 0.7: an overlapping range is refused. |
| `net.core.rmem_max` of at least 8 MB | **Not shown to be needed** at these rates | 4 MB (the kernel grants 8 MB to the socket) gave no drop up to 40,000 pps. The product's scanner still warns below 8 MB; on a real network with bursts of replies it is a cheap safeguard. |
| A host with the connection-tracking module loaded | Given | Docker loads it; that is why 0.4 matters. |

## 5. Gate 0 and Q4

**Gate 0: go.** Checks 0.1 and 0.2 pass; 0.3 to 0.8 are done and recorded. The Python scanner meets the rate and the accuracy with a wide margin, so R1 does not arise and no deviation under C-1 is needed.

Phase 1B was implemented before this gate could be run (no lab host until 2026-11-18). The product's own scanner has since passed checks 1B.10, 1B.12, 1B.15, 1B.16 and 1B.17 on the same lab.

**Q4, for the product owner:** is this one-time host preparation acceptable?

1. Let `scanner-netinit` install the two `NOTRACK` rules at start-up (the Compose default), or install them by hand (`deploy/scanner-netinit/README.md`).
2. Keep the scanner's source-port range outside the kernel's ephemeral range (the default does).
3. Optionally raise `net.core.rmem_max` to 8,388,608 (`sysctl -w`, and in `/etc/sysctl.d/` to keep it after a reboot).

**Answer:** not yet given.
