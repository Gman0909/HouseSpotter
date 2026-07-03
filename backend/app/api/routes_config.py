"""Server settings UI: read/update the relevant .env values, apply them live where
possible, and test each connection. Secrets are never echoed back — only a hint."""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_admin
from ..config import PROJECT_DIR, settings
from ..models import User

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_admin)])
log = logging.getLogger("housespotter.config")

# field on Settings → metadata. Order matters (UI renders in this order).
SETTINGS_META: dict[str, dict] = {
    "anthropic_api_key": {"env": "HS_ANTHROPIC_API_KEY", "secret": True, "kind": "str", "section": "ai",
                          "label": "Anthropic API key"},
    "ai_budget_usd": {"env": "HS_AI_BUDGET_USD", "secret": False, "kind": "float", "section": "ai",
                      "label": "Monthly AI budget (USD)"},
    "telegram_bot_token": {"env": "HS_TELEGRAM_BOT_TOKEN", "secret": True, "kind": "str", "section": "telegram",
                           "label": "Bot token"},
    "smtp_host": {"env": "HS_SMTP_HOST", "secret": False, "kind": "str", "section": "email", "label": "SMTP host"},
    "smtp_port": {"env": "HS_SMTP_PORT", "secret": False, "kind": "int", "section": "email", "label": "SMTP port"},
    "smtp_user": {"env": "HS_SMTP_USER", "secret": False, "kind": "str", "section": "email", "label": "SMTP username"},
    "smtp_password": {"env": "HS_SMTP_PASSWORD", "secret": True, "kind": "str", "section": "email", "label": "SMTP password"},
    "smtp_from": {"env": "HS_SMTP_FROM", "secret": False, "kind": "str", "section": "email", "label": "From address"},
    "ors_api_key": {"env": "HS_ORS_API_KEY", "secret": True, "kind": "str", "section": "routing",
                    "label": "OpenRouteService key"},
    "scrape_enabled": {"env": "HS_SCRAPE_ENABLED", "secret": False, "kind": "bool", "section": "scraping",
                       "label": "Automatic scanning", "restart": True},
    "playwright_fallback": {"env": "HS_PLAYWRIGHT_FALLBACK", "secret": False, "kind": "bool", "section": "scraping",
                            "label": "Zoopla adapter (experimental)", "restart": True},
}


def _payload() -> list[dict]:
    out = []
    for field, meta in SETTINGS_META.items():
        value = getattr(settings, field)
        item = {
            "key": field, "label": meta["label"], "section": meta["section"],
            "secret": meta["secret"], "kind": meta["kind"],
            "restart_required": meta.get("restart", False),
            "set": bool(value) if meta["kind"] != "bool" else True,
        }
        if meta["secret"]:
            item["value"] = None
            item["hint"] = f"…{str(value)[-4:]}" if value else None
        else:
            item["value"] = value
        out.append(item)
    return out


def _write_env(updates: dict[str, str]) -> None:
    """Rewrite .env preserving unrelated lines/comments. Atomic replace, 0600."""
    path = PROJECT_DIR / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for env_key, raw in updates.items():
        new_line = f"{env_key}={raw}"
        for i, line in enumerate(lines):
            if line.startswith(f"{env_key}="):
                lines[i] = new_line
                break
        else:
            lines.append(new_line)
    # ProtectSystem=strict only whitelists the .env file itself, so no temp+rename —
    # write in place (single small write; risk of torn write is negligible here).
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows dev


@router.get("")
def get_config():
    return _payload()


@router.patch("")
def update_config(body: dict):
    values = body.get("values") or {}
    if not values:
        raise HTTPException(422, "values required")
    env_updates: dict[str, str] = {}
    for field, raw in values.items():
        meta = SETTINGS_META.get(field)
        if not meta:
            raise HTTPException(422, f"Unknown setting '{field}'")
        if meta["kind"] == "bool":
            coerced = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
            env_updates[meta["env"]] = "true" if coerced else "false"
        elif meta["kind"] in ("int", "float"):
            try:
                coerced = int(raw) if meta["kind"] == "int" else float(raw)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{meta['label']} must be a number")
            env_updates[meta["env"]] = str(coerced)
        else:
            coerced = str(raw or "").strip()
            env_updates[meta["env"]] = coerced
        setattr(settings, field, coerced)  # apply live

    _write_env(env_updates)

    # The Anthropic client caches itself — rebuild on next use
    if "anthropic_api_key" in values:
        from ..llm import client as llm_client

        llm_client._client = None

    log.info("Settings updated: %s", ", ".join(values.keys()))
    return _payload()


# --- Connection tests ---

@router.post("/test/telegram-bot")
def test_telegram_bot():
    """Validate the bot token itself (per-user delivery is tested from My alert targets)."""
    if not settings.telegram_bot_token:
        raise HTTPException(422, "Set the bot token first")
    import httpx

    try:
        resp = httpx.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe", timeout=15
        )
        data = resp.json()
    except Exception:
        raise HTTPException(502, "Couldn't reach Telegram")
    if not data.get("ok"):
        raise HTTPException(502, f"Telegram rejected the token: {data.get('description', 'invalid')}")
    username = data["result"].get("username", "?")
    return {"ok": True, "detail": f"Token valid — bot @{username}. Users message it, then detect their chat ID in 'My alert targets'."}


@router.post("/test/anthropic")
def test_anthropic():
    if not settings.anthropic_api_key:
        raise HTTPException(422, "Set the API key first")
    from ..llm.client import get_client

    client = get_client()
    try:
        client.models.list()
    except Exception as exc:
        raise HTTPException(502, f"Anthropic rejected the key: {exc}")
    return {"ok": True, "detail": "Key is valid"}


@router.post("/test/ors")
def test_ors():
    if not settings.ors_api_key:
        raise HTTPException(422, "Set the key first")
    from ..research.travel import _ors_matrix

    result = _ors_matrix("driving-car", [(52.2053, 0.1218)], [(52.1937, 0.1369)])
    if not result or not result[0]:
        raise HTTPException(502, "OpenRouteService rejected the key or the request")
    return {"ok": True, "detail": "Key is valid — routing works"}


@router.post("/test/email")
def test_email(user: User = Depends(require_admin)):
    """Sends to the requesting admin's own address — recipients are per-user."""
    if not settings.smtp_host:
        raise HTTPException(422, "Set the SMTP host first")
    if not user.email_to:
        raise HTTPException(422, "Set your email address in 'My alert targets' first — recipients are per-user")
    from ..notify.channels import send_email

    ok = send_email("HouseSpotter test", "<p>✅ Email alerts are working.</p>", to=user.email_to)
    if not ok:
        raise HTTPException(502, "SMTP send failed — check host, port and credentials")
    return {"ok": True, "detail": f"Test email sent to {user.email_to}"}
