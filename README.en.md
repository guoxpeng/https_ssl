# https_ssl — Self-Signed Certificate Manager

<div align="center">
  <img src="./static/images/https_ssl-logo.png" alt="https_ssl Logo" width="150">
  <p>An easy-to-use self-signed certificate manager</p>
  <img src="./static/images/https_ssl-zt.png" width="1000">
</div>

[中文说明](./README.md) · [Deployment guide (Chinese)](./INSTALL.md)

## What it is

`https_ssl` is a self-signed certificate management system built on Flask and OpenSSL.
It turns the whole "make my LAN service HTTPS, and make my devices actually trust it"
workflow into a web panel:

**create your own root CA → issue server certificates from it → wire up an HTTPS
reverse proxy in one click → install the root CA on your devices.**

No need to memorise OpenSSL command lines, and no more browser "Not secure" warnings
on your intranet.

This repository is the Linux / Docker port of the upstream project. The reverse proxy
no longer depends on a Windows-only `proxy.exe`; it is handled by an nginx process
inside the same container, so every feature works on Linux (verified on fnOS / Feiniu NAS
and Debian 12).

## Features

### 1. Certificate Authority (CA) management
- Create your own root CA in one click, with optional private key password protection
- View and download the CA certificate, manage its lifecycle
- Install the root certificate on phones / PCs so they trust everything you issue

### 2. Certificate issuance and management
- Issue server certificates from your own CA
- Multi-domain and multi-IP support via SAN extensions, including wildcards (`*.example.com`)
- List certificates with expiry dates, batch delete
- Secure download of certificate and private key

> Certificate names accept Chinese/English letters, digits, underscore, hyphen and dot,
> length 1–64, and must be unique (duplicates return HTTP 409). `ca` is reserved.

### 3. Certificate verification
- Parse the certificate, check that the private key matches, check the validity period
- Compare the certificate's SAN against the address you entered
- Finish with a real TLS handshake on the loopback address
- Upload an external certificate to verify it against the system trust store

### 4. HTTPS reverse proxy
- Put HTTPS in front of an existing HTTP service (e.g. `http://192.168.5.3:4000`)
- Pick a certificate in the panel — no hand-written nginx config
- Multi-site support, hot reload on change, start/stop per service without touching others

## Architecture

Everything runs in a single container; `entrypoint.sh` starts nginx and Flask together:

```
┌───────────────────────────────────────────┐
│   qilin_ssl                               │
│                                           │
│  ┌──────────────┐    ┌──────────────────┐ │
│  │ nginx        │    │ Flask            │ │
│  │ · TLS        │◄───│ · issuance       │ │
│  │ · routing    │    │ · CA mgmt        │ │
│  │ · hot reload │    │ · proxy config   │ │
│  └──────────────┘    └──────────────────┘ │
│    :14000 etc.          :2002             │
└───────────────────────────────────────────┘
```

- **nginx** terminates TLS and forwards requests; site configs live in `/app/proxy/sites`.
- **Flask** is the management panel; it also generates nginx site configs and triggers
  `nginx -s reload`.

The proxy needs no Windows binaries and no Docker socket mount, so the container runs
with far fewer privileges.

## Requirements

- Linux (verified on fnOS / Feiniu NAS and Debian 12)
- Docker Engine 20.10+ and Docker Compose v2
- The host does **not** need Python, OpenSSL or nginx — they are all inside the image

## Quick start (Docker)

### 1. Get the code onto your server

```bash
git clone <your-repo-url> https_ssl
cd https_ssl
```

Or simply upload the project directory, e.g. to `/vol1/docker/https_ssl`.

### 2. Start it

```bash
docker compose up -d --build
```

> On Feiniu NAS and similar systems the `admin` user is not in the `docker` group,
> so prefix the command with `sudo`: `sudo docker compose up -d --build`

### 3. Open the panel

```
http://<server-ip>:2002
```

Default credentials:

| Username | Password |
| -------- | -------- |
| `admin`  | `admin`  |

**Change the password in the Settings page right after you log in.**

To use a different initial password, create a `.env` file in the project directory:

```bash
echo 'QILIN_ADMIN_PASSWORD=your-password' > .env
docker compose up -d
```

This password is only used on **first start** to create the account. Once the account
exists, changing `.env` has no effect — use the Settings page instead.

### 4. Three steps to get going

1. **Create the root CA** — Home → "Create" → organisation name (optional) → create.
   Then download `qilin-ca.crt` and install it on the devices that need to trust it.
2. **Issue a server certificate** — "Certificate list" → "Add" → name → IPs and domains
   (semicolon-separated) → create.
3. **Configure the reverse proxy** — declare the port in `docker-compose.yml` first
   (the container cannot see host port mappings), run `docker compose up -d`, then in the
   Reverse Proxy page fill in the original address, the new HTTPS address and pick a
   certificate. Save and toggle it on — **no container restart needed**.

### Making devices trust the CA

Download `qilin-ca.crt` from the panel, then:

