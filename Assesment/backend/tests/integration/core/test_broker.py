"""The broker topology and its delivery rules, against a real RabbitMQ (NFR-TST-1)."""

import json
from ipaddress import IPv4Address
from typing import Any

import aio_pika
import pytest
from aio_pika.abc import AbstractChannel
from aiormq.exceptions import ChannelPreconditionFailed

from ndm.core import broker, messages
from ndm.core.messages import (
    Cancel,
    ScanCreated,
    ScanProgress,
    TracerouteJob,
    TracerouteMethod,
)

VANTAGE = "test"
QUORUM_WITH_LIMIT = {
    "x-queue-type": "quorum",
    "x-delivery-limit": 5,
    "x-dead-letter-exchange": "ndm.dlx",
}


async def publish_raw(
    channel: AbstractChannel, exchange: str, routing_key: str, body: bytes
) -> None:
    target = await channel.get_exchange(exchange, ensure=False)
    await target.publish(aio_pika.Message(body), routing_key=routing_key)


# --- Topology (architecture section 9.1) ---


@pytest.mark.parametrize(
    ("name", "kind", "durable"),
    [
        ("ndm.scanner", "direct", True),
        ("ndm.results", "direct", True),
        ("ndm.commands", "direct", True),
        ("ndm.dlx", "fanout", True),
        ("ndm.scanner.control", "fanout", False),
        ("ndm.events", "topic", False),
    ],
)
async def test_exchanges_are_declared(
    topology: AbstractChannel, name: str, kind: str, durable: bool
) -> None:
    # Declaring again with the same properties succeeds only if they match.
    await topology.declare_exchange(name, kind, durable=durable)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("scanner.test.chunks", QUORUM_WITH_LIMIT),
        # Traceroutes have a queue of their own, with the same guarantees (architecture 7).
        ("scanner.test.traceroutes", QUORUM_WITH_LIMIT),
        ("orchestrator.results", QUORUM_WITH_LIMIT),
        ("orchestrator.commands", QUORUM_WITH_LIMIT),
        ("ndm.dead", {"x-queue-type": "quorum"}),
    ],
)
async def test_queues_are_durable_quorum_queues(
    topology: AbstractChannel, name: str, arguments: dict[str, Any]
) -> None:
    await topology.declare_queue(name, durable=True, arguments=arguments)


async def test_a_queue_with_other_arguments_would_be_refused(topology: AbstractChannel) -> None:
    """Shows that the test above really compares the arguments."""
    with pytest.raises(ChannelPreconditionFailed):
        await topology.declare_queue(
            "orchestrator.results", durable=True, arguments={"x-queue-type": "quorum"}
        )


async def test_messages_are_routed_to_their_queues(topology: AbstractChannel) -> None:
    await broker.publish(topology, "ndm.commands", "orchestrator.commands", ScanCreated(scan_id=7))
    await publish_raw(topology, "ndm.scanner", "scanner.test.chunks", b"chunk")
    await publish_raw(topology, "ndm.results", "orchestrator.results", b"result")

    commands = await topology.get_queue("orchestrator.commands")
    command = await commands.get(timeout=5)
    assert command is not None
    assert messages.COMMANDS.validate_json(command.body) == ScanCreated(
        scan_id=7, message_id=json.loads(command.body)["message_id"]
    )
    assert command.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    await command.ack()

    for name, body in [("scanner.test.chunks", b"chunk"), ("orchestrator.results", b"result")]:
        message = await (await topology.get_queue(name)).get(timeout=5)
        assert message is not None and message.body == body
        await message.ack()


async def test_declaring_a_second_time_changes_nothing(topology: AbstractChannel) -> None:
    await broker.publish(topology, "ndm.commands", "orchestrator.commands", ScanCreated(scan_id=7))

    await broker.declare_topology(topology, VANTAGE)
    await broker.declare_topology(topology, VANTAGE)

    # The message published before is still there, exactly once.
    commands = await topology.get_queue("orchestrator.commands")
    first = await commands.get(timeout=5)
    assert first is not None
    await first.ack()
    assert await commands.get(fail=False, timeout=1) is None


async def test_a_second_vantage_point_gets_its_own_queue(topology: AbstractChannel) -> None:
    await broker.declare_topology(topology, "second")

    await publish_raw(topology, "ndm.scanner", "scanner.second.chunks", b"for the second scanner")

    assert (
        await (await topology.get_queue("scanner.test.chunks")).get(fail=False, timeout=1) is None
    )
    message = await (await topology.get_queue("scanner.second.chunks")).get(timeout=5)
    assert message is not None
    await message.ack()
    await (await topology.get_queue("scanner.second.chunks")).delete(
        if_unused=False, if_empty=False
    )


# --- Dead-lettering (NFR-REL-4) ---


async def dead_letter(channel: AbstractChannel) -> aio_pika.abc.AbstractIncomingMessage | None:
    return await (await channel.get_queue("ndm.dead")).get(fail=False, timeout=5)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"type": "findings"}',
        ScanCreated(scan_id=1)
        .model_dump_json()
        .replace('"schema_version":1', '"schema_version":2')
        .encode(),
    ],
    ids=["not-json", "missing-fields", "unknown-schema-version"],
)
async def test_invalid_message_goes_to_the_dead_letter_queue(
    topology: AbstractChannel, body: bytes
) -> None:
    await publish_raw(topology, "ndm.commands", "orchestrator.commands", body)
    commands = await topology.get_queue("orchestrator.commands")
    delivery = await commands.get(timeout=5)
    assert delivery is not None

    assert await broker.decode(delivery, messages.COMMANDS) is None

    # Rejected without requeue: gone from its queue, present in the dead-letter queue.
    assert await commands.get(fail=False, timeout=1) is None
    dead = await dead_letter(topology)
    assert dead is not None and dead.body == body
    await dead.ack()


