# Provenance of the bundled data files

Every data file in this directory is listed here with its source and its licence (NFR-LIC-2). A file that is not listed must not be added.

| File | Source | Licence |
| --- | --- | --- |
| `iana-service-names.csv` | IANA Service Name and Transport Protocol Port Number Registry | CC0 1.0 (public domain dedication) |
| `top-100-tcp.txt` | Selected by this project | This project's own work; uses facts from the IANA registry (CC0 1.0) |
| `top-1000-tcp.txt` | Selected by this project | This project's own work; uses facts from the IANA registry (CC0 1.0) |

## `iana-service-names.csv`

- **Source.** <https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.csv>, downloaded on 2026-11-15. SHA-256 of the downloaded file: `ee7416ab54a15a77de38cc2c03cc5ed6da8f59597bdbfff795360de3ffd8a719`.
- **Licence.** IANA and the IETF place the protocol registries under the Creative Commons CC0 1.0 dedication (<https://www.iana.org/help/licensing-terms>). CC0 permits inclusion and redistribution without conditions.
- **What was kept.** Rows with transport protocol `tcp`, a single port number and a service name. Where the registry has several names for one port, the first one is kept. The columns are reduced to `port,name`.
- **Use.** The name is shown as the nominal assignment of a port number. It is never presented as a detected service (FR-SCN-3).

## `top-100-tcp.txt` and `top-1000-tcp.txt`

These lists are this project's own selection. **They are not derived from nmap's `nmap-services` file or from any other scan-frequency dataset** (R4), and they make no claim to rank ports by measured frequency.

How they were made:

1. **Top-100.** One hundred ports chosen by hand: the default ports of the services most often exposed on Internet-facing hosts, taken by kind of service (remote access, web, mail, name service, file sharing, directory, databases and queues, network-device management, VPN, proxies, voice and video, printing, application platforms, industrial protocols). The port numbers are facts from the IANA registry and from the public documentation of the software concerned.
2. **Top-1000, hand-picked part.** The top-100 ports plus 594 further default and commonly used alternative ports of widely deployed software, chosen the same way: 694 ports.
3. **Top-1000, fill.** The remaining 306 places are filled by a fixed rule: the lowest-numbered ports that have a TCP registration in the IANA registry and are not already in the list, in ascending order. This takes registered system ports up to port 460.

The top-100 list is a subset of the top-1000 list. Both files hold one port per line in ascending order; the scan order is randomized at run time (FR-SCN-10), so the order in the file carries no meaning.

To change a list, edit the file, keep the counts at exactly 100 and 1,000 distinct ports, keep the subset rule, and record the change here. The unit tests check the counts and the subset rule.
