#!/usr/bin/env python3
"""Manage DNS for occident.institute and occident.now at Porkbun.

Two independent modes:

    porkbun_dns.py                 web: apex -> GitHub Pages, occident.now -> site
    porkbun_dns.py --mail google   mail: MX, SPF and DMARC for Google Workspace

Reads PORKBUN_API_KEY and PORKBUN_SECRET_KEY from the environment. Run with
--dry-run first to see what would change.

Do not run --mail google until the Google Workspace account exists and the
domain is verified in the Admin console. Publishing Google's MX record before
then points mail at an account that cannot accept it, so messages bounce.

Porkbun requires API access to be switched on per domain in the control panel
(Details -> API Access) before any of this will work.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.porkbun.com/api/json/v3"

SITE_DOMAIN = "occident.institute"
REDIRECT_DOMAIN = "occident.now"
PAGES_TARGET = "lydnot.github.io"

# https://docs.github.com/pages/configuring-a-custom-domain-for-your-github-pages-site
PAGES_A = ["185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153"]
PAGES_AAAA = [
    "2606:50c0:8000::153",
    "2606:50c0:8001::153",
    "2606:50c0:8002::153",
    "2606:50c0:8003::153",
]

# Anything else at these names is left alone, so MX and TXT records used for
# email forwarding and domain verification survive.
MANAGED_TYPES = {"A", "AAAA", "ALIAS", "CNAME"}

# Single-record setup Google has used for new accounts since 2023.
# https://support.google.com/a/answer/140038
GOOGLE_MX = [("1", "smtp.google.com")]
GOOGLE_SPF = "v=spf1 include:_spf.google.com ~all"

# p=none only reports, it never rejects mail. Tighten to quarantine or reject
# once the reports show every legitimate sender passing.
DMARC = "v=DMARC1; p=none; rua=mailto:lydia@occident.institute"


class PorkbunError(RuntimeError):
    pass


def call(path, payload=None):
    body = dict(payload or {})
    body["apikey"] = os.environ["PORKBUN_API_KEY"]
    body["secretapikey"] = os.environ["PORKBUN_SECRET_KEY"]

    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise PorkbunError(f"{path}: HTTP {exc.code} {exc.read().decode()[:400]}") from exc

    if data.get("status") != "SUCCESS":
        raise PorkbunError(f"{path}: {data.get('message', data)}")
    return data


def fqdn(domain, sub):
    return f"{sub}.{domain}" if sub else domain


def sync_records(domain, dry_run):
    """Replace the apex and www address records, leaving everything else."""
    existing = call(f"dns/retrieve/{domain}")["records"]

    desired = [("", "A", ip) for ip in PAGES_A]
    desired += [("", "AAAA", ip) for ip in PAGES_AAAA]
    desired += [("www", "CNAME", PAGES_TARGET)]

    managed_names = {fqdn(domain, sub) for sub, _, _ in desired}
    stale = [
        r
        for r in existing
        if r["name"] in managed_names and r["type"] in MANAGED_TYPES
    ]

    keep = set()
    for record in stale:
        match = next(
            (
                d
                for d in desired
                if fqdn(domain, d[0]) == record["name"]
                and d[1] == record["type"]
                and d[2].rstrip(".") == record["content"].rstrip(".")
            ),
            None,
        )
        if match:
            keep.add(match)
        else:
            print(f"  delete {record['type']:<5} {record['name']} -> {record['content']}")
            if not dry_run:
                call(f"dns/delete/{domain}/{record['id']}")

    for sub, rtype, content in desired:
        if (sub, rtype, content) in keep:
            print(f"  keep   {rtype:<5} {fqdn(domain, sub)} -> {content}")
            continue
        print(f"  create {rtype:<5} {fqdn(domain, sub)} -> {content}")
        if not dry_run:
            call(
                f"dns/create/{domain}",
                {"name": sub, "type": rtype, "content": content, "ttl": "600"},
            )


def sync_forward(domain, destination, dry_run):
    """Forward the apex and every subdomain to destination."""
    existing = call(f"domain/getUrlForwarding/{domain}")["forwards"]

    wanted = {
        "subdomain": "",
        "location": destination,
        "type": "permanent",
        "includePath": "no",
        "wildcard": "yes",
    }
    if len(existing) == 1 and all(
        (existing[0].get(k) or "") == v for k, v in wanted.items()
    ):
        print(f"  keep   forward @ -> {destination}")
        return

    for forward in existing:
        print(f"  delete forward {forward.get('subdomain') or '@'} -> {forward['location']}")
        if not dry_run:
            call(f"domain/deleteUrlForward/{domain}/{forward['id']}")

    print(f"  create forward @ -> {destination} (permanent, wildcard)")
    if not dry_run:
        call(f"domain/addUrlForward/{domain}", wanted)


def replace(domain, records, selector, desired, dry_run, label):
    """Delete every record in records matching selector, then create desired.

    selector is a predicate over a Porkbun record dict, so callers decide
    exactly how narrow the blast radius is. desired is a list of
    (subdomain, type, content, priority) tuples.
    """
    existing = [r for r in records if selector(r)]

    wanted = {(fqdn(domain, s), t, c.rstrip("."), p) for s, t, c, p in desired}
    present = {
        (r["name"], r["type"], r["content"].rstrip("."), r.get("prio") or None)
        for r in existing
    }

    if wanted == present:
        for _, rtype, content, _ in desired:
            print(f"  keep   {rtype:<5} {label} -> {content}")
        return

    for record in existing:
        print(f"  delete {record['type']:<5} {record['name']} -> {record['content'][:60]}")
        if not dry_run:
            call(f"dns/delete/{domain}/{record['id']}")

    for sub, rtype, content, prio in desired:
        shown = content if len(content) <= 60 else content[:57] + "..."
        print(f"  create {rtype:<5} {fqdn(domain, sub)} -> {shown}")
        if not dry_run:
            payload = {"name": sub, "type": rtype, "content": content, "ttl": "600"}
            if prio is not None:
                payload["prio"] = prio
            call(f"dns/create/{domain}", payload)


def sync_mail(domain, dry_run, dkim=None, verify=None):
    """Publish Google Workspace mail records, leaving unrelated TXT alone."""
    apex = fqdn(domain, "")
    records = call(f"dns/retrieve/{domain}")["records"]

    print("  -- MX --")
    replace(
        domain,
        records,
        lambda r: r["type"] == "MX" and r["name"] == apex,
        [("", "MX", host, prio) for prio, host in GOOGLE_MX],
        dry_run,
        apex,
    )

    # Only the SPF record is touched. A google-site-verification TXT also lives
    # at the apex and must survive, or the domain falls out of verification.
    print("  -- SPF --")
    replace(
        domain,
        records,
        lambda r: (
            r["type"] == "TXT"
            and r["name"] == apex
            and r["content"].strip('"').lower().startswith("v=spf1")
        ),
        [("", "TXT", GOOGLE_SPF, None)],
        dry_run,
        apex,
    )

    print("  -- DMARC --")
    replace(
        domain,
        records,
        lambda r: r["type"] == "TXT" and r["name"] == fqdn(domain, "_dmarc"),
        [("_dmarc", "TXT", DMARC, None)],
        dry_run,
        fqdn(domain, "_dmarc"),
    )

    if dkim:
        print("  -- DKIM --")
        replace(
            domain,
            records,
            lambda r: r["type"] == "TXT" and r["name"] == fqdn(domain, "google._domainkey"),
            [("google._domainkey", "TXT", dkim, None)],
            dry_run,
            fqdn(domain, "google._domainkey"),
        )

    if verify:
        print("  -- site verification --")
        replace(
            domain,
            records,
            lambda r: (
                r["type"] == "TXT"
                and r["name"] == apex
                and r["content"].strip('"').startswith("google-site-verification=")
            ),
            [("", "TXT", verify, None)],
            dry_run,
            apex,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show changes without applying")
    parser.add_argument(
        "--mail",
        choices=["google"],
        help="publish mail records instead of web records",
    )
    parser.add_argument("--dkim", help="DKIM TXT value from the Workspace Admin console")
    parser.add_argument("--verify", help="google-site-verification=... TXT value")
    args = parser.parse_args()

    missing = [k for k in ("PORKBUN_API_KEY", "PORKBUN_SECRET_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing environment variable(s): {', '.join(missing)}")

    try:
        call("ping")
        if args.mail:
            print(f"{SITE_DOMAIN}: mail -> Google Workspace")
            sync_mail(SITE_DOMAIN, args.dry_run, dkim=args.dkim, verify=args.verify)
        else:
            print(f"{SITE_DOMAIN}: DNS -> GitHub Pages")
            sync_records(SITE_DOMAIN, args.dry_run)
            print(f"{REDIRECT_DOMAIN}: URL forward -> https://{SITE_DOMAIN}")
            sync_forward(REDIRECT_DOMAIN, f"https://{SITE_DOMAIN}", args.dry_run)
    except PorkbunError as exc:
        sys.exit(f"Porkbun API error: {exc}")

    print("\nDry run, nothing changed." if args.dry_run else "\nDone.")


if __name__ == "__main__":
    main()
