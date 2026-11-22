# Registry Data Sources

This document records where the bulk data of the five Regional Internet Registries is published, in what format, under what terms, and what Network Discovery Map does with each source. It is the result of TASK-033 and resolves open issue R3 of the specification. It decides which loaders TASK-038 builds.

| | |
| --- | --- |
| Verified on | 2026-11-16 |
| How | Every location below was requested on that date; the formats were read from the first kilobytes of the real files; the terms were read on the registries' own pages. |
| Not verified | The bulk files of ARIN and LACNIC themselves. They are given out only under a signed agreement, and the team holds none. |

The team is not a legal adviser. Section 3 quotes the terms; whether a given installation's use is inside them is for the operator to judge.

## 1. Summary and decisions

Each registry publishes two different things.

- **Delegation statistics.** One line per block the registry has handed out: prefix, country, date and status. No names. All five publish them freely.
- **Detailed objects.** The registration records behind whois: network name and organization per range. Only these give the "organization" of FR-ENR-3. Their terms are stricter, and differ per registry.

| Registry | Delegation statistics | Detailed objects |
| --- | --- | --- |
| RIPE NCC | Load automatically | Load automatically, **only if the operator has switched it on** for this registry |
| APNIC | Load automatically | Load automatically, **only if the operator has switched it on** for this registry |
| AFRINIC | Load automatically | Load automatically, **only if the operator has switched it on** for this registry |
| ARIN | Load automatically | Load from an operator-supplied file |
| LACNIC | Load automatically | Load from an operator-supplied file |

Where no detailed objects are loaded for a registry, the organization of an address in its space falls back to the AS holder, and the snapshot records that source (FR-ENR-3). Registry and registration country always come from the delegation statistics.

### One change against the architecture

Architecture section 11.1 says that detailed objects are loaded for RIPE, APNIC and AFRINIC. This document adds a condition: **they are loaded only for the registries the operator lists in a setting, and the default is none.**

The reason is what the terms say (section 3). All three files can be downloaded without a login, but:

- APNIC states that bulk access needs a signed acceptable-use agreement.
- AFRINIC states that bulk access must be requested, and prohibits use "as part of a commercial service or product".
- RIPE NCC allows use only for a fixed list of purposes.

Whether an installation meets these depends on who runs it and why. The product cannot know that, so it must not download the files on its own. This is the same rule the product already follows for map tiles: no default source, the operator chooses (R6).

This needs the product owner's confirmation. The alternative, loading the three by default, is a one-line change of the default.

### What the later tasks build from this

| Task | Consequence |
| --- | --- |
| TASK-034 | Two settings: the list of registries whose detailed objects are downloaded (default empty), and the path for operator-supplied files. |
| TASK-038 | A delegation-statistics loader for all five. One loader for the RPSL dumps of RIPE, APNIC and AFRINIC. A loader for ARIN's `arin_db.xml`. A loader for a LACNIC file in whois object format. |
| TASK-047 | The operator guide repeats section 3 in short form and names the two settings. |

## 2. Delegation statistics

All five registries publish the same file, in the "extended" form of the RIR statistics exchange format, once a day.

| Registry | Location | Size |
| --- | --- | --- |
| RIPE NCC | `https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-extended-latest` | 18.1 MB |
| APNIC | `https://ftp.apnic.net/stats/apnic/delegated-apnic-extended-latest` | 9.3 MB |
| ARIN | `https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest` | 12.9 MB |
| LACNIC | `https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest` | 4.6 MB |
| AFRINIC | `https://ftp.afrinic.net/pub/stats/afrinic/delegated-afrinic-extended-latest` | 1.0 MB |

Each has a checksum file at the same location with `.md5` appended.

**Format.** Plain text, fields separated by `|`. The description is `RIR-Statistics-Exchange-Format.txt`, published in each directory.

```text
2.3|apnic|20261117|191431||20261116|+1000          version line
apnic|*|ipv4|*|61844|summary                       summary lines, one per type
apnic|AU|ipv4|1.0.0.0|256|20110811|assigned|A91872ED
registry|cc|type|start|value|date|status|opaque-id
```

