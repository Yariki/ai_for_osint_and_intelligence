# Network Discovery Map — Task Graph

| | |
| --- | --- |
| Version | 1.0 |
| Date | 2026-11-15 |
| Status | Proposed; ready for assignment |
| Based on | Network Discovery Map — Delivery Plan v1.0; Architecture v1.0; Technical Specification v1.0 |
| Audience | Implementation team |

This document is the index of the task graph for all seven phases of the delivery plan. The tasks themselves are in one file per phase. Requirement identifiers (FR-…, NFR-…, C-…, R…, A…) refer to the specification, AD…, I… and Q… to the architecture, P… and "check n.m" to the delivery plan.

## 1. Files

| Phase | File | Tasks | Number of tasks |
| --- | --- | --- | --- |
| 0 — Scanner spike | [network-discovery-map-task-graph-phase-0.md](network-discovery-map-task-graph-phase-0.md) | TASK-011 to TASK-015 | 5 |
| 1A — Foundation | [network-discovery-map-task-graph-phase-1a.md](network-discovery-map-task-graph-phase-1a.md) | TASK-001 to TASK-010 | 10 |
| 1B — Scan pipeline | [network-discovery-map-task-graph-phase-1b.md](network-discovery-map-task-graph-phase-1b.md) | TASK-016 to TASK-032 | 17 |
| 2 — Enrichment and map | [network-discovery-map-task-graph-phase-2.md](network-discovery-map-task-graph-phase-2.md) | TASK-033 to TASK-047 | 15 |
| 3 — Traceroute, classification and annotations | [network-discovery-map-task-graph-phase-3.md](network-discovery-map-task-graph-phase-3.md) | TASK-048 to TASK-059 | 12 |
| 4 — Schedules, comparison, alerts and export | [network-discovery-map-task-graph-phase-4.md](network-discovery-map-task-graph-phase-4.md) | TASK-060 to TASK-071 | 12 |
| 5 — Hardening and release | [network-discovery-map-task-graph-phase-5.md](network-discovery-map-task-graph-phase-5.md) | TASK-072 to TASK-077 | 6 |

Phase 1A was cut first, so it holds the lowest identifiers. An identifier says nothing about the phase order; the dependencies do.

## 2. Conventions

**Graph rules.**

- Task identifiers run from TASK-001 to TASK-077 across all phases, so a task can depend on a task of an earlier phase.
- A task depends only on tasks with a lower identifier. The graph has no cycles.
- Two tasks where neither depends on the other, directly or through other tasks, may run at the same time. Such tasks never list the same file.
- A dependency on a phase's exit task stands for the whole phase, because the exit task depends on everything in it.

**Fields of a task.**

| Field | Meaning |
| --- | --- |
| `dependencies` | Tasks that must be complete before this one starts. |
| `risk_level` | `high`, `medium` or `low`, with the reason in brackets. |
| `expected_files` | Paths relative to the git repository root. "(modified)" marks a file an earlier task created. |
| Acceptance | What must hold for the task to be complete, with the plan's check numbers where a check is closed. |

**Risk level.** A task is `high` when it creates or changes one of the following:

- authentication or session handling;
- an authorization control, which here includes every scope check that decides whether an address may be probed;
- handling of secrets;
- a database migration;
- a write to or a deletion of user data, or a path on which user data leaves the system;
- raw-socket privilege or host firewall rules.

A task that only reads user data through authentication that already exists is `medium`. Pure functions, test tools and documents are `medium` or `low`.

**Paths.** The product lives under `Assesment/` in the repository; the CI workflows sit at the repository root. The directories follow architecture section 17. The file names inside them are proposed by this graph, since no code exists yet.

**Common completion criteria.** Section 2.2 of the delivery plan applies to every phase from 1A onward. Each phase file ends with a table that says which task covers which criterion.

## 3. Dependencies between phases

```text
001 ──► Phase 1A (002 … 010) ───┐
 │                              ├──► Phase 1B (017 … 032) ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
 ├────► Phase 0 (011 … 015) ────┘              ▲
 └────► 016 ───────────────────────────────────┘
```

Inside Phase 0 only TASK-012 needs TASK-001; TASK-011 and TASK-014 depend on nothing.

