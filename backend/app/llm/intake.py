"""Conversational intake: an Opus-driven estate-agent chat that builds/edits a SearchProfile
via tool use. Locations are geocoded server-side; criteria edits bump criteria_version
and trigger a rescore."""
import logging

from fastapi import HTTPException
from sqlmodel import Session, select

from ..config import settings
from ..models import ChatMessage, SearchProfile, User, utcnow
from .client import get_client

log = logging.getLogger("housespotter.intake")

SYSTEM = """You are the resident property expert inside HouseSpotter, a personal UK \
property-search app. You act like a knowledgeable, friendly estate agent helping the user \
define (and later refine) what they're looking for.

Style: warm, concise, one focused question at a time. Never ask about more than two things \
in one message. Use plain language, no bullet-point interrogations.

Gather, over a few short turns: buy or rent; budget (max, and min if relevant); areas they \
like (and why — commute? family? vibe?); bedrooms; property type preferences; must-haves \
vs nice-to-haves (parking, garden, chain-free, en-suite...); any free-text desires \
("period features", "light and airy", "quiet street"); how picky alerts should be.

As soon as you have mode + budget + at least one area + bedrooms, call save_search_profile \
with everything you know — don't wait for perfection; you can update it again any time the \
user adds or changes something. After saving, briefly confirm what you've set up and invite \
corrections.

The user's existing search profiles (if any) are listed at the end of this prompt. Two \
distinct operations — choose deliberately every time you call the tool:

1. CHANGING an existing search ("add semi-detached to my Cambridge search", "raise the \
budget"): pass that profile's profile_id with the FULL updated criteria (your call replaces \
the old criteria entirely, so carry over everything that isn't changing).
2. A SEPARATE search ("do a separate/another/new search for X", "also watch Y as well"): \
omit profile_id — this creates a brand-new profile and must NEVER touch an existing one. \
Do not reuse an existing profile's name for it.

If it's ambiguous whether the user wants to modify or add, ask before calling the tool.

Structured must_have/nice_to_have keys you may use: parking, garden, garage, chain_free, \
new_build, ensuite, epc_c, value, extra_beds, milestone_access (how quickly the property \
reaches the user's saved Milestone places — use when they say being near their favourite \
places matters). Anything else the user wants goes into nice_to_haves as kind="desire" \
free text. Weights: 1 = mild preference, 2 = matters, \
3 = really matters. Radius defaults: town 5 km, village 3 km, city area 8 km."""

PROFILE_TOOL = {
    "name": "save_search_profile",
    "description": (
        "Create or fully update the user's property search profile. Call this whenever you "
        "have enough information, and again whenever criteria change. Provide the complete "
        "current criteria each time — this replaces the previous version."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "profile_id": {
                "type": ["integer", "null"],
                "description": "ID of the existing profile to update. Omit/null to create a new profile.",
            },
            "name": {"type": "string", "description": "Short label, e.g. 'Family home near Guildford'"},
            "mode": {"type": "string", "enum": ["buy", "rent"]},
            "min_price": {"type": ["integer", "null"]},
            "max_price": {"type": ["integer", "null"], "description": "Sale price or monthly rent"},
            "min_beds": {"type": ["integer", "null"]},
            "max_beds": {"type": ["integer", "null"]},
            "min_baths": {"type": ["integer", "null"]},
            "property_types": {
                "type": "array",
                "items": {"type": "string", "enum": ["detached", "semi-detached", "terraced", "flat", "bungalow", "land", "park-home"]},
            },
            "tenures": {"type": "array", "items": {"type": "string", "enum": ["freehold", "leasehold", "share-of-freehold"]}},
            "locations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "UK place name, e.g. 'Guildford'"},
                        "radius_km": {"type": "number"},
                    },
                    "required": ["label"],
                    "additionalProperties": False,
                },
            },
            "must_haves": {
                "type": "object",
                "description": "Hard requirements; keys from the structured list, values true",
                "additionalProperties": {"type": "boolean"},
            },
            "nice_to_haves": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Structured key, for kind=structured"},
                        "text": {"type": "string", "description": "Free text, for kind=desire"},
                        "kind": {"type": "string", "enum": ["structured", "desire"]},
                        "weight": {"type": "integer"},
                    },
                    "required": ["kind", "weight"],
                    "additionalProperties": False,
                },
            },
            "commutes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "max_minutes": {"type": "integer"},
                        "mode": {"type": "string", "enum": ["driving", "transit", "cycling", "walking"]},
                    },
                    "required": ["label", "max_minutes"],
                    "additionalProperties": False,
                },
            },
            "qol_weights": {
                "type": "object",
                "description": "0-3 importance: transport, safety, amenities, green, schools, quiet",
                "additionalProperties": {"type": "integer"},
            },
            "brief": {"type": "string", "description": "One-paragraph summary of what the user wants, in their words"},
            "alert_threshold": {"type": "integer", "description": "Minimum match score (0-100) to alert on, default 70"},
            "exclusions": {
                "type": "array",
                "items": {"type": "string", "enum": ["retirement", "shared_ownership", "auction", "park_home"]},
                "description": "Listing kinds to exclude entirely",
            },
            "min_floor_area": {"type": ["integer", "null"], "description": "Minimum floor area in square metres"},
        },
        "required": ["name", "mode", "brief"],
        "additionalProperties": False,
    },
}

