"""The operator guide is complete for configuration (architecture 15.5):
every NDM_ setting of the application and every variable of the example
environment file is described in it."""

import re
from pathlib import Path

from ndm.core.settings import Settings

ASSESMENT = Path(__file__).resolve().parents[3]
GUIDE = (ASSESMENT / "docs" / "operator-guide.md").read_text()


def test_every_setting_is_in_the_guide() -> None:
    names = [f"NDM_{field.upper()}" for field in Settings.model_fields]

    assert [name for name in names if f"`{name}`" not in GUIDE] == []


def test_every_variable_of_the_example_file_is_in_the_guide() -> None:
    example = (ASSESMENT / "deploy" / ".env.example").read_text()
    names = set(re.findall(r"^#? ?(NDM_[A-Z0-9_]+)=", example, re.MULTILINE))

    assert sorted(name for name in names if f"`{name}`" not in GUIDE) == []


def test_the_guide_has_the_sections_a_deployment_needs() -> None:
    for heading in (
        "Host preparation",
        "Backup and restore",
        "Reverse DNS",
        "The map",
        "GeoLite2",
        "Abuse reports",
    ):
        assert re.search(rf"^##+ (\d+\. )?{heading}", GUIDE, re.MULTILINE), heading
