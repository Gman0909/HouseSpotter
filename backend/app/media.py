"""Portal CDN URL helpers. Galleries store original-resolution URLs; cards and
alert messages want small variants, which the big portals' CDNs derive from the
same path."""
import re

_RM_ORIGINAL_RE = re.compile(
    r"^https://media\.rightmove\.co\.uk/([^?]+?)(\.\w+)$"
)
_OTM_SIZE_RE = re.compile(r"-(?:\d{3,4}x\d{3,4}|original)(\.\w+)$")


def thumb_url(url: str | None) -> str | None:
    """Best-effort small variant of a gallery image URL (falls back to the original)."""
    if not url:
        return url
    if url.startswith("https://media.rightmove.co.uk/") and "/dir/" not in url:
        m = _RM_ORIGINAL_RE.match(url)
        if m:
            return f"https://media.rightmove.co.uk/dir/{m.group(1)}_max_656x437{m.group(2)}"
    if url.startswith("https://media.onthemarket.com/"):
        return _OTM_SIZE_RE.sub(r"-480x320\1", url)
    return url
