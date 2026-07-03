# HouseSpotter 🏡

A personal, self-hosted UK property scout. Describe what you're looking for in plain
English; HouseSpotter continuously watches Rightmove and OnTheMarket (Zoopla optional),
scores every listing against your criteria with a transparent 0–100 match score,
researches the best neighbourhoods for your budget, and pings you on Telegram/email the
moment something good appears.

Built for a single user, running natively on a Raspberry Pi (no Docker), reachable from
your phone over your existing Tailscale tailnet.

> **Personal use only.** Rightmove, Zoopla and OnTheMarket have no public API and their
> terms prohibit automated access. HouseSpotter is built for private, low-volume,
> personal use: it polls gently (every 30±10 min, 4–10 s between requests), caches
> aggressively, backs off and pauses on any block signal, and never redistributes data.
> Portals change their markup from time to time — expect the occasional adapter fix.

## Features

- **Agent chat (Claude Opus)** — describe your ideal home; the agent builds and edits
  your structured search profile for you.
- **Multi-portal scraping** — Rightmove + OnTheMarket via their embedded JSON (no
  browser needed); Zoopla optional behind a Playwright flag. Buy and rent modes.
- **Match scoring** — hard filters (price, beds, area, must-haves) plus a weighted soft
  score; free-text desires ("period features", "light and airy") scored by Claude Haiku
  with per-listing caching. Every score has an expandable breakdown.
- **Neighbourhood research** — ranks the areas where your money goes furthest using free
  UK data (OpenStreetMap amenities, police.uk crime, postcodes.io), blended with your
  quality-of-life priorities, with readable area profiles.
- **Alerts** — Telegram and/or email, instant or daily digest, quiet hours, price-drop
  alerts on saved properties. Each match alerts exactly once.
- **Lists** — save properties to custom lists with notes.

## Requirements

- Python 3.11+, Node 18+ (frontend build only), SQLite (bundled with Python)
- An [Anthropic API key](https://platform.claude.com/) for the AI features (~£5/mo at
  personal usage; everything else works without it)
- Optional: Telegram bot token (free, via @BotFather) and/or SMTP credentials for alerts

## Quick start (development, any OS)

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt          # Windows: .venv\Scripts\pip
cp .env.example .env                                        # set HS_PASSWORD + HS_SESSION_SECRET
cd frontend && npm install && npm run build && cd ..
.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8410
```

Open http://localhost:8410, log in, and either talk to the Agent (needs the Anthropic
key) or create a profile in Settings. Hit **Status → Scan now** for the first scrape.

For frontend development: `cd frontend && npm run dev` (proxies /api to :8410).

## Raspberry Pi install (production)

```bash
git clone <this repo> && cd HouseSpotter
sudo bash deploy/install-pi.sh
sudo nano /opt/housespotter/.env      # set password, API keys
sudo systemctl start housespotter
```

The installer: creates a `housespotter` system user, installs to `/opt/housespotter`
with its own venv, builds the frontend, registers a hardened systemd service on port
**8410**, and adds a nightly SQLite backup (2:30am, 14-day retention, to
`/opt/housespotter/backups`).

**It does not touch Tailscale, Docker, or any other service.** If your Pi is already on
a tailnet, the app is immediately reachable from your phone at
`http://<pi-tailscale-name>:8410` — no `tailscale serve`, no port forwarding, no config
changes. Traffic inside the tailnet is WireGuard-encrypted; the app additionally
requires the password login.

### Operations

```bash
sudo systemctl status housespotter     # health
sudo journalctl -u housespotter -f     # logs
curl http://localhost:8410/health      # liveness endpoint
```

The **Status** page in the UI shows every scrape run per portal, including block
detections (a blocked portal pauses itself for 6h and sends you a Telegram warning).

### Updating

```bash
cd ~/HouseSpotter && git pull
sudo bash deploy/install-pi.sh        # re-syncs code, keeps .env and data
sudo systemctl restart housespotter
```

## Configuration (.env)

See `.env.example` for the full list. Key settings:

| Variable | Purpose |
|---|---|
| `HS_USERNAME` / `HS_PASSWORD` | Login (user created on first start) |
| `HS_SESSION_SECRET` | Cookie signing — generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `HS_ANTHROPIC_API_KEY` | Agent chat, desire scoring, area narratives |
| `HS_TELEGRAM_BOT_TOKEN` / `HS_TELEGRAM_CHAT_ID` | Telegram alerts |
| `HS_SMTP_*` | Email alerts |
| `HS_SCRAPE_ENABLED` | `false` disables the scheduler (manual scans still work) |
| `HS_PLAYWRIGHT_FALLBACK` | `true` enables the experimental Zoopla adapter (needs `pip install playwright` + Chromium) |

## Architecture

```
backend/app/
  scraping/    portal adapters (rightmove, onthemarket, zoopla) + normalizer + scheduler jobs
  scoring/     hard filters + weighted scoring + Haiku desire scoring (cached)
  research/    geocoding + free UK data sources + area ranking engine
  llm/         Anthropic client with DB response cache + intake chat
  notify/      Telegram/email channels + alert ledger
  api/         REST routes    models.py  SQLite schema (SQLModel)
frontend/      React + TypeScript + Tailwind + Leaflet (built to static, served by FastAPI)
deploy/        systemd unit, Pi installer, backup script
```

Data lives in a single SQLite file (`data/housespotter.db`, WAL mode). Scoring results
and all LLM responses are cached in the database, so nothing is ever paid for twice.
