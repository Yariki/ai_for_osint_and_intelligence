"""The diff of two scans (architecture 11.3; FR-HIS-2 to FR-HIS-5).

One function computes it from two scan identifiers. Automatic diffs, manual
comparison, the change-status filter and the exports all use it. It reads
only; storing a diff is the diff phase's business.

- **Addresses compared:** those targeted by both scans. The others are listed
  as not comparable (FR-HIS-2).
- **Ports compared:** those probed in both scans, stated in the result
  (FR-HIS-4). An address that was not port-scanned in one of the scans (it
  did not answer discovery) gets no port change: the change is the host
  appearing or disappearing.
- **Other changes:** reverse-DNS names, classification, and each enrichment
  field. A field that was unavailable in either snapshot is unknown there and
  is not reported as changed. An enrichment change carries the dataset-change
  flag when the version of the dataset behind the field differs between the
  two snapshots (FR-HIS-5).

``changes`` is a pure function of two ``ScanView``s; ``compute_diff`` loads
them from the database.
"""

from collections import Counter
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import (
    ClassificationResult,
    EnrichmentSnapshot,
    HostObservation,
    PortObservation,
    Scan,
)

KINDS = (
    "host_appeared",
    "host_disappeared",
    "port_opened",
    "port_closed",
    "ptr_changed",
    "classification_changed",
    "enrichment_changed",
)
ENRICHMENT_FIELDS = (
    "asn",
    "as_holder",
    "organization",
    "network_name",
    "rir",
    "registration_country",
    "country",
    "city",
)


@dataclass(frozen=True)
class HostView:
    reachable: bool = False
    port_scanned: bool = False
    """Its ports were probed: it answered discovery, or the scan probes every target."""
    open_ports: frozenset[int] = frozenset()
    ptr_names: tuple[str, ...] = ()
    device_type: str | None = None
    sub_role: str | None = None
    enrichment: dict[str, Any] = field(default_factory=dict)
    unavailable: frozenset[str] = frozenset()
    dataset_versions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanView:
    scan_id: int
    targets: frozenset[IPv4Address]
    probed_ports: frozenset[int]
    hosts: dict[IPv4Address, HostView]
    """Every target of the scan; one that is missing counts as not answering."""


@dataclass(frozen=True)
class Change:
    address: IPv4Address
    kind: str
    port: int | None = None
    field: str | None = None
    before: Any = None
    after: Any = None
    dataset_change: bool = False


@dataclass(frozen=True)
class Diff:
    base_scan_id: int
    target_scan_id: int
    compared_ports: list[tuple[int, int]]
    """The ports probed in both scans, as inclusive ranges."""
    not_comparable: list[IPv4Address]
    compared_addresses: int
    changes: list[Change]

    @property
    def summary(self) -> dict[str, int]:
        counts = Counter(change.kind for change in self.changes)
        return {kind: counts[kind] for kind in KINDS} | {
            "compared_ports": sum(high - low + 1 for low, high in self.compared_ports),
            "compared_addresses": self.compared_addresses,
            "not_comparable": len(self.not_comparable),
        }


def ranges(ports: frozenset[int]) -> list[tuple[int, int]]:
    """A set of ports as sorted inclusive ranges."""
    spans: list[tuple[int, int]] = []
    for port in sorted(ports):
        if spans and spans[-1][1] == port - 1:
            spans[-1] = (spans[-1][0], port)
        else:
            spans.append((port, port))
    return spans


def dataset_of(name: str, host: HostView) -> str | None:
    """The dataset whose version stands behind an enrichment field (architecture 11.1)."""
    rir = host.enrichment.get("rir")
    if name in ("country", "city"):
        return "geolite2-city"
    if name in ("asn", "as_holder"):
        return "geolite2-asn"
    if name in ("rir", "registration_country"):
        return f"rir-delegation-{rir}" if rir else None
    if name == "organization" and host.enrichment.get("organization_source") == "as_holder":
        return "geolite2-asn"
    return f"rir-objects-{rir}" if rir else None


def _dataset_changed(name: str, before: HostView, after: HostView) -> bool:
    datasets = {dataset_of(name, before), dataset_of(name, after)} - {None}
    return any(
        before.dataset_versions.get(dataset) != after.dataset_versions.get(dataset)
        for dataset in datasets
        if dataset is not None
    )


