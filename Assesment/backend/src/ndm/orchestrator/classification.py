"""The rule evaluator of device classification (FR-CLS-1 to FR-CLS-3).

``classify`` is a pure function. Its inputs are the four that FR-CLS-2 allows,
and nothing else: the open ports, the reverse-DNS names, the enrichment
attributes, and whether the address was seen as an intermediate traceroute
hop. The ruleset and the threshold are configuration.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

import ndm.core

RULESET_DIRECTORY = Path(ndm.core.__file__).parent / "ruleset"
CURRENT = RULESET_DIRECTORY / "v1.yaml"
UNKNOWN = "unknown"

ENRICHMENT_FIELDS = frozenset(
    {"asn", "as_holder", "organization", "network_name", "rir", "registration_country", "country"}
)
CONDITIONS = frozenset({"open_any", "open_all", "ptr_matches", "seen_as_hop", "enrichment"})

Value = str | int | None


class RulesetError(ValueError):
    """The ruleset file is not valid. The orchestrator does not start with it."""


@dataclass(frozen=True)
class Rule:
    id: str
    type: str
    confidence: float
    sub_role: str | None = None
    open_any: frozenset[int] = frozenset()
    open_all: frozenset[int] = frozenset()
    ptr_matches: re.Pattern[str] | None = None
    seen_as_hop: bool | None = None
    enrichment: Mapping[str, re.Pattern[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Ruleset:
    version: str
    taxonomy: Mapping[str, tuple[str, ...]]
    """Device type -> its sub-roles, in the order of the file."""
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Classification:
    device_type: str
    sub_role: str | None
    confidence: float
    """The combined confidence of the type found, or for ``unknown``, of the
    best type, which stayed below the threshold."""
    rules: list[dict[str, Any]]
    """Every rule that fired, with the evidence it matched (FR-CLS-3)."""
    ruleset_version: str


def _ports(value: Any, rule: str) -> frozenset[int]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(port, int) and 1 <= port <= 65535 for port in value)
    ):
        raise RulesetError(f"rule {rule}: a port list must hold port numbers")
    return frozenset(value)


def _pattern(value: Any, rule: str) -> re.Pattern[str]:
    if not isinstance(value, str):
        raise RulesetError(f"rule {rule}: a pattern must be text")
    try:
        return re.compile(value, re.IGNORECASE)
    except re.error as error:
        raise RulesetError(f"rule {rule}: the pattern does not compile: {error}") from error


def parse_ruleset(data: Any) -> Ruleset:
    """Check and read the content of a ruleset file."""
    if not isinstance(data, dict) or set(data) != {"version", "taxonomy", "rules"}:
        raise RulesetError("a ruleset has exactly: version, taxonomy, rules")
    version = data["version"]
    if not isinstance(version, str) or not version:
        raise RulesetError("the version must be text")
    taxonomy: dict[str, tuple[str, ...]] = {}
    for name, entry in data["taxonomy"].items():
        if name == UNKNOWN or not isinstance(entry, dict) or "label" not in entry:
            raise RulesetError(f"type {name}: needs a label, and 'unknown' is implicit")
        taxonomy[name] = tuple((entry.get("sub_roles") or {}).keys())

    rules: list[Rule] = []
    seen: set[str] = set()
    for item in data["rules"]:
        rule_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(rule_id, str) or rule_id in seen:
            raise RulesetError(f"every rule needs an id of its own: {rule_id!r}")
        seen.add(rule_id)
        if set(item) - {"id", "type", "sub_role", "confidence", "when"}:
            raise RulesetError(f"rule {rule_id}: unknown keys {sorted(set(item) - {'id'})}")
        kind = item.get("type")
        if kind not in taxonomy:
            raise RulesetError(f"rule {rule_id}: type {kind!r} is not in the taxonomy")
        sub_role = item.get("sub_role")
        if sub_role is not None and sub_role not in taxonomy[kind]:
            raise RulesetError(f"rule {rule_id}: {kind} has no sub-role {sub_role!r}")
        confidence = item.get("confidence")
        if not isinstance(confidence, int | float) or not 0 < confidence < 1:
            raise RulesetError(f"rule {rule_id}: the confidence must lie between 0 and 1")
        when = item.get("when")
        if not isinstance(when, dict) or not when or set(when) - CONDITIONS:
            raise RulesetError(
                f"rule {rule_id}: conditions may only be {', '.join(sorted(CONDITIONS))}"
            )
        enrichment = when.get("enrichment", {})
        if not isinstance(enrichment, dict) or set(enrichment) - ENRICHMENT_FIELDS:
            raise RulesetError(
                f"rule {rule_id}: enrichment fields may only be {sorted(ENRICHMENT_FIELDS)}"
            )
        if "seen_as_hop" in when and not isinstance(when["seen_as_hop"], bool):
            raise RulesetError(f"rule {rule_id}: seen_as_hop is true or false")
        rules.append(
            Rule(
                id=rule_id,
                type=kind,
                sub_role=sub_role,
                confidence=float(confidence),
                open_any=_ports(when["open_any"], rule_id) if "open_any" in when else frozenset(),
                open_all=_ports(when["open_all"], rule_id) if "open_all" in when else frozenset(),
                ptr_matches=_pattern(when["ptr_matches"], rule_id)
                if "ptr_matches" in when
                else None,
                seen_as_hop=when.get("seen_as_hop"),
                enrichment={name: _pattern(value, rule_id) for name, value in enrichment.items()},
            )
        )
    return Ruleset(version=version, taxonomy=taxonomy, rules=tuple(rules))


def load_ruleset(path: Path = CURRENT) -> Ruleset:
    return parse_ruleset(yaml.safe_load(path.read_text()))


def _evidence(
    rule: Rule,
    open_ports: frozenset[int],
    ptr_names: Sequence[str],
    enrichment: Mapping[str, Value],
    seen_as_hop: bool,
) -> dict[str, Any] | None:
    """What the rule matched, or None if it does not fire. Every condition must hold."""
    evidence: dict[str, Any] = {}
    if rule.open_any:
        matched = sorted(rule.open_any & open_ports)
        if not matched:
            return None
        evidence["open_ports"] = matched
    if rule.open_all:
        if not rule.open_all <= open_ports:
            return None
        evidence["open_ports"] = sorted(set(evidence.get("open_ports", [])) | rule.open_all)
    if rule.ptr_matches is not None:
        names = sorted(name for name in ptr_names if rule.ptr_matches.search(name))
        if not names:
            return None
        evidence["ptr_names"] = names
    if rule.seen_as_hop is not None:
        if seen_as_hop is not rule.seen_as_hop:
            return None
        evidence["seen_as_hop"] = seen_as_hop
    for name, pattern in rule.enrichment.items():
        value = enrichment.get(name)
        if value is None or not pattern.search(str(value)):
            return None
        evidence.setdefault("enrichment", {})[name] = value
    return evidence


def _combined(confidences: Sequence[float]) -> float:
    return 1 - math.prod(1 - confidence for confidence in confidences)


def classify(
    ruleset: Ruleset,
    *,
    open_ports: frozenset[int],
    ptr_names: Sequence[str],
    enrichment: Mapping[str, Value],
    seen_as_hop: bool,
    threshold: float,
) -> Classification:
    """The device type of one host, from the four allowed inputs (FR-CLS-2)."""
    fired: list[tuple[Rule, dict[str, Any]]] = []
    for rule in ruleset.rules:
        evidence = _evidence(rule, open_ports, ptr_names, enrichment, seen_as_hop)
        if evidence is not None:
            fired.append((rule, evidence))

    order = list(ruleset.taxonomy)
    scores = {
        kind: _combined([rule.confidence for rule, _ in fired if rule.type == kind])
        for kind in {rule.type for rule, _ in fired}
    }
    # The highest score wins; a tie goes to the type listed first in the taxonomy.
    best = min(scores, key=lambda kind: (-round(scores[kind], 12), order.index(kind)), default=None)
    confidence = round(scores[best], 6) if best is not None else 0.0
    device_type = best if best is not None and confidence >= threshold else UNKNOWN

    sub_role = None
    if device_type != UNKNOWN:
        roles = {
            role: _combined(
                [r.confidence for r, _ in fired if r.type == device_type and r.sub_role == role]
            )
            for role in {r.sub_role for r, _ in fired if r.type == device_type and r.sub_role}
        }
        sub_order = list(ruleset.taxonomy[device_type])
        sub_role = min(
            roles, key=lambda role: (-round(roles[role], 12), sub_order.index(role)), default=None
        )

    return Classification(
        device_type=device_type,
        sub_role=sub_role,
        confidence=confidence,
        rules=[
            {
                "rule": rule.id,
                "type": rule.type,
                "sub_role": rule.sub_role,
                "confidence": rule.confidence,
                "evidence": evidence,
            }
            for rule, evidence in fired
        ],
        ruleset_version=ruleset.version,
    )