**Windows** (PowerShell, as Administrator):

```powershell
.\scripts\trust-ca-windows.ps1 -CaPath .\qilin-ca.crt
```

**Linux / macOS**:

```bash
sudo sh scripts/trust-ca-unix.sh ./qilin-ca.crt
```

**iOS / Android**: transfer `qilin-ca.crt` to the device, install it, then enable
"full trust" manually in the system settings.

## Configuration

All settings are environment variables:

| Variable | Default | Description |
|---|---|---|
| `QILIN_ADMIN_PASSWORD` | `admin` | Initial password used to create the `admin` account on first start. **Only applies while `users.json` does not exist** |
| `QILIN_SECRET_KEY` | random | Session signing key. When empty a random key is generated on every start, **so every container restart logs everyone out** — set it explicitly |
| `QILIN_COOKIE_SECURE` | `0` | Set to `1` to send the session cookie over HTTPS only |
| `QILIN_PROXY_DIR` | `/app/proxy` | Reverse proxy site configs and certificates |
| `QILIN_PROXY_LISTEN_HOST` | empty | Proxy listen address; empty means all addresses |
| `QILIN_OPENSSL` | `/usr/bin/openssl` | Path to the OpenSSL executable |

> The `QILIN_*` prefix is kept for compatibility with the upstream project, so existing
> deployments and scripts keep working after the rename.

## Common commands

```bash
# status and logs
docker compose ps
docker compose logs -f

# restart / stop
docker compose restart
docker compose down

# upgrade (after changing code)
docker compose up -d --build

# health check against your real data
docker cp scripts/verify_deploy.py qilin_ssl:/tmp/
docker exec qilin_ssl python /tmp/verify_deploy.py

# full smoke test (runs in a temp dir, never touches your data)
docker exec qilin_ssl python /app/scripts/smoke_test.py
```

## Backup

Only two things need backing up: the `data/` directory and the `.env` file.

```bash
tar czf https_ssl_backup.tar.gz data .env
```

`data/ca/qilin-ca.key` is the root of the whole chain — **if you lose it, every
certificate you ever issued becomes invalid**. Keep an offline copy.

## Security notes

- The CA certificate and key generated by this system are intended for testing and
  development; use a public CA in production if the service faces the internet
- The CA private key is never distributed through the panel (`/download/ca/` only
  serves the root certificate `qilin-ca.crt`) — copy it from the host `./data/ca` if
  you need a backup
- **Do not expose the panel to the public internet.** It uses the Flask development
  server and is meant for LAN use only; put another HTTPS reverse proxy in front if
  you really need external access
- nginx and Flask run as the same user inside the container; keep the container off
  untrusted networks
- Change the default `admin` / `admin` credentials immediately

## Known gotchas

1. **Recreating the container may reset your password.** `users.json` lives inside the
   container, not in a mounted volume. `docker compose up -d --build` recreates the
   account from `.env` and **your changed password is lost**. Back it up and restore:

   ```bash
   docker cp qilin_ssl:/app/users.json ./data/users.json.bak     # before upgrading
   # ...docker compose up -d --build...
   docker cp ./data/users.json.bak qilin_ssl:/app/users.json
   docker exec qilin_ssl chmod 600 /app/users.json
   docker restart qilin_ssl
   ```

2. **Proxy ports must be declared first.** A proxy port that is not listed under
   `ports` in `docker-compose.yml` is unreachable from the host.

3. **Frequent logouts.** `QILIN_SECRET_KEY` is unset, so the session key changes on
   every restart. Pin it: `echo "QILIN_SECRET_KEY=$(openssl rand -hex 32)" >> .env`

4. **"Certificate X does not exist" when toggling a proxy.** The certificate referenced
   by that service was deleted. Edit the service, pick a certificate again and save.

## Self-check

`scripts/smoke_test.py` runs the complete flow in a temporary directory
(create CA → issue → verify → proxy create/start/stop/delete → delete cascade)
without touching your real data:

```bash
python scripts/smoke_test.py
# On Windows, if openssl is not on PATH:
#   set QILIN_OPENSSL=C:\Program Files\Git\usr\bin\openssl.exe
```

## License

Released under the [MIT License](./LICENSE).

Lineage: **[linzxcw/qilin_SSL](https://github.com/linzxcw/qilin_SSL)** is the author's
original repository (upstream) → [guoxpeng/fnos_qilin_SSL](https://github.com/guoxpeng/fnos_qilin_SSL)
is its fnOS port → this repository `https_ssl` is the Linux / Docker port built on top
of the latter (the reverse proxy was moved from a Windows binary to nginx in the same
container).

> Note: the original repository ships no license file. If you plan to publish this
> publicly, consider asking the original author for permission first.

## Credits

- Original project: [linzxcw/qilin_SSL](https://github.com/linzxcw/qilin_SSL)
- fnOS adaptation: [guoxpeng/fnos_qilin_SSL](https://github.com/guoxpeng/fnos_qilin_SSL)
