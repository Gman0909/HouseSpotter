# HouseSpotter 🏡

A personal, self-hosted UK property scout. Set up a search in plain forms (or describe it
to the optional AI agent); HouseSpotter continuously watches Rightmove, OnTheMarket and Purplebricks
(Zoopla optional), scores every listing against your criteria with a transparent 0–100
match score, researches the best neighbourhoods for your budget, and pings you on
Telegram/email the moment something good appears.

Multi-user, running natively on a Raspberry Pi (no Docker), reachable from your phone
over your existing Tailscale tailnet.

> **Personal use only.** Rightmove, Zoopla and OnTheMarket have no public API and their
> terms prohibit automated access. HouseSpotter is built for private, low-volume,
> personal use: it polls gently (every 30±10 min, 4–10 s between requests), caches
> aggressively, backs off and pauses on any block signal, and never redistributes data.
> Portals change their markup from time to time — expect the occasional adapter fix.

## Features

- **Multi-user** — each user gets their own login, search profiles, saved lists,
  milestones, chat history and alert targets. The scraped property pool is shared.
  The first user administers server settings and accounts from the in-app Settings page.
- **Manual-first search profiles** — create and refine everything in the UI: multiple
  locations with per-location radius, price/bed/bath/floor-area ranges, property types,
  tenure, one-click exclusions (retirement, shared ownership, auction, park homes), and
  a validated palette of must-haves and weighted nice-to-haves. Every criteria change is
  snapshotted with one-click restore.
- **Optional AI agent** — describe your ideal home in chat and the agent fills in the
  same structured profile. Pick your provider in Settings: **Anthropic** (cloud, best
  quality) or **Ollama** (free, local network). With no provider configured, all AI
  features hide themselves and everything else keeps working.
- **Match scoring** — hard filters plus a weighted soft score with an expandable
  breakdown showing the evidence for every criterion. Free-text desires ("period
  features", "light and airy") are AI-judged per listing and cached.
- **Milestones & travel** — save your favourite places once; every property gets real
  routed drive/cycle/walk times (OpenRouteService, free) and a Milestone Access Score
  with modelled peak/off-peak range. Nearest train station with walk time on every
  property, and a generic "near a train station" scoring criterion.
- **Neighbourhood research** — ranks the areas where your money goes furthest using free
  UK data (OpenStreetMap amenities, police.uk crime, postcodes.io), blended with your
  quality-of-life priorities, with readable area profiles.
- **Alerts** — Telegram and/or email per user, instant or daily digest, quiet hours,
  price-drop alerts on saved properties. Each match alerts exactly once.
- **Lists** — save properties to custom lists with notes; per-user "new" badges clear
  once you've viewed a property.

## Requirements

- Python 3.11+, Node 18+ (frontend build only), SQLite (bundled with Python)
- Optional, for the AI features: an [Anthropic API key](https://platform.claude.com/)
  (~£5/mo at personal usage) **or** an [Ollama](https://ollama.com) server on your
  network (free; 14B+ model recommended for the chat agent)
- Optional: Telegram bot token (free, via @BotFather) and/or SMTP credentials for alerts

All keys and tokens are entered on the in-app **Settings** page (admin only), with
setup walkthroughs and test buttons per service — editing `.env` by hand is only needed
for the initial password/secret.

## Quick start (development, any OS)

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt          # Windows: .venv\Scripts\pip
cp .env.example .env                                        # set HS_PASSWORD + HS_SESSION_SECRET
cd frontend && npm install && npm run build && cd ..
.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8410
```

Open http://localhost:8410, log in, and create a profile in **Search Profiles** (or via
the AI agent once a provider is configured). Hit **Status → Scan now** for the first
scrape.

For frontend development: `cd frontend && npm run dev` (proxies /api to :8410).

## Raspberry Pi install (production)

```bash
git clone <this repo> && cd HouseSpotter
sudo bash deploy/install-pi.sh
sudo nano /opt/housespotter/.env      # set password + session secret
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
detections (a blocked portal pauses itself for 6h and warns all admins on Telegram).

### Updating

```bash
cd ~/HouseSpotter && git pull
sudo bash deploy/install-pi.sh        # re-syncs code, keeps .env and data
sudo systemctl restart housespotter
```

## Configuration

Almost everything lives on the in-app **Settings** page (admin): AI provider
(Anthropic key / Ollama URL + model / none), Telegram bot, SMTP server, routing key,
scanning toggles and user management. Each user sets their own alert targets there too.
Settings are persisted to `.env` and applied live.

Bootstrap-only `.env` values:

| Variable | Purpose |
|---|---|
| `HS_USERNAME` / `HS_PASSWORD` | First admin login (created on first start; DB is authoritative after) |
| `HS_SESSION_SECRET` | Cookie signing — generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

## Architecture

```
backend/app/
  scraping/    portal adapters (rightmove, onthemarket, purplebricks, zoopla) + normalizer + scheduler jobs
  scoring/     hard filters + validated criteria checks + AI desire scoring (cached)
  research/    geocoding, free UK data sources, area ranking, travel times, train stations
  llm/         provider-agnostic AI client (Anthropic / Ollama) with DB response cache + intake chat
  notify/      Telegram/email channels + per-user alert routing + dedupe ledger
  api/         REST routes    models.py  SQLite schema (SQLModel)
frontend/      React + TypeScript + Tailwind + Leaflet (built to static, served by FastAPI)
deploy/        systemd unit, Pi installer, backup script
```

Data lives in a single SQLite file (`data/housespotter.db`, WAL mode). Scoring results
and all AI responses are cached in the database (scoped by provider and model), so
nothing is ever paid for twice.