What a loader must handle, as seen in the real files:

- Lines that start with `#` are comments. The version line and the three summary lines are not records.
- Only `ipv4` records are loaded. `asn` and `ipv6` records are skipped (C-5).
- For `ipv4`, `value` is a **number of addresses**, not a prefix length, and it is not always a power of two. The range is `start` to `start + value − 1`.
- The version field is `2` at RIPE NCC and AFRINIC and `2.3` elsewhere. The record lines are the same.
- `status` is `allocated`, `assigned`, `available` or `reserved`. Only `allocated` and `assigned` describe a registration. In `available` and `reserved` records the country and date can be empty.
- The country can be a code that is not a country, for example `ZZ`.
- A record can lack its last field, the opaque identifier. The loader does not use that field.

**Checksum.** Two forms occur:

```text
MD5 (delegated-ripencc-extended-latest) = abdef6f7f3edf8aabd64102db3e3ac05     RIPE NCC, APNIC, LACNIC, AFRINIC
d9b2922752244d4161f403bf61de515c  delegated-arin-extended-20261116             ARIN
```

The checksum is the MD5 of the whole file; this was confirmed for all five. The loader verifies it and keeps the previous data if it does not match.

**Terms.** No agreement, no login.

| Registry | Terms, as published with the files |
| --- | --- |
| APNIC | "The files are freely available for download and use on the condition that APNIC will not be held responsible for any loss or damage arising from the use of the information contained in these reports." (`README.TXT`) |
| LACNIC | The same sentence, for LACNIC (`disclaimer.txt`). |
| AFRINIC | The same sentence, for AFRINIC (`README-EXTENDED.txt`). |
| ARIN | "Extended statistics files … are available for use via HTTPS." No further condition is stated (statistics page and `README`). |
| RIPE NCC | No condition is stated with the files (`_README-ripe-stats.txt`). |

All three registries that state a condition add that the files show where a resource was first handed out, not where it is used today. The product shows the country from these files as the **registration country**, never as a location.

**Decision.** Load automatically for all five. The default interval is one week (architecture 11.1); the files change daily, and a shorter interval is the operator's choice.

## 3. Detailed objects

### 3.1 RIPE NCC

| | |
| --- | --- |
| Location | `https://ftp.ripe.net/ripe/dbase/split/ripe.db.inetnum.gz` (224 MB) and `https://ftp.ripe.net/ripe/dbase/split/ripe.db.organisation.gz` (5.4 MB) |
| Updated | Daily |
| Access | No login |
| Format | RPSL objects, gzip. Latin-1; a UTF-8 copy of each file exists, for example `ripe.db.inetnum.utf8.gz`. |
| Checksum | None is published. The gzip stream carries its own check. |

```text
inetnum:        213.159.160.0 - 213.159.191.255
netname:        SE-ERICSSON-20010504
country:        DK
org:            ORG-EA44-RIPE
status:         ALLOCATED PA
source:         RIPE

organisation:   ORG-AGNS1-RIPE
org-name:       AT&T Global Network Services Nederland B.V.
```

- Objects are separated by an empty line. Each line is `attribute:` and a value; a line that starts with a space or `+` continues the line before it.
- The range is written `first - last`. Network name is `netname`. The organization is the `org-name` of the `organisation` object that `org` refers to. Many `inetnum` objects have no `org`; then the first `descr` line is the best name available.
- Personal data has been removed from the file by RIPE NCC: contacts are replaced by `DUMY-RIPE`, and `person` objects are not in the dump. The loader reads only `inetnum`, `netname`, `country`, `org`, `descr` and `org-name`.

**Terms.** The file states that its contents are subject to the RIPE Database Terms and Conditions (`https://www.ripe.net/manage-ips-and-asns/db/support/documentation/terms/`).

