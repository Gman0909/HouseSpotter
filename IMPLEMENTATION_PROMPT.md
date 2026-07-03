# Implementation Prompt — HouseSpotter

> Paste this to the LLM/agent that will build the app. It is self-contained: it restates every
> decision, the stack, the schema, milestones, and acceptance criteria. Companion design doc:
> `PLAN.md` in the same directory (read it first).

---

## Your role

You are building **HouseSpotter**, a personal, self-hosted UK property-hunting assistant for a
single technical user. It behaves like a knowledgeable estate agent: the user describes what
they want in plain English, the app continuously scrapes UK property portals for matches,
scores each against the user's criteria, researches the best neighbourhoods for the budget, and
alerts the user (Telegram + email) when new matches appear. There is a beautiful, responsive
web UI. It runs 24/7 on a Raspberry Pi (arm64) and is reachable from the user's phone.

Work **incrementally, milestone by milestone**. After each milestone, stop and demonstrate the
stated acceptance check actually passing before moving on. Prefer the **simplest code that meets
the requirement** — no speculative abstraction. State assumptions; ask if genuinely blocked.

## Non-negotiable decisions (do not re-litigate)

- **Market:** support **both buying and renting**, chosen per SearchProfile via a `mode` field.
- **Data:** **scrape Rightmove, Zoopla, OnTheMarket directly** for personal use. There is no
  public API. Implement politely and defensively (see Scraping rules). Put a personal-use /
  ToS disclaimer in the README.
- **Budget:** low. Use the **Claude API** tiered — `claude-opus-4-8` for conversational intake
  and neighbourhood-research synthesis, `claude-haiku-4-5-20251001` for high-volume per-listing
  qualitative scoring and blurbs, `claude-sonnet-5` as a mid fallback. **Cache all LLM output**
  keyed by `(listing_hash, criteria_version)`; never re-score unchanged listings. All non-LLM
  data must come from **free UK sources**.
- **Alerts:** **Telegram + email (SMTP)**, with dedupe so each match alerts once.
- **Hosting:** **Raspberry Pi arm64, NATIVE install — no Docker.** Python venv + built static
  frontend, one systemd service, binding a single dedicated port (default 8410). The Pi
  already runs Tailscale and several other services through it: **do not modify Tailscale
  config, do not run `tailscale serve`/funnel, do not touch other services or their ports** —
  remote phone access happens simply because the port is reachable over the existing tailnet.
  Single-user auth (username + password, Argon2, signed session cookie) is mandatory.
- **Neighbourhood research engine is in v1.**

## Stack (use exactly this unless you hit a hard blocker — then flag it)

- Backend: **Python 3.12 + FastAPI + Uvicorn**, SQLAlchemy/SQLModel over **SQLite** (WAL, FTS5).
- Scraping: **httpx first**, parsing the page's **embedded state JSON** (Rightmove
  `window.PAGE_MODEL`/`jsonModel`, Zoopla `__NEXT_DATA__`, etc.); **Playwright with system
  Chromium (arm64)** only as a per-portal fallback behind a flag.
- Scheduling: **APScheduler** in-process, jittered intervals, per-portal locks.
- Frontend: **React + TypeScript + Vite + Tailwind + shadcn/ui + TanStack Query + Leaflet
  (OSM tiles) + Framer Motion**. Build to static, serve from FastAPI (same origin).
- LLM: **Anthropic SDK**, structured outputs via tool-use / JSON schema.
- Notifications: **Telegram Bot API** + **SMTP**.
- Deploy: **native** — venv + systemd unit + install script; no Docker, no reverse proxy
  required (uvicorn serves API + static frontend directly on one port).

## Data model (SQLite)

Implement these tables (see `PLAN.md §5` for full field lists):
`SearchProfile`, `Property` (canonical, de-duplicated), `Listing` (per-portal, with
`price_history` and status), `MatchScore` (per property+profile+criteria_version, with a
per-criterion breakdown + LLM rationale), `Area/NeighbourhoodProfile`, `SavedList` + `ListItem`,
`Notification` (dedupe ledger), `ScrapeRun` (audit). Use migrations. A real-world property that
appears on multiple portals is **one** `Property` with multiple `Listing`s (de-dupe by address
+ postcode + key attributes).

## Scraping rules (correctness + resilience)

- Isolate each portal behind a common `PortalAdapter` interface: `search(profile) -> list[RawListing]`
  and `fetch_detail(url) -> RawListing`. One broken portal must not affect others.
- Prefer the embedded-JSON path; only fall back to Playwright when necessary.
- Politeness config file (per portal): min interval, random jitter, daily cap, user-agent,
  optional proxy. Randomise timing. Cache by payload hash — never re-fetch unchanged listings.
- Detect blocks (403/429/challenge markers) → exponential backoff, pause that portal, record it
  on `ScrapeRun`, and notify the user. Never hammer.
