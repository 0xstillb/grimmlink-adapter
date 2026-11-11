# GrimmLink Adapter

> Connect KOReader and Readest to an **unmodified Grimmory server**.

**Choose a language / เลือกภาษา:** [🇬🇧 English](README.en.md) · [🇹🇭 ภาษาไทย](README.th.md)

## Quick links

- 🖥️ Run on this computer: open **[http://localhost:8085](http://localhost:8085)** after starting the Adapter.
- 🍓 Fresh Raspberry Pi install: follow the [English](README.en.md#fresh-install-on-a-raspberry-pi) or [Thai](README.th.md#ติดตั้งใหม่บน-raspberry-pi) guide.
- 📱 Connect a reader on your home network: use `http://<Pi-LAN-IP>:8085` in its GrimmLink/Adapter URL setting.
- 🌐 Access from outside home: use the Pi's Tailscale IP; do not expose port `8085` to the public Internet.

The Adapter keeps reader APIs compatible with Grimmory, stores its own cache and sync state in SQLite, and provides a local Metadata setup and diagnostics page. It does not replace Grimmory or write directly to Grimmory's database.

The Pi Compose example uses the GHCR `latest` image tag. CI publishes a new `latest` image after a successful `main` build or version release; update the Pi with `docker compose pull` and `docker compose up -d`.

For source code, configuration details, reader setup, updates, backups, and troubleshooting, use the language guide above. The Pi Compose example is [`deploy/pi-compose.yml`](deploy/pi-compose.yml); environment variable examples are in [`.env.example`](.env.example).

## Local addresses

| Where the client runs | URL to enter |
| --- | --- |
| Browser on the same computer as the Adapter | `http://localhost:8085` |
| Readest/KOReader on another device in the house | `http://<computer-or-Pi-LAN-IP>:8085` |
| Device connected to the home network through Tailscale | `http://<Pi-Tailscale-IP>:8085` |

`localhost` always means **the device you are using**. A reader on a separate device cannot use `localhost` to reach an Adapter running on your PC or Pi.
