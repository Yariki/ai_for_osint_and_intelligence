"""Checks 3.1 and 3.3: the rule evaluator."""

import inspect
import math
from typing import Any

import pytest

from ndm.orchestrator.classification import (
    CURRENT,
    UNKNOWN,
    Ruleset,
    RulesetError,
    classify,
    load_ruleset,
    parse_ruleset,
)

TAXONOMY = {
    "router": {"label": "Router"},
    "dns_server": {"label": "DNS server"},
    "server": {"label": "Server", "sub_roles": {"web": "Web", "mail": "Mail"}},
}


def ruleset(*rules: dict[str, Any], version: str = "test") -> Ruleset:
    return parse_ruleset({"version": version, "taxonomy": TAXONOMY, "rules": list(rules)})


def rule(rule_id: str, kind: str, confidence: float, **when: Any) -> dict[str, Any]:
    sub_role = when.pop("sub_role", None)
    item = {"id": rule_id, "type": kind, "confidence": confidence, "when": when}
    if sub_role:
        item["sub_role"] = sub_role
    return item


def run(rules: Ruleset, threshold: float = 0.5, **inputs: Any) -> Any:
    values: dict[str, Any] = {
        "open_ports": frozenset(),
        "ptr_names": [],
        "enrichment": {},
        "seen_as_hop": False,
    }
    values.update(inputs)
    values["open_ports"] = frozenset(values["open_ports"])
    return classify(rules, threshold=threshold, **values)


# --- Check 3.3: the evaluator takes the four allowed inputs and nothing else ---


def test_the_evaluator_accepts_only_the_four_allowed_inputs() -> None:
    parameters = inspect.signature(classify).parameters

    # The ruleset and the threshold are configuration; the rest is all it may see.
    assert set(parameters) == {
        "ruleset",
        "open_ports",
        "ptr_names",
        "enrichment",
        "seen_as_hop",
        "threshold",
    }
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("open_ports", "ptr_names", "enrichment", "seen_as_hop")
    )


def test_a_rule_can_name_only_the_allowed_conditions_and_enrichment_fields() -> None:
    with pytest.raises(RulesetError, match="conditions may only be"):
        ruleset(rule("banner", "router", 0.5, banner_matches="Cisco"))
    with pytest.raises(RulesetError, match="enrichment fields"):
        ruleset(rule("city", "router", 0.5, enrichment={"city": "Paris"}))


# --- Check 3.1: each kind of condition fires, and does not ---


@pytest.mark.parametrize(
    ("when", "matching", "not_matching", "evidence"),
    [
        (
            {"open_any": [53, 853]},
            {"open_ports": [853, 80]},
            {"open_ports": [80]},
            {"open_ports": [853]},
        ),
        (
            {"open_all": [25, 587]},
            {"open_ports": [25, 587, 993]},
            {"open_ports": [25]},
            {"open_ports": [25, 587]},
        ),
        (
            {"ptr_matches": "(^|[.])ns[0-9]*[.]"},
            {"ptr_names": ["www.example.net", "NS2.example.net"]},
            {"ptr_names": ["dns-shop.example.net"]},
            {"ptr_names": ["NS2.example.net"]},
        ),
        (
            {"seen_as_hop": True},
            {"seen_as_hop": True},
            {"seen_as_hop": False},
            {"seen_as_hop": True},
        ),
        (
            {"enrichment": {"organization": "cloud"}},
            {"enrichment": {"organization": "Example Cloud Ltd", "country": "NL"}},
            {"enrichment": {"organization": "Example Telecom"}},
            {"enrichment": {"organization": "Example Cloud Ltd"}},
        ),
        (
            {"enrichment": {"asn": "^6449[6-9]$"}},
            {"enrichment": {"asn": 64497}},
            {"enrichment": {"asn": None}},
            {"enrichment": {"asn": 64497}},
        ),
    ],
)
def test_each_condition_matches_and_does_not(
    when: dict[str, Any], matching: dict[str, Any], not_matching: dict[str, Any], evidence: Any
) -> None:
    rules = ruleset(rule("r", "router", 0.7, **when))

    fired = run(rules, **matching)
    quiet = run(rules, **not_matching)

    assert fired.device_type == "router"
    assert fired.rules == [
        {"rule": "r", "type": "router", "sub_role": None, "confidence": 0.7, "evidence": evidence}
    ]
    assert (quiet.device_type, quiet.rules) == (UNKNOWN, [])


def test_all_conditions_of_a_rule_must_hold() -> None:
    rules = ruleset(rule("r", "router", 0.7, open_any=[179], seen_as_hop=True))

    assert run(rules, open_ports=[179], seen_as_hop=True).device_type == "router"
    assert run(rules, open_ports=[179]).rules == []
    assert run(rules, seen_as_hop=True).rules == []


# --- Check 3.1: combining, the threshold, the winner ---


@pytest.mark.parametrize("confidences", [[0.6], [0.6, 0.4], [0.3, 0.3, 0.3], [0.9, 0.5, 0.2, 0.1]])
def test_confidences_of_a_type_combine_as_one_minus_the_product(confidences: list[float]) -> None:
    rules = ruleset(
        *(rule(f"r{i}", "router", c, open_any=[1000 + i]) for i, c in enumerate(confidences))
    )

    result = run(rules, threshold=0.0, open_ports=[1000 + i for i in range(len(confidences))])

    assert result.confidence == pytest.approx(1 - math.prod(1 - c for c in confidences))
    assert len(result.rules) == len(confidences)