| Edge | Reason |
| --- | --- |
| TASK-012 → TASK-001 | The packet builder and reply matcher are kept for Phase 1B, so they are written inside the `ndm` package that the scaffold creates. |
| TASK-016 → TASK-001 | Curating the port lists has no other dependency and starts during Phase 0, as the plan asks. |
| TASK-017, TASK-018 → TASK-010, TASK-015 | Phase 1B starts when Phase 1A is complete and Gate 0 has passed. |
| TASK-026 → TASK-012 | The production scanner builds on the kept packet code. |
| TASK-033, TASK-034, TASK-035 → TASK-032 | Phase 2 starts when Phase 1B is complete. |
| TASK-048, TASK-049 → TASK-047 | Phase 3 starts when Phase 2 is complete. |
| TASK-060 → TASK-059 | Phase 4 starts when Phase 3 is complete. |
| TASK-072, TASK-073 → TASK-071 | Phase 5 starts when Phase 4 is complete. |

## 4. Files that many tasks change

Some files are changed in every phase. The graph keeps each of them out of concurrent tasks in the way shown here.

| File | Rule |
| --- | --- |
| `backend/pyproject.toml`, `backend/uv.lock`, `frontend/package.json`, `frontend/package-lock.json` | Changed only by the groundwork task of a phase (TASK-001, TASK-017, TASK-034, TASK-048, TASK-060), which pins everything the phase needs. |
| `core/settings.py`, `core/messages.py`, `core/broker.py` | Created in TASK-002 and TASK-017; after that, changed only by the groundwork task of a phase. |
| `db/models.py`, `db/migrations/versions/` | One migration per phase, in one task (TASK-004, TASK-018, TASK-035, TASK-048, TASK-060). The migration history stays a single line. |
| `orchestrator/purge.py` | The task that adds a table also registers it for the deletion purge. |
| `api/app.py` | Changed by one task at a time: API tasks that register a router are chained inside a phase. |
| `orchestrator/main.py`, `orchestrator/phases.py` | Orchestrator tasks that wire a new loop, consumer or phase are chained inside a phase. |
| `frontend/src/router/index.ts`, `frontend/src/api/schema.d.ts` | Frontend tasks that add a route or regenerate the API types are chained inside a phase. |
| `deploy/compose.yaml`, `deploy/.env.example` | One task per phase. |
| `deploy/lab/compose.lab.yaml` | One task per phase. |
| `docs/operator-guide.md` | Changed by one task per phase: the exit task in Phases 1A to 4, TASK-073 in Phase 5. |

## 5. Assumptions and deviations from the delivery plan

**Assumptions.**

- CI runs on GitHub Actions, because the remote is on GitHub.
- The interpretations and proposals of delivery plan section 4.1 hold. Each phase file names the ones that should be confirmed before the phase starts.
- The prerequisites of delivery plan section 4.2 are available when the phase that needs them starts: the Linux lab host, the MaxMind key, a tile provider, the authorized public range.

**Deviations.**

| Deviation | Reason |
| --- | --- |
| One Phase 0 task (TASK-012) depends on the scaffold (TASK-001), although the plan says Phase 0 depends on nothing. | Its code is kept, so it belongs in the package. The lab (TASK-011) and `scanner-netinit` (TASK-014) still depend on nothing, and no Phase 0 task depends on the rest of Phase 1A. |
| Check 1B.18 is closed in three places. | The 2-second limit is measured at the event stream in the lab (TASK-029), the reload after a dropped connection is a frontend test (TASK-031), and both are confirmed in a browser at the exit (TASK-032). |
| Defect fixes in Phase 5 are not tasks in this graph. | They cannot be listed before the defects are found. They are raised as new tasks, numbered from TASK-078, when checks 5.1 to 5.10 run. |
| A storage cap for hosts open on every port (Q1) is not a task. | It is needed only if the product owner decides for a cap after check 5.5. |

## 6. Not covered

- FR-TRC-8, side-by-side comparison of two traceroutes (P3).
- Everything the delivery plan lists in its section 9.
- A native-language scanner. If Gate 0 fails, the scanner tasks of Phase 1B (TASK-026, TASK-027 and the scanner image in TASK-028) are re-planned; the other tasks stand.
