"""Live updates: from the broker to the browser (architecture section 12.4).

The API holds one exclusive queue on ``ndm.events`` and hands every event to
each connected browser as a server-sent event. Events are not kept and not
replayed: after a reconnect the browser reloads its view through REST.
"""

import asyncio
import contextlib

import structlog
from pydantic import SecretStr, ValidationError

from ndm.core import broker, messages

log = structlog.get_logger(__name__)

# Events a slow browser may have waiting. Beyond that its stream is closed and
# it reconnects and reloads, which is cheaper than holding events for it.
QUEUE_SIZE = 1000
RECONNECT_SECONDS = 5

# What a subscriber's queue holds: (event type, JSON body), or None to end the stream.
Item = tuple[str, str] | None


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Item]] = set()
        self._task: asyncio.Task[None] | None = None

    # --- Browsers ---

    def subscribe(self) -> asyncio.Queue[Item]:
        queue: asyncio.Queue[Item] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Item]) -> None:
        self._subscribers.discard(queue)

    def dispatch(self, body: bytes) -> None:
        """Hand one event from the broker to every connected browser.

        A body that is not a known event is dropped: only what the models
        describe is sent to a browser.
        """
        try:
            event = messages.EVENTS.validate_json(body)
        except ValidationError:
            log.warning("invalid_event_dropped", size=len(body))
            return
        item = (event.type, event.model_dump_json(exclude={"message_id", "schema_version"}))
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                # End this browser's stream; it will reconnect and reload.
                self._subscribers.discard(queue)
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                queue.put_nowait(None)

    # --- Broker ---

    def start(self, url: SecretStr | None) -> None:
        """Start listening to the broker in the background. Without a broker
        URL there are no live updates and the rest of the API works as usual."""
        if url is not None and self._task is None:
            self._task = asyncio.create_task(self._listen(url))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    async def _listen(self, url: SecretStr) -> None:
        while True:
            try:
                connection = await broker.connect(url)
                async with connection:
                    channel = await connection.channel()
                    await channel.declare_exchange(broker.EXCHANGE_EVENTS, "topic", durable=False)
                    queue = await broker.declare_private_queue(channel, broker.EXCHANGE_EVENTS)
                    async with queue.iterator(no_ack=True) as deliveries:
                        async for delivery in deliveries:
                            self.dispatch(delivery.body)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # The broker is away. Try again; live updates resume when it is back.
                log.warning("event_listener_disconnected", error=type(error).__name__)
                await asyncio.sleep(RECONNECT_SECONDS)