async def test_valid_message_is_decoded_and_left_for_the_caller_to_acknowledge(
    topology: AbstractChannel,
) -> None:
    await broker.publish(topology, "ndm.commands", "orchestrator.commands", ScanCreated(scan_id=9))
    delivery = await (await topology.get_queue("orchestrator.commands")).get(timeout=5)
    assert delivery is not None

    decoded = await broker.decode(delivery, messages.COMMANDS)

    assert isinstance(decoded, ScanCreated) and decoded.scan_id == 9
    await delivery.ack()
    assert await dead_letter(topology) is None


async def test_message_whose_consumer_goes_away_unanswered_goes_to_the_dead_letter_queue(
    broker_url: str, topology: AbstractChannel
) -> None:
    """The broker counts deliveries to a consumer that died holding the message."""
    await broker.publish(topology, "ndm.commands", "orchestrator.commands", ScanCreated(scan_id=5))

    deliveries = 0
    while True:
        connection = await aio_pika.connect(broker_url)
        commands = await (await connection.channel()).get_queue("orchestrator.commands")
        delivery = await commands.get(fail=False)
        await connection.close()  # without an answer
        if delivery is None:
            break
        deliveries += 1
        assert deliveries <= broker.DELIVERY_LIMIT + 1, "the message loops"

    assert deliveries >= broker.DELIVERY_LIMIT
    dead = await dead_letter(topology)
    assert dead is not None
    dead_command = messages.COMMANDS.validate_json(dead.body)
    assert isinstance(dead_command, ScanCreated) and dead_command.scan_id == 5
    await dead.ack()


async def test_message_its_consumer_fails_on_goes_to_the_dead_letter_queue_after_the_limit(
    topology: AbstractChannel,
) -> None:
    """RabbitMQ 4 does not count a nack with requeue; FailedDeliveries does."""
    await broker.publish(topology, "ndm.commands", "orchestrator.commands", ScanCreated(scan_id=6))
    commands = await topology.get_queue("orchestrator.commands")
    failures = broker.FailedDeliveries()

    deliveries = 0
    while (delivery := await commands.get(fail=False)) is not None:
        deliveries += 1
        assert deliveries <= broker.DELIVERY_LIMIT, "the message loops"
        await failures.failed(delivery)

    assert deliveries == broker.DELIVERY_LIMIT
    assert failures.attempts == {}
    dead = await dead_letter(topology)
    assert dead is not None
    dead_command = messages.COMMANDS.validate_json(dead.body)
    assert isinstance(dead_command, ScanCreated) and dead_command.scan_id == 6
    await dead.ack()


async def test_a_message_processed_after_failures_starts_its_count_again(
    topology: AbstractChannel,
) -> None:
    await broker.publish(topology, "ndm.commands", "orchestrator.commands", ScanCreated(scan_id=7))
    commands = await topology.get_queue("orchestrator.commands")
    failures = broker.FailedDeliveries()

    for _ in range(broker.DELIVERY_LIMIT - 1):
        delivery = await commands.get(timeout=5)
        assert delivery is not None
        await failures.failed(delivery)
    delivery = await commands.get(timeout=5)
    assert delivery is not None
    await delivery.ack()
    failures.succeeded(delivery)

    assert failures.attempts == {}
    assert await dead_letter(topology) is None


# --- Transient messages ---


async def test_cancel_broadcast_reaches_every_private_queue(topology: AbstractChannel) -> None:
    first = await broker.declare_private_queue(topology, "ndm.scanner.control")
    second = await broker.declare_private_queue(topology, "ndm.scanner.control")

    await broker.publish(topology, "ndm.scanner.control", "", Cancel(scan_id=3), persistent=False)

    for queue in (first, second):
        delivery = await queue.get(timeout=5)
        assert delivery is not None
        assert messages.CONTROL.validate_json(delivery.body).scan_id == 3
        await delivery.ack()


async def test_event_without_a_listener_is_dropped_without_an_error(
    topology: AbstractChannel,
) -> None:
    event = ScanProgress(
        scan_id=1,
        project_id=1,
        state="running",
        phase=None,
        chunks_done=0,
        chunks_total=0,
        probes_sent=0,
    )

    await broker.publish(topology, "ndm.events", "scan.1", event, persistent=False)


async def test_a_traceroute_job_reaches_the_traceroute_queue_only(
    topology: AbstractChannel,
) -> None:
    job = TracerouteJob(
        run_id=3,
        target=IPv4Address("8.8.8.8"),
        method=TracerouteMethod.ICMP,
        port=None,
        max_hops=30,
        probes_per_hop=3,
        timeout_ms=1000,
        pps=50,
        attestation_id=1,
        scope=[(IPv4Address("8.8.8.0"), IPv4Address("8.8.8.255"))],
    )

    await broker.publish(topology, broker.EXCHANGE_SCANNER, broker.traceroute_queue("test"), job)

    traceroutes = await topology.get_queue("scanner.test.traceroutes")
    chunks = await topology.get_queue("scanner.test.chunks")
    delivery = await traceroutes.get(timeout=5)
    assert messages.TRACEROUTE_WORK.validate_json(delivery.body) == job
    await delivery.ack()
    assert await chunks.get(fail=False) is None