def _host_changes(
    address: IPv4Address, before: HostView, after: HostView, compared: frozenset[int]
) -> list[Change]:
    if not before.reachable and after.reachable:
        return [Change(address, "host_appeared")]
    if before.reachable and not after.reachable:
        return [Change(address, "host_disappeared")]
    if not before.reachable:
        return []

    found: list[Change] = []
    if before.port_scanned and after.port_scanned:
        found += [
            Change(address, "port_opened", port=port)
            for port in sorted((after.open_ports - before.open_ports) & compared)
        ]
        found += [
            Change(address, "port_closed", port=port)
            for port in sorted((before.open_ports - after.open_ports) & compared)
        ]
    if sorted(before.ptr_names) != sorted(after.ptr_names):
        found.append(
            Change(
                address,
                "ptr_changed",
                before=sorted(before.ptr_names),
                after=sorted(after.ptr_names),
            )
        )
    classified = (before.device_type, before.sub_role), (after.device_type, after.sub_role)
    if None not in (before.device_type, after.device_type) and classified[0] != classified[1]:
        found.append(
            Change(
                address,
                "classification_changed",
                before={"device_type": before.device_type, "sub_role": before.sub_role},
                after={"device_type": after.device_type, "sub_role": after.sub_role},
            )
        )
    for name in ENRICHMENT_FIELDS:
        if name in before.unavailable or name in after.unavailable:
            continue  # unknown on one side: no change can be stated
        if not before.enrichment or not after.enrichment:
            continue  # a host without a snapshot on one side
        if before.enrichment.get(name) != after.enrichment.get(name):
            found.append(
                Change(
                    address,
                    "enrichment_changed",
                    field=name,
                    before=before.enrichment.get(name),
                    after=after.enrichment.get(name),
                    dataset_change=_dataset_changed(name, before, after),
                )
            )
    return found


def changes(base: ScanView, target: ScanView) -> Diff:
    """The diff of two scans: from ``base`` (the earlier) to ``target``."""
    compared = base.probed_ports & target.probed_ports
    common = base.targets & target.targets
    found: list[Change] = []
    for address in sorted(common):
        found += _host_changes(
            address,
            base.hosts.get(address, HostView()),
            target.hosts.get(address, HostView()),
            compared,
        )
    return Diff(
        base_scan_id=base.scan_id,
        target_scan_id=target.scan_id,
        compared_ports=ranges(compared),
        not_comparable=sorted(base.targets ^ target.targets),
        compared_addresses=len(common),
        changes=found,
    )


# --- From the database -----------------------------------------------------------------


async def load_view(db: AsyncSession, scan: Scan) -> ScanView:
    """What a scan stored, as the diff needs it."""
    parameters = scan.parameters or {}
    every_target_port_scanned = parameters.get("discovery") is None or bool(
        parameters.get("scan_all_targets")
    )
    probed = frozenset(
        port for span in scan.probed_ports for port in range(span.lower or 0, span.upper or 0)
    )
    # One row per host with all its ports: a host open on every port has 1,000
    # or more, and one row per port would be most of the time of a diff.
    ports: dict[IPv4Address, frozenset[int]] = {
        address: frozenset(open_ports)
        for address, open_ports in await db.execute(
            select(PortObservation.address, func.array_agg(PortObservation.port))
            .where(PortObservation.scan_id == scan.id)
            .group_by(PortObservation.address)
        )
    }
    classified = {
        row.address: row
        for row in await db.scalars(
            select(ClassificationResult).where(
                ClassificationResult.scan_id == scan.id, ClassificationResult.latest
            )
        )
    }
    snapshots = {
        row.address: row
        for row in await db.scalars(
            select(EnrichmentSnapshot).where(
                EnrichmentSnapshot.scan_id == scan.id, EnrichmentSnapshot.latest
            )
        )
    }
    hosts: dict[IPv4Address, HostView] = {}
    for observation in await db.scalars(
        select(HostObservation).where(HostObservation.scan_id == scan.id)
    ):
        address = observation.address
        result = classified.get(address)
        snapshot = snapshots.get(address)
        hosts[address] = HostView(
            reachable=observation.reachable,
            port_scanned=observation.reachable or every_target_port_scanned,
            open_ports=ports.get(address, frozenset()),
            ptr_names=tuple(observation.ptr_names),
            device_type=result.device_type if result else None,
            sub_role=result.sub_role if result else None,
            enrichment=(
                {name: getattr(snapshot, name) for name in ENRICHMENT_FIELDS}
                | {"organization_source": snapshot.organization_source}
                if snapshot
                else {}
            ),
            unavailable=frozenset(snapshot.unavailable) if snapshot else frozenset(),
            dataset_versions=dict(snapshot.dataset_versions) if snapshot else {},
        )
    return ScanView(
        scan_id=scan.id,
        targets=frozenset(scan.targets),
        probed_ports=probed,
        hosts=hosts,
    )


class NotComparable(ValueError):
    """The two scans cannot be compared."""


async def compute_diff(db: AsyncSession, base_scan_id: int, target_scan_id: int) -> Diff:
    """The diff of two scans of the same project, from what is stored."""
    base = await db.get(Scan, base_scan_id)
    target = await db.get(Scan, target_scan_id)
    if base is None or target is None:
        raise NotComparable("a scan does not exist")
    if base.project_id != target.project_id:
        raise NotComparable("only scans of the same project can be compared")
    return changes(await load_view(db, base), await load_view(db, target))
