import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import pytest
from redis import Redis

from rss_reader.network import RESERVE_REQUEST, Deferred, OriginGate


@pytest.fixture
def redis_client():
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run real Redis coordination tests")
    with Redis.from_url(url) as client:
        client.ping()
        yield client


def test_concurrent_reservations_are_spaced_and_cooldown_is_shared(redis_client, settings):
    url = f"https://{uuid4().hex}.example/feed"
    gate = OriginGate(redis_client, settings)
    keys = (gate.key(url, "last"), gate.key(url, "cooldown"))
    try:

        def reserve(_):
            return float(redis_client.eval(RESERVE_REQUEST, 2, *keys, "0.2"))

        with ThreadPoolExecutor(max_workers=4) as pool:
            waits = sorted(pool.map(reserve, range(4)))
        assert waits[0] < 0.1
        assert waits[-1] >= 0.5
        gate.cooldown(url, 30)
        other_worker = OriginGate(redis_client, settings)
        with patch("rss_reader.network.time.sleep") as sleep, pytest.raises(Deferred):
            other_worker.wait(url)
        sleep.assert_not_called()
        ttl_before = redis_client.ttl(keys[1])
        gate.cooldown(url, 1)
        assert redis_client.ttl(keys[1]) >= ttl_before - 1
    finally:
        redis_client.delete(*keys)
