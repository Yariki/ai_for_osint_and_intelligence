"""The import rule of C-3, which CI enforces through ruff (pyproject.toml).

The scanner, and the core package it imports, must not use the database or
the web layer. The scanner image does not even contain them.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

FORBIDDEN = [
    "import ndm.db",
    "from ndm.db import models",
    "from ndm.db.models import Project",
    "import ndm.api.app",
    "from ndm.orchestrator import planning",
    "import sqlalchemy",
    "from sqlalchemy.ext.asyncio import AsyncSession",
    "import fastapi",
    "import asyncpg",
]


def lint(source: str, path: str) -> subprocess.CompletedProcess[str]:
    """Lint ``source`` as if it were the file ``path`` of the backend."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "TID251",
            "--stdin-filename",
            path,
            "-",
        ],
        input=source + "\n",
        capture_output=True,
        text=True,
        cwd=BACKEND,
        check=False,
    )


@pytest.mark.parametrize("statement", FORBIDDEN)
@pytest.mark.parametrize("path", ["src/ndm/scanner/worker.py", "src/ndm/core/anything.py"])
def test_rule_fails_on_a_forbidden_import(statement: str, path: str) -> None:
    result = lint(statement, path)

    assert result.returncode != 0
    assert "TID251" in result.stdout


@pytest.mark.parametrize(
    "path",
    [
        "src/ndm/api/routers/scans.py",
        "src/ndm/db/audit.py",
        "src/ndm/orchestrator/recording.py",
        "tests/integration/api/test_scans.py",
    ],
)
def test_rule_allows_the_same_imports_where_they_belong(path: str) -> None:
    result = lint("\n".join(FORBIDDEN), path)

    assert result.returncode == 0, result.stdout


def test_scanner_may_import_the_core() -> None:
    allowed = "import ndm.core.scope\nfrom ndm.core.messages import Chunk\nimport aio_pika"

    assert lint(allowed, "src/ndm/scanner/worker.py").returncode == 0


def test_the_packages_as_they_are_pass_the_rule() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "TID251",
            "src/ndm/scanner",
            "src/ndm/core",
        ],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        check=False,
    )

    assert result.returncode == 0, result.stdout
