"""Shared scraping primitives: canonical listing shape, polite HTTP client, block detection."""
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol

import httpx

log = logging.getLogger("housespotter.scraping")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


class PortalBlockedError(Exception):
    """Portal returned a block/challenge; caller should pause this portal."""


@dataclass
class NormalizedListing:
    portal: str
    portal_id: str
    url: str
    mode: str  # buy | rent
    price: Optional[int] = None  # pounds; pcm for rent
    price_qualifier: Optional[str] = None
    status: str = "live"
    address: str = ""
    postcode: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    beds: Optional[int] = None
    baths: Optional[int] = None
    property_type: Optional[str] = None
    tenure: Optional[str] = None
    floor_area_sqm: Optional[float] = None
    epc: Optional[str] = None
    features: list = field(default_factory=list)
    description: str = ""
    image_urls: list = field(default_factory=list)
    floorplan_urls: list = field(default_factory=list)
    first_listed: Optional[datetime] = None
    raw: Optional[dict] = None


class PortalAdapter(Protocol):
    portal: str

    def search(self, profile, location: dict) -> list[NormalizedListing]: ...


# --- Politeness: per-portal min interval with jitter, shared across threads ---

_last_request: dict[str, float] = {}
_lock = threading.Lock()

# seconds between requests to the same portal (min, max jitter added)
PORTAL_MIN_INTERVAL: dict[str, tuple[float, float]] = {
    "rightmove": (4.0, 4.0),
    "zoopla": (5.0, 5.0),
    "onthemarket": (5.0, 5.0),
}

BLOCK_MARKERS = (
    "captcha",
    "cf-challenge",
    "challenge-platform",
    "access denied",
    "unusual traffic",
    "are you a robot",
    "perimeterx",
    "px-captcha",
    "datadome",
)


def polite_wait(portal: str) -> None:
    base, jitter = PORTAL_MIN_INTERVAL.get(portal, (5.0, 5.0))
    with _lock:
        last = _last_request.get(portal, 0.0)
        wait = max(0.0, last + base + random.uniform(0, jitter) - time.monotonic())
        # Reserve our slot before sleeping so concurrent callers queue up.
        _last_request[portal] = time.monotonic() + wait
    if wait > 0:
        time.sleep(wait)


def fetch(portal: str, url: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
    """Polite GET with block detection. Raises PortalBlockedError on 403/429/challenge."""
    polite_wait(portal)
    merged = {**DEFAULT_HEADERS, **(headers or {})}
    with httpx.Client(headers=merged, follow_redirects=True, timeout=30) as client:
        resp = client.get(url, params=params)
    if resp.status_code in (403, 429):
        raise PortalBlockedError(f"{portal} returned HTTP {resp.status_code} for {url}")
    if resp.status_code >= 400:
        resp.raise_for_status()
    lower_head = resp.text[:4000].lower()
    if any(marker in lower_head for marker in BLOCK_MARKERS):
        raise PortalBlockedError(f"{portal} served a challenge page for {url}")
    return resp
