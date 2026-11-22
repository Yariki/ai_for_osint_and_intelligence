import json
from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from ndm.core import messages
from ndm.core.messages import (
    Cancel,
    CancelRequested,
    Chunk,
    ChunkDone,
    ChunkPhase,
    ChunkRejected,
    ClassificationRerunRequested,
    DatasetUpdateRequested,
    Discovery,
    EnrichmentRerunRequested,
    Evidence,
    Findings,
    Heartbeat,
    Hello,
    HopReply,
    HopRow,
    HostCounts,
    HostFinding,
    HostRow,
    HostsChanged,
    PhaseRetryRequested,
    ScanCreated,
    ScanProgress,
    Technique,
    TracerouteDone,
    TracerouteHop,
    TracerouteHopEvent,
    TracerouteJob,
    TracerouteMethod,
    TracerouteRejected,
    TracerouteRequested,
)

CHUNK = Chunk(
    scan_id=4812,
    chunk_seq=17,
    phase=ChunkPhase.PORT_SCAN,
    technique=Technique.SYN,
    targets=[IPv4Address("198.51.100.7"), IPv4Address("198.51.100.23")],
    ports=[443, 22, 8080],
    pps=1000,
    timeout_ms=1500,
    retries=1,
    seed=91823,
    attestation_id=31,
    scope=[(IPv4Address("198.51.100.0"), IPv4Address("198.51.100.255"))],
)
STATUS: dict[str, Any] = {
    "vantage_point": "default",
    "egress_address": IPv4Address("203.0.113.9"),
    "syn_capable": True,
    "icmp_capable": True,
    "allow_non_public_targets": False,
}
FINDINGS = Findings(
    scan_id=4812,
    chunk_seq=17,
    hosts=[
        HostFinding(
            address=IPv4Address("198.51.100.7"),
            evidence={
                "tcp:443": Evidence(at=datetime(2026, 11, 15, 10, 0, tzinfo=UTC), rtt_ms=12.5)
            },
            open_ports=[443],
        )
    ],
)

# Every message, with the adapter of the queue it travels on.
EXAMPLES: list[tuple[TypeAdapter[Any], BaseModel]] = [
    (messages.WORK, CHUNK),
    (
        messages.WORK,
        CHUNK.model_copy(
            update={
                "phase": ChunkPhase.DISCOVERY,
                "ports": [],
                "discovery": Discovery(icmp_echo=True, tcp_ports=[80, 443]),
            }
        ),
    ),
    (messages.CONTROL, Cancel(scan_id=4812)),
    (messages.RESULTS, Hello(**STATUS)),
    (
        messages.RESULTS,
        Heartbeat(**STATUS, probes_sent=10, receive_drops=0, current_rate_pps=990.5),
    ),
    (messages.RESULTS, FINDINGS),
    (
        messages.RESULTS,
        ChunkDone(
            scan_id=4812,
            chunk_seq=17,
            probes_sent=6,
            receive_drops=0,
            counts=[HostCounts(address=IPv4Address("198.51.100.7"), closed=2, filtered=0)],
        ),
    ),
    (messages.RESULTS, ChunkRejected(scan_id=4812, chunk_seq=17, reason="out_of_scope")),
    (messages.COMMANDS, ScanCreated(scan_id=4812)),
    (messages.COMMANDS, CancelRequested(scan_id=4812)),
    (messages.COMMANDS, PhaseRetryRequested(scan_id=4812)),
    (messages.COMMANDS, EnrichmentRerunRequested(scan_id=4812)),
    (messages.COMMANDS, DatasetUpdateRequested(dataset="geolite2-city")),
    (messages.COMMANDS, DatasetUpdateRequested()),
    (messages.COMMANDS, TracerouteRequested(run_id=31)),
    (messages.COMMANDS, ClassificationRerunRequested(scan_id=4812)),
    (
        messages.TRACEROUTE_WORK,
        TracerouteJob(
            run_id=31,
            target=IPv4Address("198.51.100.7"),
            method=TracerouteMethod.TCP,
            port=443,
            max_hops=30,
            probes_per_hop=3,
            timeout_ms=1000,
            pps=50,
            attestation_id=9,
            scope=[(IPv4Address("198.51.100.0"), IPv4Address("198.51.100.255"))],
        ),
    ),
    (
        messages.RESULTS,
        TracerouteHop(
            run_id=31,
            ttl=4,
            replies=[
                HopReply(
                    probe=0, address=IPv4Address("192.0.2.1"), rtt_ms=7.5, kind="time_exceeded"
                ),
                HopReply(probe=1),
            ],
        ),
    ),
    (messages.RESULTS, TracerouteDone(run_id=31, reached=True, probes_sent=36)),
    (messages.RESULTS, TracerouteRejected(run_id=31, reason="out_of_scope")),
    (
        messages.EVENTS,
        TracerouteHopEvent(
            project_id=3,
            run_id=31,
            target=IPv4Address("198.51.100.7"),
            state="running",
            ttl=4,
            responders=[
                HopRow(address=IPv4Address("10.0.0.1"), rtt_ms=[1.2, None], unlocatable=True)
            ],
            lost=1,
        ),
    ),
    (
        messages.EVENTS,
        ScanProgress(
            scan_id=4812,
            project_id=3,
            state="running",
            phase="port_scan",
            chunks_done=4,
            chunks_total=26,
            probes_sent=40_000,
        ),
    ),
    (
        messages.EVENTS,
        HostsChanged(
            scan_id=4812,
            project_id=3,
            hosts=[HostRow(address=IPv4Address("198.51.100.7"), reachable=True, open_port_count=1)],
        ),
    ),
]


