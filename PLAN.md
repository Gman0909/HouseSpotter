# HouseSpotter — Implementation Plan

A personal, self-hosted property-hunting assistant that acts like a knowledgeable estate
agent: you describe what you want in plain English, it continuously watches UK portals for
matching homes, scores each one against your criteria, researches the best neighbourhoods
for your budget, and alerts you the moment something good appears.

---

## 1. Locked decisions

| Decision | Choice |
|---|---|
| Market | **Buy and rent** (mode toggle per search profile) |
| Data acquisition | **Direct scraping** of Rightmove, Zoopla, OnTheMarket (personal use) |
| Budget | **Low (~£5–15/mo)** — Claude API tiered; all other data from free/open UK sources |
| Alerts | **Email + Telegram** |
| Hosting | **Always-on Raspberry Pi (arm64)**, **native install** (venv + systemd, no Docker), UI reachable over the existing Tailscale tailnet on its own port — no changes to the Pi's Tailscale config or other running services |
| Neighbourhood research | **In v1** |
| Region | **UK** (postcode-based) |
| Users | **Single user** (you), password-protected |

---

## 2. Honest constraints & risks (read first)

- **No public APIs.** Rightmove, Zoopla and OnTheMarket have no open property API and their
  Terms of Service prohibit automated access. This build is for **private, personal,
  non-redistributed use** at low request volumes. It can break at any time when a site
  changes its markup or anti-bot defences, and there is a real (if low, at personal scale)
  risk of IP blocks. The plan mitigates this but cannot eliminate it.
- **Mitigations built in:** low/randomised request rates, per-portal politeness windows,
  aggressive caching (never re-fetch unchanged listings), realistic browser headers,
  exponential backoff on 403/429, block detection with automatic pause + alert, and a
  "prefer embedded JSON over full browser" strategy to minimise footprint.
- **Resilience by design:** each portal is an isolated *adapter* behind a common interface,
  so when one breaks the others keep working and only one small module needs fixing.
- **LLM cost control:** listings are scored once and cached by `(listing_hash, criteria_version)`;
  only genuinely new/changed listings ever hit the API; high-volume work uses Haiku.

---

## 3. Architecture overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  Raspberry Pi (Docker Compose, arm64)                                  │
│                                                                        │
│  ┌────────────┐   schedule   ┌──────────────┐   canonical  ┌────────┐  │
│  │ Scheduler  │─────────────▶│  Scrapers    │─────────────▶│Normaliz│  │
│  │(APScheduler)│  (jittered) │ RM / Zoopla  │   raw HTML/  │ -er    │  │
│  └────────────┘              │ / OnTheMarket│    JSON      └───┬────┘  │
│         │                    └──────────────┘                  │       │
│         │                                                      ▼       │
│         │                    ┌──────────────┐          ┌─────────────┐ │
│         └───────────────────▶│  Enrichment  │◀─────────│  Property   │ │
│                              │ geo + QoL    │  upsert  │  store      │ │
│                              │ (free UK data)│          │ (SQLite)   │ │
│                              └──────┬───────┘          └──────┬──────┘ │
│                                     │                         │        │
│                              ┌──────▼───────┐          ┌──────▼──────┐ │
│                              │  Matcher /   │          │ Neighbourhood│ │
│                              │  Scorer      │          │ Research     │ │
│                              │ (rules+Haiku)│          │ engine       │ │
│                              └──────┬───────┘          └──────┬──────┘ │
│                                     │                         │        │
│                              ┌──────▼─────────────────────────▼──────┐ │
│                              │        FastAPI  (REST + auth)         │ │
│                              └──────┬─────────────────────┬─────────┘ │
│                                     │                     │           │
│                              ┌──────▼──────┐       ┌──────▼────────┐  │
│                              │ Notifier    │       │ Web UI (React)│  │
│                              │ email+TG    │       │ served static │  │
│                              └─────────────┘       └───────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
        Remote access: Tailscale (recommended) or Cloudflare Tunnel
