# Deploying `contacts.nlma.io`

Runbook for the NLMA Hostinger VPS (`178.16.141.166`), matching the
conventions used by the other `*.nlma.io` MCP servers.

```
client --TLS--> nginx (443) --proxy--> 127.0.0.1:8020  (MCP HTTP transport)
```

- Install path: `/opt/contacts-mcp`
- Internal port: `8020` (loopback only)
- Runtime: systemd + Python 3.12 venv (`User=root`, like sibling services)
- TLS: certbot on the VPS
- Auth: none at nginx by default (matches sibling MCPs). Add basic auth
  or a bearer check in `contacts.nlma.io` if needed.

## Prerequisites

- DNS A record for `contacts.nlma.io` → `178.16.141.166`.
- Google OAuth: either `credentials.json` or `GOOGLE_CLIENT_ID` /
  `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` in `.env`.

## Provision

```bash
ssh root@178.16.141.166

mkdir -p /opt/contacts-mcp
cd /opt/contacts-mcp
git clone https://github.com/NextLevelManagementAdvisors/mcp-google-contacts-server.git .
git checkout deploy/contacts-nlma-io

python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install .

cp .env.example .env
# Edit .env with GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN
# OR scp credentials.json to /opt/contacts-mcp/credentials.json
```

## systemd

```bash
cp /opt/contacts-mcp/deploy/systemd/contacts-mcp.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now contacts-mcp
systemctl status contacts-mcp
journalctl -u contacts-mcp -n 50 --no-pager

curl -fsS http://127.0.0.1:8020/   # sanity check
```

## nginx + TLS

```bash
cp /opt/contacts-mcp/deploy/nginx.conf.example /etc/nginx/sites-available/contacts.nlma.io
ln -s /etc/nginx/sites-available/contacts.nlma.io /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

certbot --nginx -d contacts.nlma.io

curl -fsS https://contacts.nlma.io/
```

## Update

```bash
cd /opt/contacts-mcp
git pull
.venv/bin/pip install .
systemctl restart contacts-mcp
```

## Rollback

```bash
cd /opt/contacts-mcp
git log --oneline -5
git checkout <previous-sha>
.venv/bin/pip install .
systemctl restart contacts-mcp
```

## Security notes

- MCP server binds loopback only; public access is nginx → proxy.
- `.env` and `credentials.json` are gitignored. Transfer out-of-band; never commit.
- If this hostname ever gets customer-facing traffic, add basic auth or a
  bearer token check in the nginx config before that happens.