- Article 4.1: "A User may only Access the RIPE Database and the data contained therein for any of the purposes as mentioned in Article 3 hereof".
- Article 3.1 lists the purposes. Those closest to this product: "Facilitating coordination between network operators (network problem resolution, outage notification etc.)", "Scientific research into network operations and topology", and information about registrants "when the resources are suspected of being used for unlawful activities, to parties who are authorised under the law to receive such information".
- Article 4.4: no use "for advertising, direct marketing, marketing research or similar purposes".
- Article 4.5: "A User may not re-package, download, compile, re-distribute or re-use any or all of the RIPE Database or the data contained therein unless they do so only with an insubstantial part of the RIPE Database … or when permission to do so is granted by the RIPE NCC."
- Article 9.2: the data "may not be used, reproduced or made available to third parties without prior written authorisation from the RIPE NCC", except as the terms provide.

**Decision.** Load automatically, only if the operator has listed `ripe`. The operator thereby states that the installation's use is one of the purposes of Article 3. The data stays inside the installation; the product never passes it on in bulk.

### 3.2 APNIC

| | |
| --- | --- |
| Location | `https://ftp.apnic.net/apnic/whois/apnic.db.inetnum.gz` (54 MB) and `https://ftp.apnic.net/apnic/whois/apnic.db.organisation.gz` (0.6 MB) |
| Updated | Daily |
| Access | The files download without a login. APNIC's bulk-access page nevertheless requires a signed agreement; see the terms. |
| Format | RPSL objects, gzip, as for RIPE NCC. |
| Checksum | None is published. |

```text
inetnum:        202.6.91.0 - 202.6.91.255
netname:        NLA-AU
descr:          National Library of Australia
country:        AU
org:            ORG-NLOA1-AP
source:         APNIC

organisation:   ORG-NLOA1-AP
org-name:       …
```

Addresses, telephone numbers and e-mail addresses in the `organisation` objects are masked (`********`) in the published files. The attributes the loader reads are the same as for RIPE NCC.

**Terms.**

- Bulk-access page (`https://www.apnic.net/manage-ip/using-whois/bulk-access/`): "Bulk access to whois data, including domain objects, is available under an Acceptable Use Policy (AUP). This restricts the uses to which whois data may be applied. Requestors must sign the AUP agreement and lodge it with APNIC."
- Header of each file: "Except for agreed Internet operational purposes, no part of this publication may be reproduced, stored in a retrieval system, or transmitted, in any form or by any means … without prior permission of APNIC". "Any use of this material to target advertising or similar activities are explicitly forbidden".

**Decision.** Load automatically, only if the operator has listed `apnic`. An operator should list it only after lodging the signed agreement with APNIC.

### 3.3 AFRINIC

| | |
| --- | --- |
| Location | `https://ftp.afrinic.net/pub/dbase/afrinic.db.gz` (10.6 MB). One file with every object type. |
| Updated | Daily |
| Access | The file downloads without a login. AFRINIC's terms require a request for bulk access; see the terms. |
| Format | RPSL objects, gzip. All object types are in the one file (`as-block`, `inetnum`, `organisation` and others); the loader takes `inetnum` and `organisation` and skips the rest. |
| Checksum | None is published. |

E-mail addresses in the file are masked (`***@afrinic.net`). The attributes are those of RIPE NCC.

**Terms.** Terms of Use of the AFRINIC Whois Database (`https://afrinic.net/whois-terms`).

- Permitted purposes include: "Facilitating operational coordination between network operators", "Conducting scientific research into network operations", and "Identifying resources used or suspected of being used for unlawful or harassing purposes".
- Prohibited: use "As part of a commercial service or product, including the solicitation and servicing of any customer, even if additional data not derived from the AFRINIC Whois Database is incorporated therein", and use "For advertising, direct marketing, marketing research or similar purposes."
- Bulk: "Where a legitimate need for high-volume queries or bulk access to the AFRINIC Whois Database exists, such need has to be requested from AFRINIC by emailing hostmaster@afrinic.net with a request for bulk whois data."

**Decision.** Load automatically, only if the operator has listed `afrinic`. An operator should list it only after AFRINIC has agreed to the request. An operator who sells scanning as a service should not list it at all.

### 3.4 ARIN

