# GrimmLink Adapter · English

[🇬🇧 English](README.en.md) · [🇹🇭 ภาษาไทย](README.th.md) · [← Project overview](README.md)

## ✨ What it does

GrimmLink Adapter connects KOReader and Readest to a **stock, unmodified Grimmory server**. It translates reader requests, syncs reading data, and hosts a private setup page for Metadata automation and diagnostics.

- 📚 Sync shelves, reading progress/status, bookmarks, highlights, ratings, and reading sessions supported by the configured reader/plugin.
- 🔐 Readers authenticate with their own KOReader/Grimmory credentials. The Metadata page uses a Grimmory account once to connect its background account.
- 🗃️ Adapter cache, credentials, retry state, and sync bookkeeping are stored in its own SQLite data volume.
- 🧭 OPF and JSON sidecars can be used as metadata sources. The Pi Compose example writes metadata through Grimmory's API and mounts the book/NAS folder read-only.
- 🚫 The Adapter does not replace Grimmory, write directly to Grimmory's MariaDB, or require a public Internet port.

## 🚀 Pick how to run it

| Setup | Best for | Reader URL |
| --- | --- | --- |
| Local Python | Trying it on Windows/macOS/Linux or same-computer access | `http://localhost:8085` |
| Raspberry Pi Docker | Always-on home sync for devices on LAN/Tailscale | `http://<Pi-IP>:8085` |

> **About localhost:** `localhost` points back to the device where the app is running. A Readest/KOReader device must use the host computer's LAN IP, the Pi's LAN IP, or its Tailscale IP.

## 🖥️ Run locally on Windows/macOS/Linux

### Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Grimmory reachable from this computer (default: `http://localhost:6060`)

### Install and start

```bash
git clone https://github.com/0xstillb/grimmlink-adapter.git
cd grimmlink-adapter
uv sync
```

Create `.env` from the template:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

Edit `.env` and set at least:

```dotenv
GRIMMORY_BASE_URL=http://localhost:6060
ADAPTER_HOST=0.0.0.0
ADAPTER_PORT=8085
GRIMMORY_DB_ENABLED=false
```

Then start the Adapter:

```bash
uv run python -m grimmlink_adapter.main
```

