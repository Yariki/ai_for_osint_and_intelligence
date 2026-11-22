#!/usr/bin/env bash
# Phase 0 measurements: runs checks 0.1 to 0.8 on the lab host and keeps every
# result, so the runs can be repeated and the spike report written from them.
#
#     cd Assesment/spike && ./run-checks.sh
#
# Needs: the lab host, Docker with the Compose plugin, python3 and tcpdump (to
# read captures). Captures are taken with tcpdump under sudo when sudo needs
# no password, otherwise with tcpdump in a container on the host network
# (deploy/lab/capture). It starts the lab, builds the images, and installs and
# removes the two scanner-netinit rules on the host. The whole run takes
# about 20 minutes, most of it the two connection-tracking runs.
#
# Run only on the lab host, or with nothing else running: the
# connection-tracking run without the rules fills the host's table at about
# 5,000 entries a second (one per probe). It is stopped once the count passes
# half the limit, before a full table disrupts the host's networking.
#
# First run on 2026-11-18; the report is docs/spike/phase-0-report.md.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$HERE/../deploy"
RESULTS="$HERE/results/$(date -u +%Y%m%dT%H%M%SZ)"
SCANNED=10.77.3.0/24
# A part of the scanned /24 in which the manifest has no host: nothing answers.
SILENT=10.77.3.64/26
mkdir -p "$RESULTS"
SUMMARY="$RESULTS/summary.txt"

say() { printf '%s\n' "$*" | tee -a "$SUMMARY"; }
spike() { docker compose -f "$HERE/compose.spike.yaml" run --rm -T spike "$@"; }
netinit() { docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local "$@"; }
# field FILE EXPRESSION: evaluate a Python expression over the JSON report "r".
field() { python3 -c "import json,sys; r=json.load(open(sys.argv[1])); print($2)" "$1"; }
conntrack() { cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo "n/a"; }
CONNTRACK_MAX="$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo 0)"
# capture_start FILE EXPRESSION / capture_stop: tcpdump on every interface.
if sudo -n true 2>/dev/null; then
    capture_start() { sudo tcpdump -i any -n -w "$1" "$2" > /dev/null 2>&1 & sleep 2; }
    capture_stop() { sleep 2; sudo pkill -INT tcpdump || true; sleep 1; }
else
    CAPTURE_NAME="ndm-spike-capture-$$"
    capture_start() {
        capture_file="$1"
        docker build -q -t ndm-lab-capture "$DEPLOY/lab/capture" > /dev/null
        # -Z root: tcpdump still calls setgid and setgroups, hence SETUID and SETGID.
        docker run -d --name "$CAPTURE_NAME" --network host --cap-drop ALL \
            --cap-add NET_RAW --cap-add NET_ADMIN --cap-add SETUID --cap-add SETGID \
            ndm-lab-capture \
            -i any -n -Z root -w /tmp/capture.pcap "$2" > /dev/null
        sleep 2
    }
    capture_stop() {
        sleep 2
        docker kill --signal INT "$CAPTURE_NAME" > /dev/null || true
        docker wait "$CAPTURE_NAME" > /dev/null || true
        docker cp "$CAPTURE_NAME:/tmp/capture.pcap" "$capture_file"
        docker rm -f "$CAPTURE_NAME" > /dev/null
    }
fi

say "Phase 0 checks, $(date -u +%FT%TZ)"
say "host: $(uname -srm); docker: $(docker version --format '{{.Server.Version}}'); iptables: $(iptables --version 2>/dev/null || echo unknown)"
say "ephemeral ports: $(cat /proc/sys/net/ipv4/ip_local_port_range); rmem_max: $(cat /proc/sys/net/core/rmem_max)"

# --- Set-up -----------------------------------------------------------------
# --wait names the long-running services: it reports the one-shot host-route,
# which exits once the routes are set, as a failure.
docker compose -f "$DEPLOY/lab/compose.lab.yaml" up -d --build --wait \
    router targets allopen hop1 hop2 hop3 far-target resolver
docker compose -f "$DEPLOY/lab/compose.lab.yaml" up -d host-route
docker build -q -t ndm-scanner-netinit:local "$DEPLOY/scanner-netinit" > /dev/null
docker compose -f "$HERE/compose.spike.yaml" build -q
netinit install | tee -a "$SUMMARY"

# --- 0.1 accuracy at the ceiling, 0.2 own replies not dropped -----------------
say ""; say "0.1 / 0.2: three consecutive top-1000 scans of $SCANNED at 5,000 packets per second"
passed=0
for run in 1 2 3; do
    out="$RESULTS/0.1-run-$run.json"
    spike --manifest /lab/manifest.yaml --ports top-1000 --rate 5000 > "$out" || true
    say "  run $run: exact=$(field "$out" 'r.get("exact")') drops=$(field "$out" 'r.get("kernel_receive_drops")') rate=$(field "$out" 'r.get("achieved_rate_pps")') load=$(field "$out" 'r.get("processor_load")') missed=$(field "$out" 'r.get("missed")') false=$(field "$out" 'r.get("false_open")')"
    if [ "$(field "$out" 'r.get("exact") is True and r.get("kernel_receive_drops") == 0')" = "True" ]; then
        passed=$((passed + 1))
    fi
done
if [ "$passed" -eq 3 ]; then say "  0.1 and 0.2: PASSED"; else say "  0.1 and 0.2: FAILED ($passed of 3 runs exact with zero drops)"; fi

# --- 0.6 privileges ----------------------------------------------------------
out="$RESULTS/0.1-run-1.json"
say ""; say "0.6: uid=$(field "$out" 'r["uid"]') capabilities=$(field "$out" 'r["effective_capabilities"]') syn_capability=$(field "$out" 'r.get("syn_capability")')"
if [ "$(field "$out" 'r["uid"] != 0 and r["effective_capabilities"] == ["NET_RAW"] and r.get("syn_capability") is True')" = "True" ]; then
    say "  0.6: PASSED"