| | |
| --- | --- |
| Location | `https://accountws.arin.net/public/rest/downloads/bulkwhois/nets+orgs.xml?apikey=…`, for an approved ARIN Online account only |
| Updated | Daily |
| Access | By signed agreement, after a review by ARIN |
| Format | `arin_db.xml` (XML) or `arin_db.txt` (flat text). The download is always named one of these two. |

How an operator gets the file (`https://www.arin.net/reference/research/bulkwhois/`): create an ARIN Online account, sign the "Bulk Whois ToU and Data Request Form", open an "Ask ARIN" ticket with the subject "Bulk Whois Request", describe the intended use, and answer ARIN's questions. After approval the file is downloaded with an API key.

ARIN states who gets it: "organizations that will use the data for Internet operational or technical research purposes". And who does not: "ARIN does not provide bulk copies of Whois data to operators who wish to incorporate this data into products, services, or internal systems with no clear benefit to the broader community." An operator of this product should expect that ARIN may refuse.

The general Whois Terms of Use also prohibit use "as part of a commercial service or product" and "for advertising, direct marketing, marketing research or similar purposes", and any use that would "republish, resell, or make publicly available" the data.

**The XML elements the loader reads**, from ARIN's description of the report:

| Element | Content |
| --- | --- |
| `net` | One network registration |
| `net` / `name` | Network name |
| `net` / `orgHandle` | Handle of the organization; matches `org` / `handle` |
| `net` / `netBlocks` / `netBlock` / `startAddress`, `endAddress` | The range. A `net` can have several blocks. |
| `net` / `version` | `4` or `6`. Only `4` is loaded. |
| `org` / `handle`, `name` | The organization and its name |
| `org` / `iso3166-1` | The organization's country |

**Decision.** Load from an operator-supplied file. The operator places `arin_db.xml` (the `nets+orgs` selection is enough) in the directory for registry files and replaces it when a newer one is wanted. The product never holds the operator's ARIN API key and never downloads the file itself.

**Not verified.** The team has no agreement with ARIN and has not seen a real file. The loader is written against ARIN's published description of the elements and must be checked against the first real file an operator supplies.

### 3.5 LACNIC

| | |
| --- | --- |
| Location | None published. LACNIC delivers the file to an approved applicant. |
| Access | By signed application, decided by LACNIC |
| Format | **Not published.** |

How an operator gets the file ("Request for Bulk Whois", `https://www.lacnic.net/innovaportal/file/799/1/bulkwhoisii-en.pdf`): complete the application form with the reason and the intended use, sign it, and send it to LACNIC. The form says to send the original by post; LACNIC's web page names e-mail to the hostmaster. Check with LACNIC which applies.

From the form:

- "LACNIC shall provide a bulk copy of the WHOIS information only to those organizations that will use the information for technical research and/or Internet operation purposes."
- "LACNIC's bulk WHOIS shall only be used for technical research and/or Internet operation purposes, such as designing and developing security software, projects for improving Internet performance and web traffic optimization. It may not be used for publicity, direct marketing, market research or other similar purposes."
- "Redistribution or retransmission of the information by any means is expressly prohibited."
- "The request for information and LACNIC's resolution either denying or authorizing the request may be published."

**Decision.** Load from an operator-supplied file, placed in the directory for registry files under the name `lacnic_db.txt`.

**Not verified.** LACNIC does not publish the format of the delivered file, and the team has not seen one. The loader is written for the object format LACNIC's whois service prints: `inetnum` as a prefix in which trailing zero octets can be left out (`200.3.12/22`), the organization in `owner`, its identifier in `ownerid`, and `country`. If the first real file differs, the loader has to be corrected; until then the organization of LACNIC space falls back to the AS holder, as it does without a file.

## 4. Rules for every loader

- A file is downloaded at most once per update interval, with the product's name as the user agent. No registry is queried per address (FR-ENR-2).
- A loader reads the attributes named in this document and nothing else. No contact, address, telephone or e-mail attribute is stored.
- The data is used inside the installation, to label hosts the analyst has scanned. The product offers no export of a registry's data set as such.
- A failed download, a failed checksum or a file that cannot be parsed keeps the previous data and records the failure.
- The terms can change. Read them again before each release that touches a loader, and update the date at the top of this document.