- Normalizer converts each portal's `RawListing` into the canonical `Property`/`Listing` schema
  (currency, beds, type taxonomy, tenure, features, images, EPC, lat/long via `postcodes.io`).

## Match scoring (must be transparent)

Overall 0–100 + expandable breakdown per `PLAN.md §6`:
1. **Hard filters** (mode, price, min beds, type, tenure, geography radius/polygon) exclude or
   dim non-matches.
2. **Weighted soft score**: structured criteria (parking, garden, EPC, price headroom, ≥N beds,
   chain-free, floor area) scored deterministically 0–1; **free-text desires** scored 0–1 by
   Haiku against description+features with a one-line reason. `score = 100·Σ(wᵢ·satᵢ)/Σwᵢ`.
3. Store a short natural-language explanation. Re-score only when `criteria_version` bumps or a
   listing changes.

## Neighbourhood research engine (v1)

Per `PLAN.md §7`. Free sources: `postcodes.io`, OSM Overpass (amenities), OpenRouteService
(commute isochrones/travel time; TfL API for London), police.uk (crime), gov.uk school
performance, Open Data Communities EPC, HM Land Registry price paid, Environment Agency
(flood), Ofcom (broadband), ONS Census. Pipeline: expand target locations into candidate areas
within commute range → pull + normalise metrics → blend with the user's QoL weight sliders →
Area Score → cross-reference in-budget matching listings → **Opus** writes a readable profile
and ranked shortlist (including areas not previously considered). Cache; refresh weekly.

## Conversational intake

Opus-driven guided chat that asks a short set of natural questions and, via structured
tool-use, produces a reviewable/editable `SearchProfile`. Re-openable to adjust criteria;
edits bump `criteria_version` and trigger re-scoring.

## Notifications

New match crossing the profile's alert threshold, not already in the `Notification` ledger →
send Telegram (photo + score + key facts + deep link) and/or email (instant or daily digest).
Support per-profile threshold, quiet hours, digest vs instant, and "price drop on saved
property" mode.

## UI

Per `PLAN.md §10`: onboarding chat → criteria review; results card grid (hero photo, score
ring, price, beds/baths, badges, new/price-drop flags, sort + filter, map toggle with
score-coloured pins); property detail (gallery, floorplan, full score breakdown, neighbourhood
snapshot, price history, add-to-list, open-on-portal); custom lists with notes/status; areas
page; settings (profiles, alerts, cadence, quiet hours). Dark/light, mobile-first, tasteful
motion. Prioritise clarity and beauty — this is used mostly on a phone.

## Build order — deliver and verify one milestone at a time

- **M0 Scaffold** — native run (venv + uvicorn) serves FastAPI + React shell with login;
  migrations run. *Verify:* login works, empty app loads.
- **M1 First listings** — Rightmove adapter (embedded JSON) + normalizer + storage. *Verify:*
  one poll of a hard-coded search shows real, de-duplicated properties in a bare list.
- **M2 Scrape loop** — scheduler+jitter, caching, block detection/backoff, add Zoopla +
  OnTheMarket. *Verify:* 24h unattended run refreshes without dupes, survives a simulated 403
  (recorded + alerted), modest Pi CPU.
- **M3 Criteria + scoring** — SearchProfile, hard filters, weighted structured score, Haiku
  free-text scoring w/ caching, breakdown API. *Verify:* two contrasting profiles rank the same
  data differently and explainably.
- **M4 Intake chat** — Opus intake → structured editable profile. *Verify:* a plain-English
  description round-trips into a correct profile and re-scores.
- **M5 Enrichment + research** — geo/QoL enrichment + neighbourhood engine. *Verify:* for a
  target town, a ranked sourced area shortlist tied to real in-budget listings.
- **M6 Alerts + lists** — Notification ledger, Telegram + email, thresholds/quiet-hours/digest,
  custom lists. *Verify:* a new matching property triggers exactly one Telegram + one email;
  re-runs don't re-alert.
- **M7 Polish + deploy** — full UI pass, native Pi install (venv + systemd + install script),
  nightly SQLite backup, health checks, README that provisions a fresh Pi without touching
  Tailscale or existing services. *Verify:* usable end-to-end from phone over the tailnet.

## Operational requirements

`.env` for all secrets (Claude key, Telegram token, SMTP, session secret) — never committed.
Structured logs, `/health`, a Scrape Runs page. Nightly SQLite backup with WAL checkpoint +
JSON export/import of profiles and lists. README covers Pi provisioning, Tailscale setup, and
the personal-use/ToS disclaimer.

## Ground rules

- Keep it simple and surgical; every line traces to a requirement above.
- Match existing project style once it exists.
- Don't invent features not listed here without asking.
- Verify each milestone's acceptance check for real before claiming it's done; for UI work,
  drive it in a real browser and report concrete before/after observations.
- Use the exact Claude model IDs given above. Cache aggressively to stay in budget.
- When something is genuinely ambiguous or blocked, stop and ask rather than guessing.
