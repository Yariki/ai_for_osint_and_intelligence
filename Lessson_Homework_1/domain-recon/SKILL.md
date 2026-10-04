---
name: domain-recon
description: >
  Use for a passive OSINT footprint of a domain you own or are explicitly
  authorized to assess. Collects WHOIS registration data, DNS records,
  subdomains from Certificate Transparency logs, and HTTP response headers
  for a tech fingerprint. Triggers on "recon this domain", "domain footprint",
  "what's exposed for example.com", "passive OSINT on a domain".
---

# Passive domain reconnaissance

## Scope and authorization
This skill performs **passive** collection only — it reads public records and
makes a single, ordinary HTTP request to the homepage. It does **not** scan
ports, brute-force, enumerate aggressively, or exploit anything.

Run it only against domains you **own or are explicitly authorized to assess**
(your own assets, a client with a signed engagement, a bug-bounty program whose
scope includes the target). If you aren't sure you're authorized, stop.

## How to run
```bash
# One-time setup (optional but recommended for full DNS + WHOIS):
pip install dnspython python-whois

# Run against a domain:
python scripts/recon.py example.com

# Machine-readable output, saved to a file:
python scripts/recon.py example.com --json --out example-recon.json
```

The script degrades gracefully: with no extra packages installed it still
returns A records, Certificate Transparency subdomains, and HTTP headers using
only the Python standard library.

## What each section tells you
- **WHOIS** — registrar, creation/expiry dates, registrant org (when not
  privacy-protected), and authoritative name servers.
- **DNS** — A/AAAA (hosts), MX (mail provider), NS (DNS provider), TXT
  (SPF/DKIM/verification records that often reveal SaaS vendors in use).
- **Certificate Transparency (crt.sh)** — subdomains that have ever been issued
  a TLS certificate. This is the single highest-signal passive source for
  mapping an organization's external surface.
- **HTTP headers** — `Server`, `X-Powered-By`, CDN/WAF hints, and security
  headers that are present or missing.

## Interpreting and next steps
For how to read the results and where an **authorized** assessment goes from
here, load `reference.md` only when needed — keep it out of context otherwise.
