import hashlib
import ipaddress
import math
import random
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from protego import Protego
from redis import Redis

from rss_reader.config import Settings


class CrawlError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        delay: float = 0,
        status: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.delay = delay
        self.status = status


class Deferred(CrawlError):
    """Pacing is scheduling, not a failed attempt."""

    def __init__(self, delay: float):
        super().__init__("pacing", "Waiting for the origin request window", delay=delay)


def validate_url(value: str) -> str:
    if len(value) > 2048 or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("URL is too long or contains whitespace")
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Use an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs must not contain credentials")
    if parsed.port not in (None, 80, 443):
        raise ValueError("Only standard HTTP and HTTPS ports are supported")
    # HTTPX provides consistent IDNA and escaping without fetching the URL.
    normalized = httpx.URL(value).copy_with(fragment=None)
    return str(normalized)


def origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def pinned_url(url: str) -> tuple[str, str, str]:
    """Validate DNS answers and connect to that IP to prevent DNS rebinding."""
    url = validate_url(url)
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise CrawlError("dns", "Could not resolve the feed host", retryable=True) from exc
    ips = [address[4][0] for address in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise CrawlError("unsafe_url", "Feed hosts must resolve only to public IP addresses")
    return str(httpx.URL(url).copy_with(host=ips[0])), parsed.netloc, host


def retry_after(value: str | None, now: datetime | None = None) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            return max(0, (date - (now or datetime.now(UTC))).total_seconds())
        except ValueError, TypeError, OverflowError:
            return 0


# Redis time keeps spacing consistent across worker processes and host clock drift.
RESERVE_REQUEST = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local last = tonumber(redis.call('GET', KEYS[1]) or '0')
local cooldown = tonumber(redis.call('GET', KEYS[2]) or '0')
local slot = math.max(now, last + tonumber(ARGV[1]), cooldown)
local wait = slot - now
if wait <= 5 then
  redis.call('SET', KEYS[1], tostring(slot), 'EX', math.ceil(tonumber(ARGV[1]) + 3600))
end
return tostring(wait)
"""

SET_COOLDOWN = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local until_time = math.max(tonumber(redis.call('GET', KEYS[1]) or '0'), now + tonumber(ARGV[1]))
redis.call('SET', KEYS[1], tostring(until_time), 'EX', math.ceil(until_time - now) + 1)
"""

COOLDOWN_REMAINING = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
return tostring(math.max(0, tonumber(redis.call('GET', KEYS[1]) or '0') - now))
"""


class OriginGate:
    def __init__(self, redis: Redis, settings: Settings):
        self.redis = redis
        self.settings = settings

    def key(self, url: str, suffix: str) -> str:
        digest = hashlib.sha256(origin(url).encode()).hexdigest()
        return f"rss:origin:{digest}:{suffix}"

    def wait(self, url: str, policy_delay: float = 0) -> None:
        known_delay = cast(bytes | str | None, self.redis.get(self.key(url, "policy-delay")))
        if known_delay:
            policy_delay = max(policy_delay, float(known_delay))
        delay = max(self.settings.request_delay_seconds, policy_delay)
        delay += random.uniform(0, self.settings.request_jitter_seconds)
        wait = float(
            cast(
                str,
                self.redis.eval(
                    RESERVE_REQUEST, 2, self.key(url, "last"), self.key(url, "cooldown"), str(delay)
                ),
            )
        )
        if wait > 5:
            raise Deferred(wait)
        if wait > 0:
            time.sleep(wait)
        # A different worker may have received 429 while this one was waiting
        # for its reserved slot. Recheck before sending the pending request.
        cooldown = float(
            cast(str, self.redis.eval(COOLDOWN_REMAINING, 1, self.key(url, "cooldown")))
        )
        if cooldown > 0:
            raise Deferred(cooldown)

    def cooldown(self, url: str, seconds: float) -> None:
        self.redis.eval(SET_COOLDOWN, 1, self.key(url, "cooldown"), str(max(1, seconds)))


@dataclass
class FetchResult:
    status: int
    body: bytes
    headers: dict[str, str]
    url: str


class FeedHTTPClient:
    def __init__(self, settings: Settings, redis: Redis, client: httpx.Client | None = None):
        self.settings = settings
        self.redis = redis
        self.gate = OriginGate(redis, settings)
        self.client = client or httpx.Client(
            timeout=settings.request_timeout_seconds,
            proxy=settings.proxy_url,
            trust_env=False,
            follow_redirects=False,
            # A pinned IP must never reuse another hostname's TLS connection.
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self.deadline = time.monotonic() + settings.job_lease_seconds - 45

    def close(self) -> None:
        self.client.close()

    def _request(self, url: str, headers: dict[str, str], policy_delay: float = 0) -> FetchResult:
        if time.monotonic() >= self.deadline:
            raise CrawlError("timeout", "Crawl time budget exceeded", retryable=True)
        self.gate.wait(url, policy_delay)
        try:
            target, host_header, tls_host = pinned_url(url)
        except ValueError as exc:
            raise CrawlError("unsafe_url", "Invalid feed or redirect URL") from exc
        try:
            with self.client.stream(
                "GET",
                target,
                headers={**headers, "Host": host_header, "User-Agent": self.settings.user_agent},
                extensions={"sni_hostname": tls_host},
            ) as response:
                # Never read a potentially unbounded error or redirect response.
                if response.status_code == 429 or response.status_code >= 500:
                    delay = max(
                        self.settings.retry_base_seconds,
                        retry_after(response.headers.get("retry-after")),
                    )
                    self.gate.cooldown(url, delay)
                    raise CrawlError(
                        "http_transient",
                        f"Publisher returned HTTP {response.status_code}",
                        retryable=True,
                        delay=delay,
                        status=response.status_code,
                    )
                body = bytearray()
                if response.status_code == 200:
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.max_response_bytes:
                            raise CrawlError(
                                "response_size", "Response exceeds configured size limit"
                            )
                        if time.monotonic() >= self.deadline:
                            raise CrawlError(
                                "timeout", "Crawl time budget exceeded", retryable=True
                            )
                return FetchResult(response.status_code, bytes(body), dict(response.headers), url)
        except httpx.HTTPError as exc:
            # Exception text may contain proxy credentials or private feed parameters.
            raise CrawlError("network", "HTTP connection or timeout error", retryable=True) from exc

    def _robots(self, url: str) -> tuple[Protego, float]:
        key = self.gate.key(url, "robots")
        cached = self.redis.get(key)
        if cached is None:
            robots_url = origin(url) + "/robots.txt"
            for _ in range(6):
                result = self._request(robots_url, {})
                if result.status in (301, 302, 303, 307, 308):
                    location = result.headers.get("location")
                    if not location:
                        raise CrawlError(
                            "robots", "Robots redirect has no location", retryable=True
                        )
                    robots_url = urljoin(robots_url, location)
                    continue
                if result.status == 200:
                    cached = result.body.decode("utf-8", errors="replace")
                    if len(result.body) > 512_000:
                        raise CrawlError("robots", "Robots policy exceeds parsing limit")
                elif result.status in (404, 410):
                    cached = "User-agent: *\nAllow: /"
                elif result.status in (401, 403):
                    cached = "User-agent: *\nDisallow: /"
                else:
                    raise CrawlError("robots", "Could not obtain robots policy", retryable=True)
                self.redis.set(key, cached, ex=self.settings.robots_cache_seconds)
                break
            else:
                raise CrawlError("robots", "Too many robots redirects", retryable=True)
        if isinstance(cached, bytes):
            cached = cached.decode("utf-8")
        policy = Protego.parse(str(cached))
        delay = policy.crawl_delay(self.settings.user_agent) or 0
        rate = policy.request_rate(self.settings.user_agent)
        if rate and rate.requests:
            # A whole window between requests is conservative and prevents bursts.
            delay = max(delay, rate.seconds)
        self.redis.set(
            self.gate.key(url, "policy-delay"), str(delay), ex=self.settings.robots_cache_seconds
        )
        return policy, float(delay)

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        for _ in range(6):
            policy, delay = self._robots(url)
            if not policy.can_fetch(url, self.settings.user_agent):
                raise CrawlError("robots_blocked", "Crawling this feed is disallowed by robots.txt")
            result = self._request(url, headers or {}, delay)
            if result.status in (301, 302, 303, 307, 308):
                location = result.headers.get("location")
                if not location:
                    raise CrawlError("redirect", "Feed redirect has no location")
                redirected = urljoin(url, location)
                try:
                    redirected = validate_url(redirected)
                except ValueError as exc:
                    raise CrawlError("unsafe_url", "Invalid redirect URL") from exc
                if origin(redirected) != origin(url):
                    headers = {}
                url = redirected
                continue
            if result.status not in (200, 304):
                raise CrawlError(
                    "http", f"Publisher returned HTTP {result.status}", status=result.status
                )
            return result
        raise CrawlError("redirect", "Too many feed redirects")
