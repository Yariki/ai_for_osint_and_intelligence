"""Check 3.2: the ruleset, run over the fixed fixtures, gives the stored output.

A difference means the ruleset's behaviour changed. That needs a new ruleset
version (FR-CLS-4): results stored with earlier scans name the version that
produced them, and must stay explainable by it.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from ndm.orchestrator.classification import UNKNOWN, classify, load_ruleset

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "classification"
HOSTS: list[dict[str, Any]] = json.loads((FIXTURES / "hosts.json").read_text())["hosts"]
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text())
RULESET = load_ruleset()


def run(host: dict[str, Any]) -> dict[str, Any]:
    result = classify(
        RULESET,
        open_ports=frozenset(host["open_ports"]),
        ptr_names=host["ptr_names"],
        enrichment=host["enrichment"],
        seen_as_hop=host["seen_as_hop"],
        threshold=EXPECTED["threshold"],
    )
    return {
        "device_type": result.device_type,
        "sub_role": result.sub_role,
        "confidence": result.confidence,
        "rules": result.rules,
    }


def test_the_expected_output_belongs_to_this_ruleset_version() -> None:
    assert EXPECTED["ruleset_version"] == RULESET.version, (
        "expected.json was made by another ruleset version"
    )
    assert [host["name"] for host in HOSTS] == list(EXPECTED["results"])


@pytest.mark.parametrize("host", HOSTS, ids=lambda host: host["name"])
def test_ruleset_gives_the_stored_output(host: dict[str, Any]) -> None:
    assert run(host) == EXPECTED["results"][host["name"]], (
        f"ruleset {RULESET.version} classifies {host['name']} differently: "
        "a change of behaviour needs a new ruleset version"
    )


def test_every_type_and_sub_role_is_produced_by_a_fixture() -> None:
    results = EXPECTED["results"].values()
    produced = {(r["device_type"], r["sub_role"]) for r in results}

    for kind, sub_roles in RULESET.taxonomy.items():
        assert any(t == kind for t, _ in produced), kind
        for role in sub_roles:
            assert (kind, role) in produced, (kind, role)
    assert (UNKNOWN, None) in produced


def test_every_rule_fires_for_one_fixture_and_not_for_another() -> None:
    fired = {
        name: {r["rule"] for r in result["rules"]} for name, result in EXPECTED["results"].items()
    }

    for rule in RULESET.rules:
        assert any(rule.id in rules for rules in fired.values()), f"{rule.id} never fires"
        assert any(rule.id not in rules for rules in fired.values()), f"{rule.id} always fires"
