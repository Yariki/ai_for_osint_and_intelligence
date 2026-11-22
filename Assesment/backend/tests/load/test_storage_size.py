"""Check 5.4: table and index sizes at the envelope (A7), compared with the
estimate of architecture section 10.3. A deviation above 50 percent is
reported; it does not fail the check, it goes into the capacity report.

The sizes are measured on the seeded database and scaled to the row counts of
A7 by the measured size per row. At the full scale no scaling is needed: the
seeded counts are those of A7.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.load.conftest import report

GB = 1024**3
# Architecture 10.3: rows at A7, and the estimate in GB as (low, high), tables
# and their indexes together.
ESTIMATES: dict[str, tuple[int, float, float]] = {
    "port_observation": (250_000_000, 13 + 8, 13 + 10),
    "host_observation": (50_000_000, 10, 15),
    "enrichment_snapshot": (50_000_000, 5, 10),
    "classification_result": (50_000_000, 5, 10),
}
DEVIATION = 0.5


async def sizes(owner: AsyncEngine) -> dict[str, dict[str, int]]:
    async with owner.connect() as connection:
        # The observation tables are hash-partitioned (AD8): a table's size is
        # the sum over its partitions; a plain table has no partition tree.
        rows = await connection.execute(
            text(
                "SELECT c.relname, c.reltuples::bigint, "
                "coalesce((SELECT sum(pg_table_size(t.relid)) "
                "FROM pg_partition_tree(c.oid) AS t), pg_table_size(c.oid)), "
                "coalesce((SELECT sum(pg_indexes_size(t.relid)) "
                "FROM pg_partition_tree(c.oid) AS t), pg_indexes_size(c.oid)) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
            )
        )
        measured = {
            name: {"rows": rows_, "table_bytes": int(table), "index_bytes": int(index)}
            for name, rows_, table, index in rows
        }
        for name in ESTIMATES:
            measured[name]["rows"] = (
                await connection.scalar(text(f"SELECT count(*) FROM {name}")) or 0
            )
    return measured


async def test_sizes_at_the_envelope_are_measured_and_compared(owner: AsyncEngine) -> None:
    measured = await sizes(owner)

    comparison: dict[str, Any] = {}
    deviations = []
    for name, (a7_rows, low, high) in ESTIMATES.items():
        table = measured[name]
        assert table["rows"] > 0, f"{name} is empty: the database is not seeded"
        per_row = (table["table_bytes"] + table["index_bytes"]) / table["rows"]
        at_a7 = per_row * a7_rows / GB
        # Above 50 % over the upper estimate, or under half of the lower one.
        off = at_a7 > high * (1 + DEVIATION) or at_a7 < low * (1 - DEVIATION)
        comparison[name] = {
            "seeded_rows": table["rows"],
            "bytes_per_row": round(per_row, 1),
            "table_bytes_per_row": round(table["table_bytes"] / table["rows"], 1),
            "index_bytes_per_row": round(table["index_bytes"] / table["rows"], 1),
            "gb_at_a7": round(at_a7, 1),
            "estimate_gb": [low, high],
            "deviation_above_50_percent": off,
        }
        if off:
            deviations.append(name)
    total = sum(t["table_bytes"] + t["index_bytes"] for t in measured.values())

    report(
        "storage",
        {
            "tables": comparison,
            "deviations": deviations,
            "seeded_database_bytes": total,
            "all_tables": measured,
        },
    )
    # Every table that holds data at rest is filled, so that its size is measured.
    empty = [name for name, table in measured.items() if table["table_bytes"] == 0]
    assert empty == [] or set(empty) <= {"scan_chunk", "session", "alembic_version"}, empty
