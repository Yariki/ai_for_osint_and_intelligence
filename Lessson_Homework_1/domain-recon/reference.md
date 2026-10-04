# Interpreting results & authorized next steps

Load this only when you actually need it — keep it out of context otherwise.

## Reading the output

**WHOIS**
- A recent `created` date on a domain impersonating a known brand is a classic
  phishing signal.
- `org` / registrant is often privacy-protected (Whois Guard, Redacted for
  Privacy) — absence of data is normal, not suspicious.
- Compare `name_servers` here against the NS records in DNS; a mismatch can mean
  a migration in progress or a stale registration.

**DNS**
- **MX** reveals the mail provider (Google Workspace, Outlook/EXO, Proofpoint…).
- **TXT** is the richest passive source: SPF (`v=spf1 include:...`) lists every
  service authorized to send mail for the domain — each `include:` is a SaaS
  vendor in use. DKIM selectors and `*-site-verification` records expose more.
- **NS** reveals the DNS host (Cloudflare, Route 53, Azure DNS).

**Certificate Transparency (crt.sh)**
- Highest-signal passive source for surface mapping. Every public TLS cert ever
  issued shows up, so you see subdomains that aren't linked anywhere.
- Watch for internal-looking names leaked publicly: `dev.`, `staging.`, `vpn.`,
  `jira.`, `jenkins.`, `admin.`. These are the interesting attack surface.
- Wildcard certs (`*.example.com`) hide the specific hostnames — note the gap.

**HTTP headers**
- `Server` / `X-Powered-By` fingerprint the stack (nginx, IIS, Kestrel for
  .NET, etc.). Modern setups often strip these.
- `CF-RAY` → Cloudflare; `X-Amz-Cf-Id` / `Via` → CloudFront; these indicate a
  CDN/WAF sits in front.
- **Missing** security headers (`Strict-Transport-Security`,
  `Content-Security-Policy`, `X-Frame-Options`) are a finding in their own right
  for a defensive report.

## Where an authorized assessment goes next

Everything above is passive. The following are **active** and require explicit
authorization (signed engagement or in-scope bug bounty) — they are NOT part of
this skill, listed only so you know the boundary:

- Resolving each CT subdomain and probing which are live (e.g. `httpx`).
- Port/service scanning (`nmap`) against hosts in scope.
- Directory/content discovery, vulnerability scanning.

If you don't have authorization for active testing, stop at the passive report
and hand it to whoever does.

## Good passive add-ons (still safe)
- **Shodan / Censys** — look up your own IPs to see what's internet-exposed.
- **Have I Been Pwned** — check your own org's domain for breached accounts.
- Both have free APIs and fit the same `scripts/` + `SKILL.md` pattern if you
  want to extend this skill.