```

---

## 4. Technology stack

**Chosen for: runs well on a Pi (arm64), low cost, single-binary-ish simplicity, beautiful UI.**

- **Backend:** Python 3.12 + **FastAPI** (async) + Uvicorn. Python is the strongest choice
  for scraping, data wrangling, and LLM orchestration in one place.
- **Scraping:** two-tier per portal —
  1. **Primary:** `httpx` fetch + parse the **embedded state JSON** that portals ship in the
     page (e.g. Rightmove's `window.PAGE_MODEL` / `window.jsonModel`; Zoopla's `__NEXT_DATA__`).
     Fast, light, Pi-friendly, no browser.
  2. **Fallback:** **Playwright** (system Chromium, arm64) only when a page needs JS
     rendering or the light path is blocked. Kept behind a feature flag to conserve Pi CPU.
- **Storage:** **SQLite** (single-user, zero-admin, perfect on a Pi) via SQLAlchemy/SQLModel;
  FTS5 for text search; WAL mode. Images are referenced by URL and thumbnailed lazily into a
  local cache dir (not stored in the DB).
- **Scheduler:** **APScheduler** inside the app process (jittered intervals, per-portal locks).
- **Frontend:** **React + TypeScript + Vite**, **Tailwind CSS + shadcn/ui** for a polished,
  consistent component system, **Leaflet + OpenStreetMap** tiles for maps (free),
  **TanStack Query** for data, **Framer Motion** for tasteful transitions. Built to static
  assets and served by FastAPI (one origin, no CORS headaches).
- **LLM:** **Claude API** (Anthropic SDK), tiered:
  - `claude-opus-4-8` — conversational intake ("estate agent" chat) + neighbourhood research synthesis (low volume, high value).
  - `claude-sonnet-5` — mid-tier fallback / structured extraction if Opus budget is tight.
  - `claude-haiku-4-5-20251001` — per-listing qualitative match scoring + "why it matches" blurbs (high volume, cheap).
  - Structured outputs via tool-use / JSON schema for reliable criteria extraction.
  - *(Optional, deferred)* Voyage AI embeddings for semantic search if pure-rule matching proves insufficient — not needed for v1.
- **Notifications:** SMTP (email) + **Telegram Bot API** (free, instant push to phone).
- **Deploy:** **native** — Python venv + built frontend static files, run by a single
  **systemd service** on the Pi. The app binds one dedicated port (default 8410) on all
  interfaces; the Pi's **existing Tailscale** installation makes it reachable from the phone
  with **zero changes** to Tailscale config or other services already running on the Pi.
- **Auth:** single-user username + password (Argon2 hash), signed session cookie. Mandatory
  because the UI is reachable from the internet.

---

## 5. Data model (SQLite)

- **SearchProfile** — one saved hunt. `mode` (buy/rent), price range, min/max beds & baths,
  property types, tenure, radius/drawn-area polygons, list of target locations, `must_haves`
  (free text + structured), `nice_to_haves` (free text + weights), commute destinations with
  max travel time, QoL weight sliders, `criteria_version` (bumps on edit → re-score).
- **Property** — canonical de-duplicated home (address, postcode, lat/long, beds, baths,
  type, tenure, size, EPC, features[], description, image URLs[], floorplan). One row per
  real-world property even if it appears on multiple portals.
- **Listing** — a Property as seen on one portal at one time: `portal`, `portal_id`, url,
  price, status (live/STC/let-agreed/removed), `first_seen`, `last_seen`, `price_history[]`,
  raw payload hash.
- **MatchScore** — `(property_id, profile_id, criteria_version)` → overall 0–100, hard-filter
  pass/fail, per-criterion breakdown JSON, LLM rationale, computed_at.
- **Area / NeighbourhoodProfile** — geographic unit (postcode district / ward / LSOA):
  sub-scores (transport, safety, amenities, green space, schools, affordability, "vibe"),
  raw metrics, LLM narrative, refreshed_at.
- **SavedList** & **ListItem** — user's custom shortlists (e.g. "Shortlist", "Maybe",
  "Viewings booked"); a property can be in many lists, with notes and a status.
- **Notification** — dedupe ledger so each new match alerts exactly once per channel.
- **ScrapeRun** — audit of each poll: portal, counts, duration, blocked?, errors.

---

## 6. Match scoring (transparent & explainable)

Every property shows an overall **0–100 score** plus a breakdown you can expand.

1. **Hard filters (must-haves):** mode, price range, min beds, property type, tenure, and
   geography (inside a target radius/polygon). Failing a hard filter excludes the property
   (or shows it dimmed under "just outside your criteria", configurable).
2. **Weighted soft score:** each nice-to-have carries a weight. Two kinds of criteria:
   - **Structured** (parking, garden, ≥N beds, EPC ≥ C, price headroom vs budget, garden
     size, floor area, chain-free, new-build) → matched deterministically to a 0–1 satisfaction.
   - **Free-text desires** ("light and airy", "period features", "quiet street", "home
     office space") → scored 0–1 by **Haiku** against the listing's description + features,
     with a one-line justification. Cached per listing+criteria version.
   - Blend: `score = 100 × Σ(weightᵢ × satisfactionᵢ) / Σweightᵢ`.
3. **Explanation:** a short "why this scores 82 — has the garden and parking you wanted, but
   EPC is only D and it's a 12-min walk to the station" generated once and stored.

This keeps the API bill tiny (structured work is free; only fuzzy desires cost tokens) while
still feeling smart.

---

## 7. Neighbourhood research engine (v1)

**Goal:** given your target locations, budget, commute needs and QoL priorities, recommend the
best places to live — including areas you hadn't considered — and tie them to real available
listings.

**Free UK data sources (no/low cost):**

- **Postcodes → geo:** `postcodes.io` (free, unlimited-ish) and ONS boundaries.
- **Amenities** (shops, GPs, schools, parks, gyms, cafés): **OpenStreetMap Overpass API**.
- **Transport / commute:** **OpenRouteService** (free API key) for travel-time & isochrones;
  nearest-station distance from OSM; TfL Unified API for London specifics.
- **Crime / safety:** **police.uk** API (free).
- **Schools & Ofsted:** gov.uk school performance data (free bulk download).
- **EPC:** Open Data Communities EPC API (free registration).
- **Sold prices / value trends:** HM Land Registry Price Paid Data (free).
- **Flood risk:** Environment Agency API (free).
- **Broadband:** Ofcom open data.
- **Demographics / area character:** ONS Census 2021.

**Pipeline:**
1. Expand each target location into candidate areas within commute range (isochrone).
2. Pull the metrics above per candidate area; normalise to 0–1 sub-scores.
3. Blend with your QoL weight sliders → an **Area Score**.
4. Cross-reference **available listings in budget** matching your criteria → "where your money
   goes furthest for what you want."
5. **Opus** synthesises a readable profile per area ("Leafy, well-connected, family-friendly;
   12 min to the station, strong primary schools, quieter than X, and 2-bed flats here are
   ~£40k cheaper than your first-choice area") and a ranked shortlist of areas to explore.
6. Results cached and refreshed weekly (data changes slowly).

---

## 8. Conversational intake ("knowledgeable estate agent")

A guided chat (Opus) asks a short series of natural questions — budget, buy/rent, must-haves
vs nice-to-haves, where and why, commute anchors, lifestyle priorities — and, via structured
tool-use, fills in a **SearchProfile** JSON you can review and tweak with normal form controls.
You can re-open the chat any time to adjust ("actually, drop the parking requirement and push
the budget to £450k") and it edits the profile, bumps `criteria_version`, and re-scores.

---

## 9. Notifications

- **Trigger:** the poll → normalize → score pipeline finds a property whose score crosses your
  per-profile alert threshold **and** hasn't been notified before (Notification ledger dedupes).
- **Channels:** Telegram (instant, with photo + score + key facts + deep link) and/or email
  (instant or daily digest — your choice per profile).
- **Controls:** quiet hours, minimum score, "only price drops on saved properties" mode,
  digest vs instant.

---

## 10. UI (beautiful, easy, estate-agent feel)

- **Onboarding:** the chat intake, then a review screen of your criteria.
- **Results feed:** responsive card grid — hero photo, score ring, price, beds/baths, area,
  key badges (parking ✓, garden ✓, EPC C), "new" and "price ↓" flags. Sort by score / newest /
  price. Filter chips. Map view toggle (Leaflet) with score-coloured pins.
- **Property detail:** gallery, floorplan, full score breakdown with reasons, neighbourhood
  snapshot (transport, safety, amenities), price history, "add to list", "open on portal".
- **Lists:** drag-into custom shortlists, per-property notes and status.
- **Areas:** the research engine's ranked neighbourhoods with narratives and maps.
- **Settings:** profiles, alert thresholds/channels, scrape cadence, quiet hours.
- Dark/light, fully responsive (it will mostly be used on a phone), tasteful motion.

---

## 11. Build milestones (each with a concrete "done" check)

> Research engine is in v1 but the build is still staged so there's something usable early.

**M0 — Scaffold** → *verify:* a native run (venv + `uvicorn`) serves an empty FastAPI + React
shell with login; SQLite migrations run.

**M1 — First real listings** → build the Rightmove adapter (embedded-JSON path) + normalizer +
Property/Listing storage. *verify:* running one poll for a hard-coded search populates real,
de-duplicated properties visible in a bare list.

**M2 — Scrape loop & resilience** → scheduler with jitter, block detection/backoff, caching,
add Zoopla + OnTheMarket adapters. *verify:* unattended for 24h it refreshes without
duplicates, survives (and reports) a simulated 403, and CPU stays modest on the Pi.

**M3 — Criteria + scoring** → SearchProfile model, hard filters, weighted structured score,
Haiku free-text scoring with caching, score breakdown API. *verify:* two contrasting profiles
produce sensibly different, explainable rankings over the same data.

**M4 — Intake chat** → Opus conversational intake → structured profile + edit flow. *verify:*
a plain-English description round-trips into a correct, editable SearchProfile and re-scores.

**M5 — Enrichment + research engine** → geo/QoL enrichment and the neighbourhood engine with
area scores, narratives, and budget cross-reference. *verify:* for a target town it returns a
ranked, sourced area shortlist and ties each to real in-budget listings.

**M6 — Alerts + lists** → Notification ledger, Telegram + email, thresholds/quiet hours/digest,
custom lists with notes. *verify:* a newly appearing matching property triggers exactly one
Telegram + one email with correct details; re-runs don't re-alert.

**M7 — Polish + deploy** → full UI pass (cards, map, detail, areas, settings), native Pi
install (venv + systemd unit + install script), backups, health checks, docs. *verify:* usable
end-to-end from your phone over the existing tailnet; a nightly SQLite backup exists; a fresh
Pi can be provisioned from the README without touching Tailscale or other services.

---

## 12. Operational concerns

- **Backups:** nightly SQLite copy (WAL checkpoint) to a second location; export/import of
  profiles + lists as JSON.
- **Secrets:** `.env` (Claude key, Telegram token, SMTP creds, session secret) — never in git.
- **Observability:** structured logs, a `/health` endpoint, a Scrape Runs page showing last
  poll status per portal and any blocks.
- **Politeness config:** per-portal min interval, jitter, daily cap, user-agent, optional
  proxy — all in one config file so behaviour is tunable without code changes.
- **Legal note in README:** personal-use disclaimer; respect robots/ToS; do not redistribute
  scraped data.

---

## 13. Rough monthly cost

- Claude API: a few £ (Haiku scoring is cents/hundreds of listings; Opus intake/research is
  occasional). Comfortably inside £5–15 with caching.
- Everything else (maps, transit, crime, schools, EPC, prices, Telegram, Tailscale): **£0**.
- Pi electricity: negligible.
