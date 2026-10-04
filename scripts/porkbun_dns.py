#!/usr/bin/env python3
"""Point occident.institute at GitHub Pages and forward occident.now to it.

Reads PORKBUN_API_KEY and PORKBUN_SECRET_KEY from the environment. Run with
--dry-run first to see what would change.

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
    for forward in call(f"domain/getUrlForwarding/{domain}")["forwards"]:
        print(f"  delete forward {forward.get('subdomain') or '@'} -> {forward['location']}")
        if not dry_run:
            call(f"domain/deleteUrlForward/{domain}/{forward['id']}")

    print(f"  create forward @ -> {destination} (permanent, wildcard)")
    if not dry_run:
        call(
            f"domain/addUrlForward/{domain}",
            {
                "subdomain": "",
                "location": destination,
                "type": "permanent",
                "includePath": "no",
                "wildcard": "yes",
            },
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show changes without applying")
    args = parser.parse_args()

    missing = [k for k in ("PORKBUN_API_KEY", "PORKBUN_SECRET_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing environment variable(s): {', '.join(missing)}")

    try:
        call("ping")
        print(f"{SITE_DOMAIN}: DNS -> GitHub Pages")
        sync_records(SITE_DOMAIN, args.dry_run)
        print(f"{REDIRECT_DOMAIN}: URL forward -> https://{SITE_DOMAIN}")
        sync_forward(REDIRECT_DOMAIN, f"https://{SITE_DOMAIN}", args.dry_run)
    except PorkbunError as exc:
        sys.exit(f"Porkbun API error: {exc}")

    print("\nDry run, nothing changed." if args.dry_run else "\nDone.")


if __name__ == "__main__":
    main()
