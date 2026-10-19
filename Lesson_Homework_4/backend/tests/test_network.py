import socket
from datetime import UTC, datetime
from unittest.mock import Mock

import httpx
import pytest

from rss_reader.network import CrawlError, Deferred, FeedHTTPClient, pinned_url, retry_after


class Cache:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, **kwargs):
        self.data[key] = value


@pytest.fixture
def make_client(settings, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    clients = []

    def create(handler):
        client = FeedHTTPClient(
            settings, Cache(), httpx.Client(transport=httpx.MockTransport(handler))
        )
        client.gate.wait = Mock()
        client.gate.cooldown = Mock()
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()


def test_robots_wildcards_and_specific_user_agent(make_client):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(
            200,
            text="User-agent: *\nAllow: /\nUser-agent: RespectfulRSSReader\nDisallow: /*/feed$\n",
        )

    with pytest.raises(CrawlError) as error:
        make_client(handler).fetch("https://example.com/private/feed")
    assert error.value.code == "robots_blocked"
    assert requests == ["/robots.txt"]


def test_robots_cached_and_delay_applied(make_client):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /\nCrawl-delay: 8\nRequest-rate: 2/20\n"
            )
        return httpx.Response(200, text="<rss/>")

    client = make_client(handler)
    client.fetch("https://example.com/feed")
    client.fetch("https://example.com/feed")
    assert requests.count("/robots.txt") == 1
    assert client.gate.wait.call_args.args == ("https://example.com/feed", 20.0)


@pytest.mark.parametrize("status,allowed", [(404, True), (410, True), (401, False), (403, False)])
def test_missing_and_denied_robots(make_client, status, allowed):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(status if request.url.path == "/robots.txt" else 200)

    client = make_client(handler)
    if allowed:
        client.fetch("https://example.com/feed")
        assert requests == ["/robots.txt", "/feed"]
    else:
        with pytest.raises(CrawlError, match="disallowed"):
            client.fetch("https://example.com/feed")
        assert requests == ["/robots.txt"]


def test_robots_outage_does_not_fetch_feed(make_client):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(503)

    with pytest.raises(CrawlError) as error:
        make_client(handler).fetch("https://example.com/feed")
    assert error.value.retryable and requests == ["/robots.txt"]


def test_redirect_destination_gets_its_own_robots_check(make_client):
    requests = []

    def handler(request):
        host = request.headers["host"]
        requests.append((host, request.url.path))
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\n" + ("Disallow: /" if host == "other.example" else "Allow: /"),
            )
        return httpx.Response(302, headers={"Location": "https://other.example/feed"})

    with pytest.raises(CrawlError, match="disallowed"):
        make_client(handler).fetch("https://example.com/feed")
    assert requests == [
        ("example.com", "/robots.txt"),
        ("example.com", "/feed"),
        ("other.example", "/robots.txt"),
    ]


def test_http_429_sets_shared_cooldown_and_preserves_retry_after(make_client):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429, headers={"Retry-After": "600"})

    client = make_client(handler)
    with pytest.raises(CrawlError) as error:
        client.fetch("https://example.com/feed")
    assert error.value.status == 429 and error.value.delay == 600
    client.gate.cooldown.assert_called_once_with("https://example.com/feed", 600)


def test_response_size_limit(make_client, settings):
    settings.max_response_bytes = 1024

    def handler(request):
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=b"x" * 2048)
        )

    with pytest.raises(CrawlError, match="size limit"):
        make_client(handler).fetch("https://example.com/feed")


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"]
)
def test_private_dns_targets_are_rejected(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(0, 0, 0, "", (ip, 443))])
    with pytest.raises(CrawlError, match="public IP"):
        pinned_url("https://example.com/feed")


def test_retry_after_http_date_and_invalid_values():
    now = datetime(2026, 10, 19, 11, tzinfo=UTC)
    assert retry_after("Mon, 19 Oct 2026 11:10:00 GMT", now) == 600
    for value in ("invalid", "-1", "nan", "inf", None):
        assert retry_after(value, now) == 0


def test_long_pacing_window_defers_instead_of_sleeping(settings):
    from rss_reader.network import OriginGate

    cache = Mock()
    cache.get.return_value = None
    cache.eval.return_value = b"60.5"
    with pytest.raises(Deferred) as error:
        OriginGate(cache, settings).wait("https://example.com/feed")
    assert error.value.delay == 60.5


def test_waiting_request_rechecks_cooldowns_from_other_workers(settings, monkeypatch):
    from rss_reader.network import OriginGate

    cache = Mock()
    cache.get.return_value = None
    cache.eval.side_effect = [b"1", b"120"]
    monkeypatch.setattr("rss_reader.network.time.sleep", Mock())
    with pytest.raises(Deferred) as error:
        OriginGate(cache, settings).wait("https://example.com/feed")
    assert error.value.delay == 120