else
    say "  0.6: FAILED"
fi

# --- 0.3 headroom ------------------------------------------------------------
say ""; say "0.3: highest rate that still gives exact results with zero drops"
highest=0
for rate in 5000 7500 10000 15000 20000 30000 40000; do
    out="$RESULTS/0.3-rate-$rate.json"
    spike --manifest /lab/manifest.yaml --ports top-1000 --rate "$rate" > "$out" || true
    ok="$(field "$out" 'r.get("exact") is True and r.get("kernel_receive_drops") == 0')"
    say "  requested $rate: achieved=$(field "$out" 'r.get("achieved_rate_pps")') exact=$(field "$out" 'r.get("exact")') drops=$(field "$out" 'r.get("kernel_receive_drops")') load=$(field "$out" 'r.get("processor_load")')"
    if [ "$ok" = "True" ]; then highest="$(field "$out" 'r.get("achieved_rate_pps")')"; else break; fi
done
say "  0.3: highest exact rate $highest packets per second (below 10,000 is a risk to report, not a failure)"

# --- 0.4 connection tracking --------------------------------------------------
say ""; say "0.4: five minutes at 5,000 packets per second against addresses that do not answer ($SILENT)"
for mode in with-rules without-rules; do
    if [ "$mode" = "with-rules" ]; then netinit install > /dev/null; else netinit remove > /dev/null; fi
    sleep 150   # let entries of earlier runs expire
    before="$(conntrack)"
    out="$RESULTS/0.4-$mode.json"
    spike --targets "$SILENT" --ports full --rate 5000 --max-seconds 300 > "$out" &
    scan=$!
    peak="$before"
    stopped=""
    while kill -0 "$scan" 2>/dev/null; do
        now="$(conntrack)"; echo "$(date +%T) $now" >> "$RESULTS/0.4-$mode-samples.txt"
        if [ "$now" != "n/a" ] && [ "$now" -gt "$peak" ]; then peak="$now"; fi
        # A full table disrupts all networking on the host: stop at half of it.
        if [ "$now" != "n/a" ] && [ "$CONNTRACK_MAX" -gt 0 ] && [ "$now" -gt $((CONNTRACK_MAX / 2)) ]; then
            docker ps -q --filter "label=com.docker.compose.service=spike" | xargs -r docker kill > /dev/null
            stopped=" (stopped at half the limit)"
        fi
        sleep 5
    done
    wait "$scan" || true
    say "  $mode: conntrack count before=$before peak=$peak after=$(conntrack) (limit $CONNTRACK_MAX)$stopped; probes sent=$(field "$out" 'r.get("probes_sent")' 2>/dev/null || echo n/a)"
done
netinit install > /dev/null
say "  0.4: with the rules the count must stay flat; see the samples files"

# --- 0.5 half-open teardown, no payload ---------------------------------------
say ""; say "0.5: capture of one top-100 scan"
pcap="$RESULTS/0.5-capture.pcap"
capture_start "$pcap" "tcp and net $SCANNED"
spike --manifest /lab/manifest.yaml --ports top-100 --rate 5000 > "$RESULTS/0.5-scan.json" || true
capture_stop
count() { tcpdump -n -r "$pcap" "$1" 2>/dev/null | wc -l; }
payload="$(count "dst net $SCANNED and tcp and (ip[2:2] - ((ip[0]&0xf)<<2) - ((tcp[12]&0xf0)>>2)) != 0")"
synack="$(count "src net $SCANNED and tcp[tcpflags] & (tcp-syn|tcp-ack) == (tcp-syn|tcp-ack)")"
resets="$(count "dst net $SCANNED and tcp[tcpflags] & tcp-rst != 0")"
say "  packets to targets with TCP payload: $payload (must be 0)"
say "  SYN/ACK from targets: $synack; resets from the host: $resets (every SYN/ACK must be followed by a reset)"
if [ "$payload" -eq 0 ] && [ "$resets" -ge "$synack" ] && [ "$synack" -gt 0 ]; then say "  0.5: PASSED"; else say "  0.5: FAILED or needs a look at $pcap"; fi

# --- 0.7 source-port guard ----------------------------------------------------
say ""; say "0.7: a source-port range inside the ephemeral range"
out="$RESULTS/0.7-guard.json"
status=0; spike --manifest /lab/manifest.yaml --source-ports 40000-40999 > "$out" || status=$?
say "  exit status $status, syn_capability=$(field "$out" 'r.get("syn_capability")'), reason: $(field "$out" 'r.get("reason")')"
if [ "$status" -eq 3 ] && [ "$(field "$out" 'r.get("syn_capability")')" = "False" ]; then say "  0.7: PASSED"; else say "  0.7: FAILED"; fi

# --- 0.8 receive path ---------------------------------------------------------
say ""; say "0.8: capture socket against raw socket, host firewall active, scanner-netinit rules installed"
for path in capture raw; do
    out="$RESULTS/0.8-$path.json"
    spike --manifest /lab/manifest.yaml --ports top-1000 --rate 5000 --receive "$path" > "$out" || true
    say "  $path: exact=$(field "$out" 'r.get("exact")') drops=$(field "$out" 'r.get("kernel_receive_drops")') matched=$(field "$out" 'r.get("replies_matched")') load=$(field "$out" 'r.get("processor_load")')"
done
say "  0.8: record the choice and its reason in the spike report"

say ""; say "Results are in $RESULTS. The scanner-netinit rules are left installed;"
say "remove them with: docker run --rm --network host --cap-drop ALL --cap-add NET_ADMIN ndm-scanner-netinit:local remove"
