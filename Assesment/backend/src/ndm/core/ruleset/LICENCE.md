# Classification ruleset: provenance and licence

| | |
| --- | --- |
| File | `v1.yaml` |
| Version | 1 |
| Written | 2026-11-17, for Network Discovery Map |
| Licence | The licence of Network Discovery Map itself |

The ruleset was written for this project. It copies no third-party rule set, signature database or fingerprint list. Its content is:

- **Port numbers** and what they are registered or commonly used for. These are facts; the names come from the IANA service-name registry, which the project already ships under CC0 (`ndm/core/data/PROVENANCE.md`).
- **Name patterns** (regular expressions over reverse-DNS names), written for this project from common naming conventions such as `ns1`, `mail`, `rtr`.
- **Confidences**, chosen for this project.

It contains no data from nmap, from nmap's service or OS fingerprint files, or from any commercial product (NFR-LIC-2).
