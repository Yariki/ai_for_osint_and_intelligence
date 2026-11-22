"""A stand-in for the registries' download servers, serving the fixture files."""

import gzip
import hashlib
from pathlib import Path

import httpx

from ndm.orchestrator.datasets import rir_delegation, rir_objects

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rir"
LAST_MODIFIED = "Sun, 15 Nov 2026 23:20:53 GMT"


class FakeRegistries:
    """Serves the fixture files at the registries' URLs."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.last_modified = LAST_MODIFIED
        self.requests: list[tuple[str, str]] = []
        for registry, url in rir_delegation.SOURCES.values():
            self.files[url] = (FIXTURES / f"delegated-{registry}-extended-latest").read_bytes()
        for urls in rir_objects.DOWNLOADS.values():
            for url in urls:
                name = url.rsplit("/", 1)[1].removesuffix(".gz")
                self.files[url] = gzip.compress((FIXTURES / name).read_bytes())

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer))

    def fetched(self, part: str) -> int:
        return sum(method == "GET" and part in url for method, url in self.requests)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        if url.endswith(".md5"):
            content = self.files.get(url.removesuffix(".md5"))
            if content is None:
                return httpx.Response(404)
            name = url.rsplit("/", 1)[1].removesuffix(".md5")
            return httpx.Response(200, text=f"MD5 ({name}) = {hashlib.md5(content).hexdigest()}\n")
        if url not in self.files:
            return httpx.Response(404)
        headers = {"last-modified": self.last_modified}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        return httpx.Response(200, content=self.files[url], headers=headers)


def supply_operator_files(directory: Path) -> None:
    """Put the bulk files of ARIN and LACNIC where an operator would."""
    for name in rir_objects.OPERATOR_FILES.values():
        (directory / name).write_bytes((FIXTURES / name).read_bytes())
