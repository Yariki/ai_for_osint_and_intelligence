"""Smoke test for the scaffold: the package is installed and its pinned
runtime dependencies can be imported."""

import importlib
from importlib import metadata

import pytest

import ndm

RUNTIME_MODULES = [
    "fastapi",
    "uvicorn",
    "sqlalchemy.ext.asyncio",
    "asyncpg",
    "alembic",
    "pydantic_settings",
    "argon2",
    "structlog",
]


def test_package_version_matches_the_installed_distribution() -> None:
    assert ndm.__version__ == metadata.version("ndm")


def test_core_package_is_importable() -> None:
    assert importlib.import_module("ndm.core").__name__ == "ndm.core"


@pytest.mark.parametrize("module", RUNTIME_MODULES)
def test_runtime_dependency_is_importable(module: str) -> None:
    assert importlib.import_module(module) is not None


async def test_async_tests_run() -> None:
    """Later tasks write async tests without a marker; this proves the mode is on."""
    assert True
