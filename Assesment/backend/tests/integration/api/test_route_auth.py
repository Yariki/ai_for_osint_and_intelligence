"""Check 1A.3: authentication fails closed (FR-ACC-1).

The test walks over every route the application has registered, so it covers
an endpoint added later without being changed.
"""

import re

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient

# The only routes that answer without a session.
PUBLIC_PATHS = {"/healthz", "/api/v1/login", "/api/v1/setup"}


def registered_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path) the application serves, with router prefixes applied."""
    found: list[tuple[str, str]] = []
    for route in iter_route_contexts(app.routes):
        # Anything that is not a plain API route (a mount, a schema or
        # documentation route) would escape the check, so none may exist.
        assert isinstance(route.original_route, APIRoute), f"unexpected kind of route: {route!r}"
        assert route.path and route.methods
        found.extend((method, route.path) for method in sorted(route.methods))
    return found


async def test_every_route_except_the_public_ones_needs_a_session(
    app: FastAPI, anonymous_client: AsyncClient
) -> None:
    protected = [(m, path) for m, path in registered_routes(app) if path not in PUBLIC_PATHS]
    assert protected, "no protected route was found"

    for method, path in protected:
        response = await anonymous_client.request(method, re.sub(r"\{[^}]+\}", "1", path))

        assert response.status_code == 401, f"{method} {path} answered without a session"
        assert response.json()["error"]["code"] == "not_authenticated"


async def test_the_public_routes_are_exactly_the_expected_ones(
    app: FastAPI, anonymous_client: AsyncClient
) -> None:
    assert PUBLIC_PATHS <= {path for _, path in registered_routes(app)}

    assert (await anonymous_client.get("/healthz")).status_code == 200
    assert (await anonymous_client.get("/api/v1/setup")).status_code == 200
    # Login and setup answer with their own errors, not with 401 "not authenticated".
    assert (await anonymous_client.post("/api/v1/login", json={})).status_code == 422
    assert (await anonymous_client.post("/api/v1/setup", json={})).status_code == 422


async def test_a_made_up_cookie_is_not_a_session(anonymous_client: AsyncClient) -> None:
    anonymous_client.cookies.set("ndm_session", "not-a-real-token")

    response = await anonymous_client.get("/api/v1/session")

    assert response.status_code == 401


async def test_no_documentation_or_schema_route_is_served(anonymous_client: AsyncClient) -> None:
    for path in ["/docs", "/redoc", "/openapi.json", "/api/v1/openapi.json"]:
        assert (await anonymous_client.get(path)).status_code == 404


async def test_no_cross_origin_allowance(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.options(
        "/api/v1/login",
        headers={"Origin": "https://other.example", "Access-Control-Request-Method": "POST"},
    )

    assert not any(name.lower().startswith("access-control-") for name in response.headers)
