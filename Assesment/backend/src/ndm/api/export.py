"""Export as CSV and JSON (FR-EXP-1 to FR-EXP-4).

The host, diff and traceroute endpoints take ``format=csv`` or ``format=json``.
An export is a set of flat rows plus the metadata of FR-EXP-4: scan
identifier, scan time, vantage point and dataset versions.

- **JSON:** ``{"metadata": {...}, "rows": [...]}``.
- **CSV:** one header line and one line per row. The metadata is repeated in
  columns of its own on every row, so that each line says where it comes from
  and every line parses as CSV.

Both are streamed in pieces. The rows are read before the first piece is sent,
so the database session is never held by a slow download.

CSV cells come partly from outside (reverse-DNS names, registry records) and
partly from the analyst (tags, notes). A text cell that begins with a formula
character is prefixed with an apostrophe, so a spreadsheet shows it as text
and does not run it.
"""

import csv
import io
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api.queries.hosts import (
    HostFilters,
    Source,
    fields_of,
    row_columns,
    select_hosts,
    sort_key,
)
from ndm.db.models import (
    ProjectHost,
    Scan,
    VantagePoint,
)

ExportFormat = Literal["csv", "json"]

# A spreadsheet treats a cell that begins with one of these as a formula.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
# Rows per piece of the streamed body.
ROWS_PER_PIECE = 500

SCAN_COLUMNS = [
    "scan_id",
    "scan_time",
    "vantage_point",
    "dataset_versions",
    "icmp_discovery_unavailable",
]


@dataclass
class Export:
    """What an endpoint exports: a file name, the metadata, and flat rows."""

    name: str
    metadata: dict[str, Any]
    columns: list[str]
    rows: list[dict[str, Any]]


def escape(text: str) -> str:
    """A text that a spreadsheet will show and not evaluate."""
    return "'" + text if text.startswith(FORMULA_START) else text


def cell(value: Any) -> str:
    """One CSV cell. Lists are joined with ';', mappings written as JSON."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return escape(";".join("" if item is None else str(item) for item in value))
    if isinstance(value, dict):
        return escape(json.dumps(value, sort_keys=True, default=str))
    return escape(str(value))


def _csv(export: Export) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(export.columns)
    for number, row in enumerate(export.rows, start=1):
        writer.writerow([cell(row.get(column)) for column in export.columns])
        if number % ROWS_PER_PIECE == 0:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue()


def _json(export: Export) -> Iterator[str]:
    def dumps(value: Any) -> str:
        return json.dumps(value, default=str, ensure_ascii=False)

    yield '{"metadata": ' + dumps(export.metadata) + ', "rows": ['
    for start in range(0, len(export.rows), ROWS_PER_PIECE):
        piece = ", ".join(dumps(row) for row in export.rows[start : start + ROWS_PER_PIECE])
        yield (", " if start else "") + piece
    yield "]}"


def response(export: Export, form: ExportFormat) -> StreamingResponse:
    media = "text/csv; charset=utf-8" if form == "csv" else "application/json"
    return StreamingResponse(
        _csv(export) if form == "csv" else _json(export),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{export.name}.{form}"'},
    )


# --- Metadata ---------------------------------------------------------------------------


async def scan_metadata(db: AsyncSession, scan_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """Scan identifier, scan time, vantage point and dataset versions of each scan
    (FR-EXP-4), and whether ICMP discovery was unavailable (TASK-079)."""
    rows = await db.execute(
        select(Scan, VantagePoint.name)
        .outerjoin(VantagePoint, VantagePoint.id == Scan.vantage_point_id)
        .where(Scan.id.in_(scan_ids))
    )
    return {
        scan.id: {
            "scan_id": scan.id,
            "scan_time": (scan.started_at or scan.created_at).isoformat(),
            "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
            "vantage_point": vantage,
            "dataset_versions": scan.dataset_versions or {},
            "icmp_discovery_unavailable": scan.icmp_discovery_unavailable,
        }
        for scan, vantage in rows
    }


def _scan_columns(meta: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    return {prefix + name: meta[name] for name in SCAN_COLUMNS}


# --- Hosts ------------------------------------------------------------------------------

HOST_COLUMNS = [
    *SCAN_COLUMNS,
    "address",
    "reachable",
    "open_ports",
    "closed_count",
    "filtered_count",
    "ptr_names",
    "asn",
    "as_holder",
    "organization",
    "network_name",
    "rir",
    "registration_country",
    "country",
    "city",
    "latitude",
    "longitude",
    "accuracy_radius_km",
    "device_type",
    "sub_role",
    "confidence",
    "type_override",
    "effective_type",
    "tags",
    "notes",
]


async def hosts(
    db: AsyncSession,
    source: Source,
    filters: HostFilters,
    *,
    sort: str,
    descending: bool,
    name: str,
) -> Export:
    """Every host the table shows for these filters, in its order, not paged (FR-EXP-1)."""
    fields = fields_of(source)
    key = sort_key(fields, sort)
    query = select_hosts(
        *row_columns(fields),
        fields.open_ports.label("open_ports"),
        fields.network_name.label("network_name"),
        fields.rir.label("rir"),
        fields.registration_country.label("registration_country"),
        ProjectHost.notes,
        source=source,
        filters=filters,
    ).order_by((key.desc() if descending else key.asc()).nulls_last(), fields.address)
    found = list(await db.execute(query))
    scans = await scan_metadata(db, sorted({row.scan_id for row in found} | _source_scan(source)))
    rows = []
    for row in found:
        point = row.location
        rows.append(
            _scan_columns(scans[row.scan_id])
            | {
                "address": str(row.address),
                "reachable": row.reachable,
                "open_ports": row.open_ports or [],
                "closed_count": row.closed_count,
                "filtered_count": row.filtered_count,
                "ptr_names": row.ptr_names or [],
                "asn": row.asn,
                "as_holder": row.as_holder,
                "organization": row.organization,
                "network_name": row.network_name,
                "rir": row.rir,
                "registration_country": row.registration_country,
                "country": row.country,
                "city": row.city,
                "latitude": point.latitude if point else None,
                "longitude": point.longitude if point else None,
                "accuracy_radius_km": row.accuracy_radius_km,
                "device_type": row.device_type,
                "sub_role": row.sub_role,
                "confidence": row.confidence,
                "type_override": row.type_override,
                "effective_type": row.effective_type,
                "tags": row.tags or [],
                "notes": row.notes,
            }
        )
    metadata = {
        "project_id": source.project_id,
        "scan_id": source.scan_id,
        "scans": [scans[scan_id] for scan_id in sorted(scans)],
        "filters": {k: v for k, v in vars(filters).items() if v is not None and k != "addresses"},
        "sort": sort,
        "order": "desc" if descending else "asc",
        "rows": len(rows),
    }
    return Export(name=name, metadata=metadata, columns=HOST_COLUMNS, rows=rows)


def _source_scan(source: Source) -> set[int]:
    """The scan of a one-scan export: its metadata is there even with no row."""
    return {source.scan_id} if source.scan_id is not None else set()


# --- Diffs ------------------------------------------------------------------------------

CHANGE_COLUMNS = ["address", "kind", "port", "field", "before", "after", "dataset_change"]
DIFF_COLUMNS = [
    "diff_id",
    *("base_" + name for name in SCAN_COLUMNS),
    *("target_" + name for name in SCAN_COLUMNS),
    *CHANGE_COLUMNS,
]


async def diff(db: AsyncSession, out: Any) -> Export:
    """The change events of a diff, with both scans' metadata (FR-EXP-2). ``out`` is a DiffOut."""
    scans = await scan_metadata(db, [out.base_scan_id, out.target_scan_id])
    base, target = scans[out.base_scan_id], scans[out.target_scan_id]
    fixed = {"diff_id": out.id} | _scan_columns(base, "base_") | _scan_columns(target, "target_")
    rows = [fixed | change.model_dump(include=set(CHANGE_COLUMNS)) for change in out.changes]
    metadata = {
        "diff_id": out.id,
        "stored": out.stored,
        "base": base,
        "target": target,
        "compared_ports": out.compared_ports,
        "not_comparable": out.not_comparable,
        "summary": out.summary,
        "created_at": out.created_at.isoformat() if out.created_at else None,
        "rows": len(rows),
    }
    name = f"diff-{out.base_scan_id}-{out.target_scan_id}"
    return Export(name=name, metadata=metadata, columns=DIFF_COLUMNS, rows=rows)