@pytest.mark.parametrize(("adapter", "message"), EXAMPLES)
def test_message_survives_the_trip_through_json(
    adapter: TypeAdapter[Any], message: BaseModel
) -> None:
    body = message.model_dump_json().encode()

    received = adapter.validate_json(body)

    assert type(received) is type(message)
    assert received == message


@pytest.mark.parametrize(("adapter", "message"), EXAMPLES)
def test_every_message_has_a_version_and_an_identifier(
    adapter: TypeAdapter[Any], message: BaseModel
) -> None:
    body = json.loads(message.model_dump_json())

    assert body["schema_version"] == 1
    assert len(body["message_id"]) == 36
    assert isinstance(body["type"], str)
    # Everything about a scan carries its identifier, for log correlation.
    # A traceroute message carries its run's identifier instead.
    if "run_id" in body:
        assert body["run_id"] == 31
    elif body["type"] not in ("hello", "heartbeat", "dataset_update_requested"):
        assert body["scan_id"] == 4812


def test_message_identifiers_are_unique() -> None:
    assert ScanCreated(scan_id=1).message_id != ScanCreated(scan_id=1).message_id


def test_the_chunk_carries_the_scope_the_scanner_checks() -> None:
    body = json.loads(CHUNK.model_dump_json())

    assert body["scope"] == [["198.51.100.0", "198.51.100.255"]]
    assert body["targets"] == ["198.51.100.7", "198.51.100.23"]
    assert body["attestation_id"] == 31
    assert (body["phase"], body["technique"]) == ("port_scan", "syn")


# --- What must not validate: such a message goes to the dead-letter queue ---


def changed(message: BaseModel, **changes: Any) -> bytes:
    return json.dumps({**json.loads(message.model_dump_json()), **changes}).encode()


@pytest.mark.parametrize(("adapter", "message"), EXAMPLES)
def test_unknown_schema_version_is_rejected(adapter: TypeAdapter[Any], message: BaseModel) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_json(changed(message, schema_version=2))


@pytest.mark.parametrize(("adapter", "message"), EXAMPLES)
def test_unknown_field_is_rejected(adapter: TypeAdapter[Any], message: BaseModel) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_json(changed(message, surprise=True))


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b"{}",
        changed(FINDINGS, type="no_such_type"),
        changed(FINDINGS, scan_id="four"),
        changed(FINDINGS, hosts=[{"address": "not-an-address"}]),
        changed(FINDINGS, hosts=[{"address": "198.51.100.7", "open_ports": [70000]}]),
        changed(FINDINGS, hosts=[{"address": "198.51.100.7", "open_ports": [0]}]),
    ],
)
def test_invalid_result_is_rejected(body: bytes) -> None:
    with pytest.raises(ValidationError):
        messages.RESULTS.validate_json(body)


def test_a_message_is_accepted_on_its_own_queue_only() -> None:
    chunk = CHUNK.model_dump_json()

    for adapter in (
        messages.RESULTS,
        messages.COMMANDS,
        messages.EVENTS,
        messages.CONTROL,
        messages.TRACEROUTE_WORK,
    ):
        with pytest.raises(ValidationError):
            adapter.validate_json(chunk)


def test_a_traceroute_job_is_not_a_chunk_and_holds_a_scope() -> None:
    job = TracerouteJob(
        run_id=1,
        target=IPv4Address("198.51.100.7"),
        method=TracerouteMethod.UDP,
        port=33434,
        max_hops=30,
        probes_per_hop=3,
        timeout_ms=1000,
        pps=50,
        attestation_id=9,
        scope=[(IPv4Address("198.51.100.0"), IPv4Address("198.51.100.255"))],
    )
    body = job.model_dump_json()

    with pytest.raises(ValidationError):
        messages.WORK.validate_json(body)
    for field, value in [("max_hops", 0), ("max_hops", 65), ("probes_per_hop", 0), ("pps", 0)]:
        with pytest.raises(ValidationError):
            TracerouteJob.model_validate({**job.model_dump(), field: value})


@pytest.mark.parametrize(
    "changes",
    [{"pps": 0}, {"timeout_ms": 0}, {"retries": -1}, {"ports": [65536]}, {"scope": [["1.1.1.1"]]}],
)
def test_invalid_chunk_is_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        messages.WORK.validate_json(changed(CHUNK, **changes))
