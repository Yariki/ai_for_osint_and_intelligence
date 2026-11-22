# Registry files

Put the bulk files you obtained from ARIN and LACNIC here. The orchestrator reads this directory, read-only; nothing else uses it.

| File | From |
| --- | --- |
| `arin_db.xml` | ARIN Bulk Whois, after ARIN has approved your request. The `nets` and `orgs` objects are enough. |
| `lacnic_db.txt` | LACNIC bulk whois, after LACNIC has approved your request. |

The files are under the registries' terms of use and must not be passed on. They are kept out of version control. To use another directory, set `NDM_RIR_FILES_DIR` in `.env`.

See the operator guide, section 11, and `docs/datasets/registry-sources.md`.