Open **[http://localhost:8085](http://localhost:8085)**. The root redirects to the Metadata page at `/api/grimmlink/v2/local/metadata`.

### Use it from another device

Find this computer's LAN IP and enter `http://<computer-LAN-IP>:8085` in the reader's Adapter/GrimmLink URL setting. Allow TCP port `8085` through the computer's firewall for your private LAN only. If accessing from outside, connect through Tailscale and use the computer's Tailscale IP. Do not port-forward `8085` from the router.

If Grimmory is not on this computer, set `GRIMMORY_BASE_URL` to a URL reachable **from the Adapter process**. For example, use the Grimmory host's LAN IP instead of `localhost`. In Docker, `localhost` points to the Adapter container itself; use the Grimmory service name on a shared Docker network or a host-reachable address.

## 🍓 Fresh install on a Raspberry Pi

This installs **only the Adapter**. It assumes Grimmory, MariaDB, Docker Compose, and the NAS mount already exist on the Pi. Use a 64-bit Raspberry Pi OS/Docker host.

### 1. Create the Adapter folder and data volume

```bash
sudo mkdir -p /opt/grimmlink-adapter
cd /opt/grimmlink-adapter
sudo curl -fsSL https://raw.githubusercontent.com/0xstillb/grimmlink-adapter/main/deploy/pi-compose.yml -o compose.yml
sudo docker volume create grimmlink_adapter_data
```

The named volume preserves Adapter state across container recreation and upgrades.

### 2. Create the private `.env`

```bash
sudo nano .env
```

Add these values, replacing the examples:

```dotenv
GRIMMLINK_ADAPTER_IMAGE=ghcr.io/0xstillb/grimmlink-adapter:latest
LAN_IP=192.168.1.50
NAS_ROOT=/mnt/nas/books
GRIMMORY_DB_HOST=172.18.0.2
GRIMMORY_DB_USER=grimmlink_readonly
GRIMMORY_DB_PASSWORD=replace-with-a-strong-password
GRIMMORY_DB_NAME=grimmory
GRIMMORY_DB_PORT=3306
```

- `LAN_IP`: reserve a stable address for the Pi in your router.
- The Pi Compose file listens on `0.0.0.0` so both LAN and Tailscale interfaces can reach it. Restrict TCP `8085` with the Pi firewall to trusted networks; do not forward it from the router.
- `NAS_ROOT`: the **existing host path** where Grimmory can read the books. The Adapter mounts it read-only at `/imports`.
- `GRIMMORY_DB_HOST`: an address reachable from the Pi's host-network Adapter container for MariaDB. It is not necessarily the Grimmory web/API address. If unsure, inspect the MariaDB container/network on the Pi and use the address that accepts the dedicated read-only account.
- `GRIMMORY_DB_USER` / `PASSWORD`: a dedicated MariaDB account with **SELECT-only** access. Do not use Grimmory's database admin account.
- `GRIMMLINK_ADAPTER_IMAGE`: GHCR image to run. The default `latest` tag is published for successful `main` builds and version releases, after validation passes. For a pinned rollout, replace `latest` with an exact published version or commit tag.

The database lookup is needed to resolve some first-time reader book hashes on stock Grimmory. It is read-only. Keep `.env` private:

```bash
sudo chmod 600 .env
```

### 3. Start the Adapter

```bash
sudo docker compose -f compose.yml config
sudo docker compose -f compose.yml pull
sudo docker compose -f compose.yml up -d
```

Check it:

```bash
sudo docker compose -f compose.yml ps
sudo docker compose -f compose.yml logs --tail=100 grimmlink-adapter
curl -fsS http://127.0.0.1:8085/healthcheck
curl -i http://127.0.0.1:8085/readyz
```

`/healthcheck` reports whether the Adapter process is alive. `/readyz` returns `200` when Grimmory is reachable and `503` while Grimmory is still starting or unavailable. A `503` from `/readyz` does not mean the Adapter needs a restart.

### 4. Open the page and connect readers

- On the Pi itself: **[http://localhost:8085](http://localhost:8085)**
- On a device at home: **`http://<Pi-LAN-IP>:8085`**
- Away from home with Tailscale: **`http://<Pi-Tailscale-IP>:8085`**

For KOReader and Readest, set the GrimmLink/Adapter server URL to the Pi address above. Do not enter `localhost` on a separate reader device. Keep router port-forwarding disabled; use Tailscale for remote access.

## ⚙️ First-time setup and settings

### Reader sync

1. Open the reader's GrimmLink/Adapter settings.
2. Set its server URL to the Adapter address. Example: `http://192.168.1.50:8085`.
3. Sign in with the reader's existing KOReader/Grimmory account credentials, then run the plugin's shelf refresh/sync.

Use the IP address reachable from that reader: `localhost` for the same computer only, Pi LAN IP at home, or Pi Tailscale IP when connected remotely.

### Metadata UI

1. Open `http://<Adapter-address>:8085/` and sign in with the Grimmory account used to manage the Library.
2. In **Setup**, connect the background account once. Normal visits can reuse its short-lived Metadata-only session.
3. In **Diagnostics**, run the checks. Resolve any failed Grimmory API, database identity lookup, NAS-root, or mDNS check before relying on automation.
4. In **Metadata**, load the Library and map each Grimmory Library path to the correct mounted NAS root/folder. For the Pi Compose example, the mount is `/imports`.
5. Choose the source preference/fields and save the Library policy. For a file-by-file review, scan, preview the changes, and explicitly apply them.

The worker can process new books automatically when Auto is enabled. Existing books may need a one-time backfill. The Pi Compose example uses API-only metadata writes and never writes into the read-only NAS mount. Metadata source selection can use OPF with JSON sidecar fallback when the chosen Library policy allows it. Cover changes may remain pending when Grimmory has no safe supported upload route.

### Important environment variables

| Variable | Purpose | Example |
| --- | --- | --- |
| `GRIMMORY_BASE_URL` | Grimmory API address from the Adapter's network namespace | `http://localhost:6060` on local host; `http://<Pi-LAN-IP>:6060` in Pi host-network Compose |
| `ADAPTER_HOST` / `ADAPTER_PORT` | Adapter bind address and port | `0.0.0.0` / `8085` |
| `GRIMMORY_DB_ENABLED` | Enable optional read-only book/hash lookup | `true` on Pi Compose |
| `GRIMMORY_DB_HOST`, `USER`, `PASSWORD` | Dedicated SELECT-only MariaDB lookup credentials | Set privately in `.env` |
| `SQLITE_DB_PATH` | Adapter cache/outbox database | `data/grimmlink_adapter.db` locally; persistent volume on Pi |
| `METADATA_IMPORT_ROOTS` | Folders visible to the Metadata UI | `[]` locally; Pi Compose uses `[/imports]` |
| `METADATA_INGESTION_MODE` | Metadata write path | `api_only` keeps writes in Grimmory's API |
| `METADATA_FALLBACK` | Optional output-sidecar behavior | `none` disables output-sidecar fallback |
| `MDNS_ENABLED` | Advertise `grimmlink.local` on the local network | `true` |

See [`.env.example`](.env.example) for advanced timeout, TLS, and metadata settings. Changes to `.env` require restarting the Adapter process/container.

## 🔄 Update and backup

The Pi Compose file defaults to `latest` and checks the registry for it whenever the service is brought up. To fetch and apply the newest published image, run:

```bash
sudo docker compose -f compose.yml pull grimmlink-adapter
sudo docker compose -f compose.yml up -d --no-deps grimmlink-adapter
sudo docker compose -f compose.yml ps
```

Back up `.env` and the `grimmlink_adapter_data` Docker volume together. The volume contains SQLite state and the Metadata encryption key; losing the key may require reconnecting background/linked accounts. Keep both the backup and `.env` private. Do not remove the volume when updating the container.

## 🛠️ Troubleshooting

| Symptom | Check |
| --- | --- |
| Reader cannot connect | Verify URL uses the Pi/computer LAN IP and port `8085`, confirm the Adapter is running, and allow TCP `8085` on the private LAN firewall. |
| Works in browser on Pi but not on reader | The reader cannot use `localhost`; use the Pi LAN IP or Tailscale IP. |
| `/healthcheck` works but `/readyz` is `503` | Grimmory is still starting or unreachable. Check `GRIMMORY_BASE_URL` and Grimmory logs; Adapter startup does not depend on Grimmory. |
| Metadata cannot find files | Confirm `NAS_ROOT` exists on the Pi, the NAS is mounted, and the Library mapping points to the correct `/imports` subfolder. |
| Hash/book lookup fails | Confirm the MariaDB address is reachable and the configured account has the required SELECT-only grants. |
| Container exits after a config change | Run `docker compose -f compose.yml config` and inspect `docker compose -f compose.yml logs --tail=100 grimmlink-adapter`. |

Useful endpoints:

- Local metadata page: `/` or `/api/grimmlink/v2/local/metadata`
- Liveness: `/healthcheck`
- Grimmory readiness: `/readyz`
- API schema for local development: `/docs`

## 🔒 Network and data safety

- Do not expose port `8085` to the public Internet or forward it on the router.
- Use a private home LAN or Tailscale for readers and the Metadata UI.
- Keep `.env`, SQLite, and its encryption key private; back them up together.
- The Pi Compose example mounts `/imports` read-only and uses Grimmory's API for writes. The Adapter never writes directly to MariaDB.
- Keep one Adapter worker/replica unless a shared session store is added.

## 📖 More documentation

- [Adapter specification](docs/ADAPTER_V2_SPEC.md)
- [Reader and server end-to-end checklist](docs/E2E_CHECKLIST.md)
- [Operations handoff](docs/HANDOFF.md)
- [Upstream API performance notes](docs/UPSTREAM_PERFORMANCE.md)