# --- Traceroutes ------------------------------------------------------------------------

HOP_COLUMNS = [
    "traceroute_id",
    "traceroute_time",
    "vantage_point",
    "dataset_versions",
    "target",
    "method",
    "ttl",
    "address",
    "rtt_ms",
    "lost",
    "silent",
    "unlocatable",
    "ptr_names",
    "asn",
    "as_holder",
    "organization",
    "country",
    "city",
    "latitude",
    "longitude",
    "accuracy_radius_km",
]


def traceroute(detail: Any, dataset_versions: dict[str, Any]) -> Export:
    """One row per address that answered a hop, and one per silent hop (FR-EXP-3).
    ``detail`` is a TracerouteDetailOut."""
    fixed = {
        "traceroute_id": detail.id,
        "traceroute_time": (detail.dispatched_at or detail.created_at).isoformat(),
        "vantage_point": detail.vantage_point,
        "dataset_versions": dataset_versions,
        "target": detail.target,
        "method": detail.method,
    }
    rows: list[dict[str, Any]] = []
    for hop in detail.hops:
        hop_values = {"ttl": hop.ttl, "lost": hop.lost, "silent": hop.silent}
        if not hop.responders:
            rows.append(fixed | hop_values)
        for responder in hop.responders:
            rows.append(fixed | hop_values | responder.model_dump())
    metadata = {
        "traceroute_id": detail.id,
        "target": detail.target,
        "method": detail.method,
        "parameters": detail.parameters.model_dump(),
        "state": detail.state,
        "reached": detail.reached,
        "vantage_point": detail.vantage_point,
        "created_at": detail.created_at.isoformat(),
        "dispatched_at": detail.dispatched_at.isoformat() if detail.dispatched_at else None,
        "finished_at": detail.finished_at.isoformat() if detail.finished_at else None,
        "dataset_versions": dataset_versions,
        "rows": len(rows),
    }
    return Export(name=f"traceroute-{detail.id}", metadata=metadata, columns=HOP_COLUMNS, rows=rows)