PROFILE_FIELDS = {
    "name", "mode", "min_price", "max_price", "min_beds", "max_beds", "min_baths",
    "property_types", "tenures", "must_haves", "nice_to_haves", "exclusions",
    "min_floor_area", "commutes", "qol_weights", "brief", "alert_threshold",
}


def _apply_profile(session: Session, session_id: str, data: dict, user: User) -> tuple[SearchProfile, bool]:
    """Create or update the profile linked to this chat session. Returns (profile, created)."""
    from ..research.geo import geocode_place

    locations = []
    for loc in data.get("locations") or []:
        coords = geocode_place(loc["label"])
        locations.append({
            "label": loc["label"],
            "lat": coords[0] if coords else None,
            "lng": coords[1] if coords else None,
            "radius_km": loc.get("radius_km") or 5,
        })

    # profile_id present → update that profile; absent → ALWAYS create new.
    # (No session-based fallback: a wrongly-omitted id must never overwrite data —
    # the worst case is a duplicate profile, which is trivially deletable.)
    profile = None
    if data.get("profile_id"):
        profile = session.get(SearchProfile, data["profile_id"])
        if profile and profile.user_id != user.id:
            profile = None  # never touch another user's profile
    created = profile is None
    if profile is None:
        profile = SearchProfile(name=data["name"], mode=data["mode"], user_id=user.id)

    for field in PROFILE_FIELDS:
        if field in data and data[field] is not None:
            setattr(profile, field, data[field])
    profile.locations = locations
    if not created:
        profile.criteria_version += 1
    profile.updated_at = utcnow()
    session.add(profile)
    session.commit()
    session.refresh(profile)

    from ..history import snapshot_profile

    snapshot_profile(session, profile, source="chat")
    return profile, created


def _ollama_turn(system: str, api_messages: list[dict], session: Session, session_id: str, user: User):
    """Tool-use loop against a local Ollama model. Returns (reply, profile_saved)."""
    import json as _json

    from .client import ollama_chat

    tools = [{
        "type": "function",
        "function": {
            "name": PROFILE_TOOL["name"],
            "description": PROFILE_TOOL["description"],
            "parameters": PROFILE_TOOL["input_schema"],
        },
    }]
    messages = [{"role": "system", "content": system}] + api_messages
    profile_saved = None
    reply = ""
    for _ in range(3):
        raw = ollama_chat(messages, tools=tools, max_tokens=1500)
        if raw is None:
            raise HTTPException(502, "The local AI server didn't respond — check the Ollama settings.")
        msg = raw.get("message") or {}
        reply = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            break
        messages.append(msg)
        for call in tool_calls:
            args = (call.get("function") or {}).get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except _json.JSONDecodeError:
                    args = {}
            try:
                profile_saved, created = _apply_profile(session, session_id, args, user)
                result = (
                    f"Profile {'created' if created else 'updated'} (id={profile_saved.id}, "
                    f"criteria v{profile_saved.criteria_version}). Scoring has started."
                )
            except Exception as exc:
                log.exception("Failed to apply profile from ollama tool call")
                result = f"Error saving profile: {exc}"
            messages.append({"role": "tool", "content": result})
    return reply, profile_saved


