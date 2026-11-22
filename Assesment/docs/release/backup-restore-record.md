# Backup and Restore Record — Release 1.0.0-rc.1

| | |
| --- | --- |
| Release | 1.0.0-rc.1 |
| Check | 5.7 (NFR-OPS-5) |
| Procedure | `docs/operator-guide.md`, section 16 |
| Audience | Product owner; the person who carries out the restore |
| State | **Not carried out.** Needs the deployment of the acceptance run and a second, clean host. |

## 1. What is done

1. On the deployment of the acceptance run, after AC 1 to AC 11 (so that the database holds projects, scans of every kind, schedules, diffs, alerts and audit entries), take a backup by section 16 of the guide. Record the counts below on the source, at the time of the backup.
2. Copy the backup file and the `.env` to a second host that has never run the product.
3. Restore by section 16, exactly as written. Whatever had to be done that the guide does not say is a defect of the guide.
4. Record the same counts on the restored host, log in, and compare.

## 2. The comparison

The query of section 16, step 4, extended to the tables below; plus a comparison of the audit log in full: `SELECT id, occurred_at, actor, action, object_type, object_id, details FROM audit_event ORDER BY id`, exported on both hosts and compared with `diff`.

| | Source | Restored | Same |
| --- | --- | --- | --- |
| Projects | | | |
| Scans | | | |
| Host observations | | | |
| Open ports | | | |
| Schedules | | | |
| Stored diffs | | | |
| Alerts | | | |
| Audit entries, and the highest id | | | |
| Audit log in full (`diff` of the two exports) | | | |
| Login with the source's account works | | | |
| A project's workspace, a scan's hosts and the audit log page look the same | | | |

## 3. The run

| | |
| --- | --- |
| Source host and time of the backup (UTC) | |
| Size of the backup file | |
| Restore host | |
| Duration of the restore | |
| Deviations from the guide | |
| Result | Not yet recorded |
