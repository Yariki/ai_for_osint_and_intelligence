"""Broker topology and the rules every publisher and consumer follows
(architecture sections 9.1 and 9.2).

- Work and result queues are durable quorum queues with a delivery limit and
  the dead-letter exchange. A message delivered that many times without an
  acknowledgement moves to the dead-letter queue instead of looping. The broker
  counts only deliveries to a consumer that went away without answering; a
  consumer that fails on a message itself counts its attempts with
  ``FailedDeliveries``, which applies the same limit.
- Messages are persistent and published with confirms. Consumers acknowledge
  by hand, after processing.
- A message that fails validation is rejected without requeue and goes
  straight to the dead-letter queue.
- Every process declares the topology at start-up. Declaring is idempotent.

Queue names contain the vantage-point name, so a second scanner later gets its
own queues without a change to the topology.
"""

import hashlib
from typing import Any

import aio_pika
import structlog
from aio_pika.abc import (
    AbstractChannel,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

log = structlog.get_logger(__name__)

EXCHANGE_SCANNER = "ndm.scanner"  # direct: chunks to one vantage point
EXCHANGE_CONTROL = "ndm.scanner.control"  # fanout: cancel, to every scanner
EXCHANGE_RESULTS = "ndm.results"  # direct: scanner to orchestrator
EXCHANGE_COMMANDS = "ndm.commands"  # direct: api to orchestrator
EXCHANGE_EVENTS = "ndm.events"  # topic: transient UI events
EXCHANGE_DEAD = "ndm.dlx"  # fanout: dead-lettered messages

QUEUE_RESULTS = "orchestrator.results"
QUEUE_COMMANDS = "orchestrator.commands"
QUEUE_DEAD = "ndm.dead"

DELIVERY_LIMIT = 5

_QUORUM = {"x-queue-type": "quorum"}
_WORK_QUEUE = {
    **_QUORUM,
    "x-delivery-limit": DELIVERY_LIMIT,
    "x-dead-letter-exchange": EXCHANGE_DEAD,
}


def chunk_queue(vantage_point: str) -> str:
    return f"scanner.{vantage_point}.chunks"


def traceroute_queue(vantage_point: str) -> str:
    """Traceroutes have a queue of their own, so they never wait behind scan chunks."""
    return f"scanner.{vantage_point}.traceroutes"


async def connect(url: SecretStr | None) -> AbstractRobustConnection:
    """Connect to the broker. The connection reconnects by itself after a failure."""
    if url is None:
        raise RuntimeError("the broker URL is not set (NDM_BROKER_URL)")
    return await aio_pika.connect_robust(url.get_secret_value())


async def declare_topology(channel: AbstractChannel, vantage_point: str) -> None:
    """Declare every durable exchange and queue. Safe to repeat."""
    scanner = await channel.declare_exchange(EXCHANGE_SCANNER, "direct", durable=True)
    results = await channel.declare_exchange(EXCHANGE_RESULTS, "direct", durable=True)
    commands = await channel.declare_exchange(EXCHANGE_COMMANDS, "direct", durable=True)
    dead = await channel.declare_exchange(EXCHANGE_DEAD, "fanout", durable=True)
    # Cancel broadcasts and UI events are transient: losing one loses no data.
    await channel.declare_exchange(EXCHANGE_CONTROL, "fanout", durable=False)
    await channel.declare_exchange(EXCHANGE_EVENTS, "topic", durable=False)

    async def queue(name: str, arguments: dict[str, Any]) -> AbstractQueue:
        return await channel.declare_queue(name, durable=True, arguments=arguments)

    chunks = await queue(chunk_queue(vantage_point), _WORK_QUEUE)
    await chunks.bind(scanner, routing_key=chunks.name)
    traceroutes = await queue(traceroute_queue(vantage_point), _WORK_QUEUE)
    await traceroutes.bind(scanner, routing_key=traceroutes.name)
    await (await queue(QUEUE_RESULTS, _WORK_QUEUE)).bind(results, routing_key=QUEUE_RESULTS)
    await (await queue(QUEUE_COMMANDS, _WORK_QUEUE)).bind(commands, routing_key=QUEUE_COMMANDS)
    # The dead-letter queue has no delivery limit: it is the last stop.
    await (await queue(QUEUE_DEAD, _QUORUM)).bind(dead)


async def declare_private_queue(channel: AbstractChannel, exchange: str) -> AbstractQueue:
    """An exclusive queue of this process on a transient exchange.

    Used by a scanner for the cancel broadcast and by the API for UI events.
    The queue goes away with the connection.
    """
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await queue.bind(exchange, routing_key="#")
    return queue


async def publish(
    channel: AbstractChannel,
    exchange: str,
    routing_key: str,
    message: BaseModel,
    *,
    persistent: bool = True,
) -> None:
    """Publish a message and wait for the broker's confirmation.

    Raises if the broker does not confirm, or if no queue takes the message.
    """
    mode = aio_pika.DeliveryMode.PERSISTENT if persistent else aio_pika.DeliveryMode.NOT_PERSISTENT
    body = aio_pika.Message(
        message.model_dump_json().encode(), content_type="application/json", delivery_mode=mode
    )
    target = await channel.get_exchange(exchange, ensure=False)
    # Transient messages may find no queue (no browser connected); that is not an error.
    await target.publish(body, routing_key=routing_key, mandatory=persistent)


async def decode[T](message: AbstractIncomingMessage, adapter: TypeAdapter[T]) -> T | None:
    """Validate a delivery against the models of its queue.

    An invalid body, or one with an unknown schema version, is rejected without
    requeue, which sends it to the dead-letter queue; the result is then None.
    The caller acknowledges a valid message after it has processed it.
    """
    try:
        return adapter.validate_json(message.body)
    except ValidationError as error:
        log.warning(
            "invalid_message_rejected", queue=message.routing_key, errors=error.error_count()
        )
        await message.reject(requeue=False)
        return None


class FailedDeliveries:
    """Counts a consumer's failed attempts at each message, and dead-letters a
    message after ``DELIVERY_LIMIT`` of them.

    RabbitMQ 4 does not count a nack with requeue towards a quorum queue's
    delivery limit. Measured on 4.3.7: a message nacked with requeue was
    delivered again more than a thousand times in three seconds and never
    reached the dead-letter queue, while one whose consumer went away without
    an answer was dead-lettered after the limit. Without this count, a message
    the consumer fails on would loop for ever.

    The count lives in the consuming process. A restart loses it, but the
    restart is itself a delivery the broker counts.
    """

    def __init__(self, limit: int = DELIVERY_LIMIT) -> None:
        self.limit = limit
        self.attempts: dict[bytes, int] = {}

    @staticmethod
    def _key(message: AbstractIncomingMessage) -> bytes:
        return hashlib.sha256(message.body).digest()

    async def failed(self, message: AbstractIncomingMessage) -> None:
        """Offer the message again, or after the last attempt dead-letter it."""
        key = self._key(message)
        attempts = self.attempts.get(key, 0) + 1
        if attempts < self.limit:
            self.attempts[key] = attempts
            await message.nack(requeue=True)
            return
        self.attempts.pop(key, None)
        log.warning("message_dead_lettered", queue=message.routing_key, attempts=attempts)
        await message.reject(requeue=False)

    def succeeded(self, message: AbstractIncomingMessage) -> None:
        """Forget the failed attempts at a message that has now been processed."""
        self.attempts.pop(self._key(message), None)