def intake_turn(session: Session, session_id: str, text: str, user: User) -> dict:
    from .client import llm_available

    if not llm_available():
        raise HTTPException(
            503,
            "AI agent not configured — set up an AI provider in Settings. "
            "You can still create and edit profiles manually in Search Profiles.",
        )
    client = get_client() if settings.ai_provider == "anthropic" else None

    import json

    existing = session.exec(select(SearchProfile).where(SearchProfile.user_id == user.id)).all()
    profiles_context = json.dumps([
        {
            "profile_id": p.id, "name": p.name, "mode": p.mode, "active": p.active,
            "min_price": p.min_price, "max_price": p.max_price,
            "min_beds": p.min_beds, "max_beds": p.max_beds, "min_baths": p.min_baths,
            "property_types": p.property_types, "tenures": p.tenures,
            "locations": [{"label": l.get("label"), "radius_km": l.get("radius_km")} for l in (p.locations or [])],
            "must_haves": p.must_haves, "nice_to_haves": p.nice_to_haves,
            "commutes": p.commutes, "qol_weights": p.qol_weights,
            "alert_threshold": p.alert_threshold, "brief": p.brief,
        }
        for p in existing
    ], ensure_ascii=False)
    system = SYSTEM + "\n\nCurrent search profiles:\n" + (profiles_context or "[]")

    history = session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.user_id == user.id)
        .order_by(ChatMessage.id)
    ).all()
    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": text})

    session.add(ChatMessage(session_id=session_id, role="user", content=text, user_id=user.id))
    session.commit()

    profile_saved = None
    api_messages = list(messages)
    if settings.ai_provider == "ollama":
        reply, profile_saved = _ollama_turn(system, api_messages, session, session_id, user)
    else:
        for _ in range(3):  # allow tool call + follow-up
            response = client.messages.create(
                model=settings.model_intake,
                max_tokens=1500,
                system=system,
                messages=api_messages,
                tools=[PROFILE_TOOL],
            )
            from .client import record_usage

            record_usage(settings.model_intake, response.usage)
            if response.stop_reason != "tool_use":
                break
            api_messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        profile_saved, created = _apply_profile(session, session_id, block.input, user)
                        result = (
                            f"Profile {'created' if created else 'updated'} (id={profile_saved.id}, "
                            f"criteria v{profile_saved.criteria_version}). Scoring has started."
                        )
                    except Exception as exc:
                        log.exception("Failed to apply profile from intake tool call")
                        result = f"Error saving profile: {exc}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            api_messages.append({"role": "user", "content": tool_results})

        reply = next((b.text for b in response.content if b.type == "text"), "")
    if not reply:
        reply = "Done — your search is set up. Anything you'd like to adjust?"

    assistant_msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=reply,
        user_id=user.id,
        profile_id=profile_saved.id if profile_saved else (
            history[-1].profile_id if history and history[-1].profile_id else None
        ),
    )
    session.add(assistant_msg)
    session.commit()
    session.refresh(assistant_msg)

    if profile_saved:
        from ..research.engine import ensure_profile_search
        from ..scoring.engine import rescore_profile_async

        rescore_profile_async(profile_saved.id)
        ensure_profile_search(profile_saved.id)  # keep the pinned area search in sync

    return {"id": assistant_msg.id, "role": "assistant", "content": reply}
