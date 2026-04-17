# Deploying `contacts.nlma.io`

Runbook for putting this MCP server behind the public hostname `contacts.nlma.io` on the NLMA VPS.

## Architecture

```
client --TLS--> nginx (443, auth) --HTTP--> 127.0.0.1:8000  (MCP HTTP transport)
```

The app binds only to loopback. Nginx terminates TLS and enforces auth. Two runtime options are provided:

- **Docker Compose** (recommended) — `Dockerfile` + `docker-compose.yml` in repo root.
- **systemd + venv** — `deploy/systemd/contacts-mcp.service`.

## Prerequisites

- VPS with Ubuntu/Debian, root or sudo access.
- Python 3.12 available (Docker option ships its own).
- DNS A/AAAA for `contacts.nlma.io` pointed at the VPS.
- Google OAuth credentials (`credentials.json`) or `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN`.
- nginx + certbot installed on the VPS.

## One-time host setup

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx apache2-utils
sudo mkdir -p /opt/contacts-mcp
sudo chown "$USER":"$USER" /opt/contacts-mcp
```

## Option 1 — Docker Compose

```bash
# 1. On the VPS
cd /opt/contacts-mcp
git clone https://github.com/<org>/mcp-google-contacts-server.git .
git checkout deploy/contacts-nlma-io

# 2. Credentials
cp .env.example .env
# Edit .env with GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN
# OR place credentials.json in this directory (docker-compose mounts it read-only)

# 3. Build and start
docker compose up -d --build
docker compose logs -f        # verify startup
curl -fsS http://127.0.0.1:8000/  # sanity check
```

## Option 2 — systemd + venv

```bash
cd /opt/contacts-mcp
git clone https://github.com/<org>/mcp-google-contacts-server.git .
git checkout deploy/contacts-nlma-io

python3.12 -m venv .venv
.venv/bin/pip install .

cp .env.example .env         # fill in creds
# place credentials.json at /opt/contacts-mcp/credentials.json (optional)

sudo useradd --system --home /opt/contacts-mcp --shell /usr/sbin/nologin contacts-mcp
sudo chown -R contacts-mcp:contacts-mcp /opt/contacts-mcp

sudo cp deploy/systemd/contacts-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now contacts-mcp
sudo systemctl status contacts-mcp
```

## Nginx + TLS

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/contacts.nlma.io
sudo ln -s /etc/nginx/sites-available/contacts.nlma.io /etc/nginx/sites-enabled/

# Basic auth credentials (required by the example config)
sudo htpasswd -c /etc/nginx/.contacts_htpasswd nlma

sudo nginx -t
sudo systemctl reload nginx

# Issue TLS cert
sudo certbot --nginx -d contacts.nlma.io
```

## Verify

```bash
curl -u nlma:<pw> -fsS https://contacts.nlma.io/
```

## Update

```bash
cd /opt/contacts-mcp
git pull

# Docker:
docker compose up -d --build

# systemd:
.venv/bin/pip install .
sudo systemctl restart contacts-mcp
```

## Rollback

```bash
cd /opt/contacts-mcp
git log --oneline -5
git checkout <previous-sha>
docker compose up -d --build   # or: sudo systemctl restart contacts-mcp
```

## Security notes

- The MCP server has no built-in auth. Do **not** expose port 8000 directly. Always front with nginx basic auth, a bearer-token check, or Cloudflare Access.
- `.env` and `credentials.json` are gitignored. Transfer them out-of-band (`scp`), never commit.
- Rotate `GOOGLE_REFRESH_TOKEN` if the VPS is ever compromised.
