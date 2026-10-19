#!/usr/bin/env python3
"""
Passive domain reconnaissance — a simple OSINT skill helper.

Collects only PUBLIC, PASSIVE information about a domain:
  - WHOIS registration data
  - DNS records (A / AAAA / MX / NS / TXT / CNAME)
  - Subdomains from Certificate Transparency logs (crt.sh)
  - HTTP response headers from a single request (tech fingerprint)

It does NOT scan ports, brute-force, exploit, or otherwise touch the target
aggressively. Run it only against domains you own or are explicitly
authorized to assess.
"""
from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import urllib.error
import urllib.request

UA = "domain-recon-skill/1.0 (passive OSINT; authorized use only)"
TIMEOUT = 10


# --- WHOIS -----------------------------------------------------------------
def get_whois(domain: str) -> dict:
    try:
        import whois  # python-whois
    except ImportError:
        return {"error": "python-whois not installed (pip install python-whois)"}
    try:
        w = whois.whois(domain)

        def fmt(v):
            if isinstance(v, (list, tuple)):
                return [str(x) for x in v]
            return str(v) if v is not None else None

        return {
            "registrar": fmt(w.registrar),
            "org": fmt(w.get("org")),
            "created": fmt(w.creation_date),
            "expires": fmt(w.expiration_date),
            "updated": fmt(w.updated_date),
            "name_servers": fmt(w.name_servers),
            "status": fmt(w.status),
        }
    except Exception as e:  # noqa: BLE001 - WHOIS libs raise many ad-hoc errors
        return {"error": f"{type(e).__name__}: {e}"}


# --- DNS -------------------------------------------------------------------
def get_dns(domain: str) -> dict:
    records: dict = {}
    try:
        import dns.resolver  # dnspython

        for rtype in ("A", "AAAA", "MX", "NS", "TXT", "CNAME"):
            try:
                answers = dns.resolver.resolve(domain, rtype, lifetime=TIMEOUT)
                records[rtype] = [r.to_text().strip('"') for r in answers]
            except Exception:  # noqa: BLE001 - NoAnswer / NXDOMAIN etc.
                records[rtype] = []
        return records
    except ImportError:
        # Stdlib fallback: A records only.
        try:
            _, _, ips = socket.gethostbyname_ex(domain)
            records["A"] = ips
        except OSError as e:
            records["A"] = []
            records["error"] = str(e)
        records["note"] = "install dnspython for MX/NS/TXT/CNAME records"
        return records


# --- Certificate Transparency (crt.sh) -------------------------------------
def get_crtsh_subdomains(domain: str) -> dict:
    url = f"https://crt.sh/?q=%25.{domain}&output=json"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, json.JSONDecodeError, socket.timeout) as e:
        return {"error": f"{type(e).__name__}: {e}", "subdomains": []}

    names: set[str] = set()
    for entry in data:
        for name in str(entry.get("name_value", "")).splitlines():
            name = name.strip().lower().lstrip("*.")
            if name.endswith(domain):
                names.add(name)
    return {"count": len(names), "subdomains": sorted(names)}


# --- HTTP headers ----------------------------------------------------------
def get_http_headers(domain: str) -> dict:
    ctx = ssl.create_default_context()
    url = f"https://{domain}"
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            headers = {k: v for k, v in resp.headers.items()}
            interesting = {
                k: headers.get(k)
                for k in (
                    "Server", "X-Powered-By", "Via", "X-Cache",
                    "CF-RAY", "X-Amz-Cf-Id", "Strict-Transport-Security",
                    "Content-Security-Policy", "X-Frame-Options",
                )
                if headers.get(k)
            }
            return {
                "final_url": resp.geturl(),
                "status": resp.status,
                "highlights": interesting,
                "all_headers": headers,
            }
    except urllib.error.HTTPError as e:
        return {"status": e.code, "highlights": dict(e.headers or {})}
    except (urllib.error.URLError, socket.timeout, ssl.SSLError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


# --- Orchestration ---------------------------------------------------------
def recon(domain: str) -> dict:
    domain = domain.strip().lower().removeprefix("http://").removeprefix(
        "https://"
    ).split("/")[0]
    return {
        "domain": domain,
        "whois": get_whois(domain),
        "dns": get_dns(domain),
        "certificate_transparency": get_crtsh_subdomains(domain),
        "http": get_http_headers(domain),
    }


def print_summary(r: dict) -> None:
    def line(label, value):
        print(f"  {label:<14} {value}")

    print(f"\n=== Passive recon: {r['domain']} ===")

    print("\n[WHOIS]")
    w = r["whois"]
    if "error" in w:
        line("(skipped)", w["error"])
    else:
        line("Registrar", w.get("registrar"))
        line("Org", w.get("org"))
        line("Created", w.get("created"))
        line("Expires", w.get("expires"))
        line("Name servers", w.get("name_servers"))

    print("\n[DNS]")
    for rtype, vals in r["dns"].items():
        if rtype in ("note", "error"):
            line(rtype, vals)
        elif vals:
            line(rtype, ", ".join(vals))

    print("\n[Certificate Transparency]")
    ct = r["certificate_transparency"]
    if ct.get("error"):
        line("(error)", ct["error"])
    else:
        line("Subdomains", ct["count"])
        for s in ct["subdomains"][:40]:
            print(f"      - {s}")
        if ct["count"] > 40:
            print(f"      ... and {ct['count'] - 40} more")

    print("\n[HTTP]")
    http = r["http"]
    if http.get("error"):
        line("(error)", http["error"])
    else:
        line("Status", http.get("status"))
        line("Final URL", http.get("final_url"))
        for k, v in (http.get("highlights") or {}).items():
            line(k, v)
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Passive domain reconnaissance (authorized use only).")
    p.add_argument("domain", help="Target domain, e.g. example.com")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a summary")
    p.add_argument("--out", metavar="FILE", help="Write JSON results to FILE")
    args = p.parse_args()

    result = recon(args.domain)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Wrote {args.out}")
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif not args.out:
        print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
