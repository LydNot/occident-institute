# occident.institute

Holding page for Occident, a California nonprofit public benefit corporation.

Static single file, no build step. `index.html` is the whole site.

## Deployment

Served by GitHub Pages from the `main` branch root. `CNAME` pins the custom
domain, so leave it in place — GitHub rewrites the repo's Pages settings from it
on every push.

## DNS

Managed at Porkbun.

- `occident.institute` — apex `A`/`AAAA` records pointing at GitHub Pages, plus a
  `www` `CNAME` to `lydnot.github.io`
- `occident.now` — URL forward to `https://occident.institute`

`scripts/porkbun_dns.py` applies both. It reads `PORKBUN_API_KEY` and
`PORKBUN_SECRET_KEY` from the environment, and takes `--dry-run`. API access has
to be enabled per domain in the Porkbun panel under Details -> API Access.

## Mail

Not set up yet. The same script has a mail mode for Google Workspace:

    porkbun_dns.py --mail google [--verify google-site-verification=...] [--dkim "v=DKIM1; ..."]

It writes the MX record, an SPF record, and a reporting-only DMARC record,
touching nothing else — the web records and any unrelated TXT entries are left
alone.

Do not run it until the Workspace account exists and the domain is verified.
Google's MX record published ahead of a working account means inbound mail
bounces rather than queues. Porkbun's own email forwarding has no API and must
be configured in their web UI, which writes its own MX records.
