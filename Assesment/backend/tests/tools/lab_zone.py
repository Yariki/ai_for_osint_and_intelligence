"""The lab resolver's zone file, read as a resolver reads it."""

from ipaddress import IPv4Address
from pathlib import Path

import dns.rdatatype
import dns.zone

LAB = Path(__file__).resolve().parents[3] / "deploy" / "lab"
ZONE = LAB / "resolver" / "lab.zone"


def load_zone() -> dns.zone.Zone:
    return dns.zone.from_file(str(ZONE), relativize=False)


def ptr_records() -> dict[IPv4Address, list[str]]:
    """Address -> its names in the zone, sorted, each with its final dot."""
    records: dict[IPv4Address, list[str]] = {}
    for name, rdataset in load_zone().iterate_rdatasets(dns.rdatatype.PTR):
        octets = name.to_text().removesuffix(".in-addr.arpa.").split(".")
        address = IPv4Address(".".join(reversed(octets)))
        records[address] = sorted(rdata.target.to_text() for rdata in rdataset)
    return records