def test_below_the_threshold_the_type_is_unknown_and_the_evidence_is_kept() -> None:
    rules = ruleset(
        rule("a", "dns_server", 0.4, open_any=[53]), rule("b", "dns_server", 0.1, open_any=[853])
    )

    below = run(rules, threshold=0.5, open_ports=[53, 853])  # 1 - 0.6 * 0.9 = 0.46
    at = run(rules, threshold=0.46, open_ports=[53, 853])

    assert (below.device_type, below.confidence) == (UNKNOWN, pytest.approx(0.46))
    assert [r["rule"] for r in below.rules] == ["a", "b"]
    assert below.sub_role is None
    # At the threshold is not below it.
    assert at.device_type == "dns_server"


def test_the_highest_combined_score_wins_across_types() -> None:
    rules = ruleset(
        rule("router", "router", 0.6, seen_as_hop=True),
        rule("dns-a", "dns_server", 0.5, open_any=[53]),
        rule("dns-b", "dns_server", 0.3, ptr_matches="ns"),
    )

    result = run(rules, seen_as_hop=True, open_ports=[53], ptr_names=["ns1.example.net"])

    # dns_server: 1 - 0.5 * 0.7 = 0.65 beats router: 0.6. Every fired rule is reported.
    assert (result.device_type, result.confidence) == ("dns_server", 0.65)
    assert [r["rule"] for r in result.rules] == ["router", "dns-a", "dns-b"]


def test_a_tie_goes_to_the_type_listed_first() -> None:
    rules = ruleset(
        rule("dns", "dns_server", 0.6, open_any=[53]),
        rule("router", "router", 0.6, seen_as_hop=True),
    )

    assert run(rules, open_ports=[53], seen_as_hop=True).device_type == "router"


def test_the_sub_role_with_the_most_support_is_chosen() -> None:
    rules = ruleset(
        rule("web", "server", 0.5, open_any=[443], sub_role="web"),
        rule("mail-a", "server", 0.4, open_any=[25], sub_role="mail"),
        rule("mail-b", "server", 0.3, open_any=[993], sub_role="mail"),
        rule("any", "server", 0.2, open_any=[22]),
    )

    mail = run(rules, open_ports=[443, 25, 993, 22])
    web = run(rules, open_ports=[443, 22])
    plain = run(rules, threshold=0.1, open_ports=[22])

    assert (mail.device_type, mail.sub_role) == ("server", "mail")  # 0.58 against 0.5
    assert (web.device_type, web.sub_role) == ("server", "web")
    assert (plain.device_type, plain.sub_role) == ("server", None)


def test_the_result_names_the_ruleset_version() -> None:
    assert run(ruleset(version="7")).ruleset_version == "7"


# --- The ruleset file ---


def test_the_shipped_ruleset_loads_and_has_a_version_and_a_licence_record() -> None:
    shipped = load_ruleset()

    assert shipped.version == "1"
    assert set(shipped.taxonomy) == {"router", "gateway", "dns_server", "camera", "server"}
    assert shipped.taxonomy["server"] == ("web", "mail", "database", "remote_access", "file")
    assert (CURRENT.parent / "LICENCE.md").read_text().count("v1.yaml") == 1


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"version": "1", "taxonomy": TAXONOMY}, "exactly"),
        ({"version": "", "taxonomy": TAXONOMY, "rules": []}, "version"),
        ({"version": "1", "taxonomy": {"unknown": {"label": "x"}}, "rules": []}, "implicit"),
        (
            {
                "version": "1",
                "taxonomy": TAXONOMY,
                "rules": [rule("r", "printer", 0.5, open_any=[9100])],
            },
            "not in the taxonomy",
        ),
        (
            {
                "version": "1",
                "taxonomy": TAXONOMY,
                "rules": [rule("r", "router", 0.5, open_any=[1], sub_role="web")],
            },
            "no sub-role",
        ),
        (
            {
                "version": "1",
                "taxonomy": TAXONOMY,
                "rules": [rule("r", "router", 1.0, open_any=[1])],
            },
            "between 0 and 1",
        ),
        ({"version": "1", "taxonomy": TAXONOMY, "rules": [rule("r", "router", 0.5)]}, "conditions"),
        (
            {
                "version": "1",
                "taxonomy": TAXONOMY,
                "rules": [rule("r", "router", 0.5, open_any=[70000])],
            },
            "port",
        ),
        (
            {
                "version": "1",
                "taxonomy": TAXONOMY,
                "rules": [rule("r", "router", 0.5, ptr_matches="(")],
            },
            "compile",
        ),
        (
            {
                "version": "1",
                "taxonomy": TAXONOMY,
                "rules": [rule("r", "router", 0.5, seen_as_hop="yes")],
            },
            "true or false",
        ),
        (
            {
                "version": "1",
                "taxonomy": TAXONOMY,
                "rules": [rule("r", "router", 0.5, open_any=[1])] * 2,
            },
            "id of its own",
        ),
    ],
)
def test_a_ruleset_that_is_not_valid_is_refused(data: dict[str, Any], message: str) -> None:
    with pytest.raises(RulesetError, match=message):
        parse_ruleset(data)
