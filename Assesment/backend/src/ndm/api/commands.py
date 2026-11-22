"""Commands from the API to the orchestrator.

A command says only that something changed, and carries identifiers alone.
The API commits to the database first and publishes afterwards. If the publish
is lost, or the broker is away, nothing is lost: the orchestrator's tick finds
the change in the database a few seconds later (AD1). So publishing never
fails a request; it is logged and given up.
"""

import asyncio

import structlog
from aio_pika.abc import AbstractChannel, AbstractRobustConnection
from fastapi import Request
from pydantic import SecretStr

from ndm.core import broker
from ndm.core.messages import Command

log = structlog.get_logger(__name__)

# A broker that does not answer must not hold up a request.
TIMEOUT_SECONDS = 2


class CommandPublisher:
    """Publishes commands over a connection that is opened on first use."""

    def __init__(self, url: SecretStr | None, vantage_point: str) -> None:
        self._url = url
        self._vantage_point = vantage_point
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None

    async def _open(self) -> AbstractChannel:
        if self._channel is None or self._channel.is_closed:
            self._connection = await broker.connect(self._url)
            self._channel = await self._connection.channel()
            await broker.declare_topology(self._channel, self._vantage_point)
        return self._channel

    async def publish(self, command: Command) -> bool:
        """Publish a command. Returns False, and raises nothing, if it could not be sent."""
        if self._url is None:
            return False
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                channel = await self._open()
                await broker.publish(
                    channel, broker.EXCHANGE_COMMANDS, broker.QUEUE_COMMANDS, command
                )
            return True
        except Exception as error:
            log.warning(
                "command_not_published",
                command=command.type,
                scan_id=getattr(command, "scan_id", None),
                error=type(error).__name__,
            )
            await self.close()
            return False

    async def close(self) -> None:
        connection, self._connection, self._channel = self._connection, None, None
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                log.debug("broker_connection_close_failed")


def publisher(request: Request) -> CommandPublisher:
    """The command publisher of the running application."""
    commands: CommandPublisher = request.app.state.commands
    return commands


async def send(request: Request, command: Command) -> bool:
    """Publish a command after the caller has committed. Never raises."""
    return await publisher(request).publish(command)
