"""NFR-LIC-2: the licence inventory covers every locked package and every
bundled data file (docs/licences.md)."""

import json
import re
import tomllib
from pathlib import Path

ASSESMENT = Path(__file__).resolve().parents[3]
INVENTORY = (ASSESMENT / "docs" / "licences.md").read_text()
ROWS = set(re.findall(r"^\| `([^`]+)` \| ([^ |]+) \| ([^|]+) \|", INVENTORY, re.MULTILINE))
LISTED = {(name, version) for name, version, _ in ROWS}


def test_every_python_package_of_the_lockfile_is_listed_with_a_licence() -> None:
    lock = tomllib.loads((ASSESMENT / "backend" / "uv.lock").read_text())
    packages = {
        (package["name"], package["version"])
        for package in lock["package"]
        if not package.get("source", {}).get("editable")
        and not package.get("source", {}).get("virtual")
    }

    assert packages - LISTED == set()


def test_every_npm_package_of_the_lockfile_is_listed_with_a_licence() -> None:
    lock = json.loads((ASSESMENT / "frontend" / "package-lock.json").read_text())
    packages = {
        (path.split("node_modules/")[-1], package["version"])
        for path, package in lock["packages"].items()
        if path
    }

    assert packages - LISTED == set()


def test_no_listed_package_lacks_a_licence() -> None:
    assert [name for name, _, licence in ROWS if not licence.strip()] == []


def test_every_bundled_data_file_is_listed() -> None:
    data = ASSESMENT / "backend" / "src" / "ndm" / "core"
    files = [*(data / "data").glob("*.csv"), *(data / "data").glob("*.txt")]
    files += [*(data / "ruleset").glob("*.yaml")]

    for file in files:
        assert f"`{file.relative_to(ASSESMENT)}`" in INVENTORY, file.name
