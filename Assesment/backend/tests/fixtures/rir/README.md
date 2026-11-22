# Registry fixtures

One small file per registry and format, for the loaders of `ndm.orchestrator.datasets`.

The files are written by hand in the formats the registries publish (see `docs/datasets/registry-sources.md`). The networks, names and organizations in them are made up. They include the irregularities seen in the real files: comment lines, summary lines, records without a country, a count that is not a power of two, a record without its last field, continuation lines, a comment after a country code, and placeholder objects for the address space of other registries.

`arin_db.xml` and `lacnic_db.txt` follow the registries' descriptions of their bulk files. The team has not seen a real one.
