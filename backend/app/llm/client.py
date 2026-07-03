"""Thin Anthropic wrapper with DB-backed response caching.

All high-volume calls go through cached_json_call so a listing is never re-scored
for the same criteria version. Structured outputs (output_config.format json_schema)
guarantee parseable JSON.
"""
import hashlib
import json
import logging

from sqlmodel import Session

from ..config import settings
from ..db import engine
from ..models import LlmCache

log = logging.getLogger("housespotter.llm")

_client = None


def get_client():
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            return None
        import anthropic

        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def llm_available() -> bool:
    """True when a usable AI provider is configured (drives feature gating too)."""
    if settings.ai_provider == "anthropic":
        return bool(settings.anthropic_api_key)
    if settings.ai_provider == "ollama":
        return bool(settings.ollama_url and settings.ollama_model)
    return False


def ollama_chat(
    messages: list[dict],
    *,
    format_schema: dict | None = None,
    tools: list[dict] | None = None,
    max_tokens: int = 2000,
) -> dict | None:
    """One /api/chat call against the configured Ollama server. Returns the raw
    response dict (message, eval counts) or None on failure. Records usage at $0."""
    import httpx

    payload: dict = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if format_schema:
        payload["format"] = format_schema
    if tools:
        payload["tools"] = tools
    try:
        resp = httpx.post(
            f"{settings.ollama_url.rstrip('/')}/api/chat", json=payload, timeout=300
        )
        if resp.status_code != 200:
            log.warning("Ollama call failed: %s %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
    except Exception:
        log.exception("Ollama unreachable")
        return None

    class _Usage:
        input_tokens = data.get("prompt_eval_count", 0)
        output_tokens = data.get("eval_count", 0)

    record_usage(f"ollama:{settings.ollama_model}", _Usage())
    return data


def make_cache_key(*parts: str) -> str:
    return hashlib.sha256("||".join(parts).encode()).hexdigest()


def record_usage(model: str, usage) -> None:
    """Aggregate token spend per (month, model) — powers the budget indicator."""
    from datetime import date

    from ..models import TokenUsage

    try:
        month = date.today().strftime("%Y-%m")
        with Session(engine) as session:
            from sqlmodel import select

            row = session.exec(
                select(TokenUsage).where(TokenUsage.month == month, TokenUsage.model == model)
            ).first() or TokenUsage(month=month, model=model)
            row.input_tokens += getattr(usage, "input_tokens", 0) or 0
            row.output_tokens += getattr(usage, "output_tokens", 0) or 0
            row.calls += 1
            session.add(row)
            session.commit()
    except Exception:
        log.debug("Failed to record token usage", exc_info=True)


def cached_json_call(
    cache_key: str,
    *,
    model: str,
    system: str,
    user_content: str,
    schema: dict,
    max_tokens: int = 2000,
) -> dict | None:
    """JSON-schema-constrained call, cached forever by cache_key. None if LLM unavailable.

    The cache key is scoped by the effective provider+model, so switching between
    Anthropic and a local model never serves the other's answers."""
    if settings.ai_provider == "ollama":
        model = f"ollama:{settings.ollama_model}"
    cache_key = make_cache_key(cache_key, settings.ai_provider, model)

    with Session(engine) as session:
        from sqlmodel import select

        row = session.exec(select(LlmCache).where(LlmCache.cache_key == cache_key)).first()
        if row:
            return row.response

    if not llm_available():
        return None

    if settings.ai_provider == "ollama":
        raw = ollama_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            format_schema=schema,
            max_tokens=max_tokens,
        )
        if raw is None:
            return None
        text = (raw.get("message") or {}).get("content", "")
    else:
        client = get_client()
        if client is None:
            return None
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        record_usage(model, response.usage)
        if response.stop_reason == "refusal":
            log.warning("LLM refused request (cache_key=%s)", cache_key[:12])
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.error("LLM returned unparseable JSON despite schema (cache_key=%s)", cache_key[:12])
        return None

    from sqlalchemy.exc import IntegrityError

    try:
        with Session(engine) as session:
            session.add(LlmCache(cache_key=cache_key, model=model, response=data))
            session.commit()
    except IntegrityError:
        pass  # concurrent writer cached the same key
    return data
