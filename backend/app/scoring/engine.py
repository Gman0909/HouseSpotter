"""Match scoring: hard filters + weighted soft score with transparent breakdown.

Structured criteria are scored deterministically (free). Free-text desires are scored
0-1 by Haiku against the listing text, cached by (listing content, desires, model) so
nothing is ever scored twice.
"""
import json
import logging
import math
import threading

from sqlmodel import Session, select

from ..config import settings
from ..db import engine, session_scope
from ..llm.client import cached_json_call, llm_available, make_cache_key
from ..models import Listing, MatchScore, Property, SearchProfile, utcnow

log = logging.getLogger("housespotter.scoring")


# --- Geometry ---

def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --- Structured criteria checkers: (property, listing, profile) -> (satisfaction 0-1, reason) ---

def _text_blob(prop: Property) -> str:
    return " ".join([prop.description or "", " ".join(prop.features or [])]).lower()

def _keyword_check(keywords: list[str], exclude: list[str] | None = None):
    def check(prop: Property, listing: Listing, profile: SearchProfile):
        blob = _text_blob(prop)
        for kw in exclude or []:
            blob = blob.replace(kw, "")
        hit = next((kw for kw in keywords if kw in blob), None)
        if hit:
            return 1.0, f"mentions {hit}"
        return 0.0, "not mentioned"
    return check

def _epc_check(min_grade: str):
    def check(prop: Property, listing: Listing, profile: SearchProfile):
        if not prop.epc:
            return 0.5, "EPC unknown"
        ok = prop.epc.upper() <= min_grade.upper()  # 'A' < 'B' < 'C'
        return (1.0, f"EPC {prop.epc}") if ok else (0.0, f"EPC {prop.epc}")
    return check

def _value_check(prop: Property, listing: Listing, profile: SearchProfile):
    if not profile.max_price or not listing.price:
        return 0.5, "no budget set"
    headroom = 1 - listing.price / profile.max_price
    sat = max(0.0, min(1.0, headroom * 2.5))  # 40%+ under budget → 1.0
    return sat, f"{round(headroom * 100)}% under budget" if headroom > 0 else "at budget limit"

def _beds_bonus_check(prop: Property, listing: Listing, profile: SearchProfile):
    if prop.beds is None or profile.min_beds is None:
        return 0.5, "unknown"
    extra = prop.beds - profile.min_beds
    return (min(1.0, 0.5 + extra * 0.5), f"{prop.beds} beds")


def _milestone_access_check(prop: Property, listing: Listing, profile: SearchProfile):
    from ..research.travel import access_score_single

    score, avg_car = access_score_single(prop.id)
    if score is None:
        return 0.5, "no milestones set"
    return score / 100, f"~{round(avg_car)} min drive to your places"

STRUCTURED_CHECKS = {
    "parking": (_keyword_check(["parking", "garage", "driveway", "off street", "off-street", "off road", "off-road", "carport"]), "Parking"),
    "garden": (_keyword_check(["garden", "lawn", "patio"], exclude=["garden room", "winter garden"]), "Garden"),
    "chain_free": (_keyword_check(["chain free", "chain-free", "no onward chain", "no chain", "vacant possession"]), "Chain-free"),
    "new_build": (_keyword_check(["new build", "new-build", "new home", "newly built"]), "New build"),
    "garage": (_keyword_check(["garage"]), "Garage"),
    "ensuite": (_keyword_check(["ensuite", "en-suite", "en suite"]), "En-suite"),
    "epc_c": (_epc_check("C"), "EPC C or better"),
    "value": (_value_check, "Price value"),
    "extra_beds": (_beds_bonus_check, "Extra bedrooms"),
    "milestone_access": (_milestone_access_check, "Milestone access"),
}


# --- Hard filters ---

def passes_hard_filters(prop: Property, listing: Listing, profile: SearchProfile) -> tuple[bool, str]:
    if listing.mode != profile.mode:
        return False, "wrong mode"
    if listing.status in ("removed",):
        return False, "removed"
    if profile.min_price and listing.price and listing.price < profile.min_price:
        return False, "below min price"
    if profile.max_price and listing.price and listing.price > profile.max_price:
        return False, "above max price"
    if profile.min_beds and (prop.beds or 0) < profile.min_beds:
        return False, "too few bedrooms"
    if profile.max_beds and prop.beds and prop.beds > profile.max_beds:
        return False, "too many bedrooms"
    if profile.min_baths and (prop.baths or 0) < profile.min_baths:
        return False, "too few bathrooms"
    if profile.property_types and prop.property_type:
        if prop.property_type not in profile.property_types and not (
            prop.property_type == "house" and any(
                t in ("detached", "semi-detached", "terraced") for t in profile.property_types
            )
        ):
            return False, f"type {prop.property_type}"
    if profile.tenures and prop.tenure and prop.tenure not in profile.tenures:
        return False, f"tenure {prop.tenure}"
    if profile.locations and prop.lat is not None and prop.lng is not None:
        inside = any(
            _haversine_km(prop.lat, prop.lng, loc["lat"], loc["lng"]) <= (loc.get("radius_km") or 8) * 1.2
            for loc in profile.locations
            if loc.get("lat") is not None
        )
        if not inside:
            return False, "outside target area"
    # Structured must-haves (boolean keys checked against listing text)
    for key, required in (profile.must_haves or {}).items():
        if required and key in STRUCTURED_CHECKS:
            check, label = STRUCTURED_CHECKS[key]
            sat, _ = check(prop, listing, profile)
            if sat < 0.5:
                return False, f"missing {label.lower()}"
    return True, ""


# --- Desire scoring (Haiku) ---

DESIRE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "desire": {"type": "string"},
                    "satisfaction": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["desire", "satisfaction", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}

DESIRE_SYSTEM = (
    "You score how well a UK property listing satisfies a buyer's stated desires. "
    "For each desire, return satisfaction from 0.0 (definitely not satisfied) to 1.0 "
    "(clearly satisfied), using 0.5 when the listing gives no evidence either way. "
    "Base your judgment only on the listing text provided. Keep each reason under 12 words."
)


def score_desires(prop: Property, listing: Listing, desires: list[str]) -> dict[str, tuple[float, str]]:
    """Returns {desire_text: (satisfaction, reason)}. Neutral 0.5 if LLM unavailable."""
    if not desires:
        return {}
    if not llm_available():
        return {d: (0.5, "AI scoring not configured") for d in desires}

    listing_text = json.dumps({
        "address": prop.address,
        "type": prop.property_type,
        "beds": prop.beds,
        "baths": prop.baths,
        "tenure": prop.tenure,
        "epc": prop.epc,
        "floor_area_sqm": prop.floor_area_sqm,
        "features": prop.features,
        "description": prop.description[:2500],
    }, ensure_ascii=False)

    cache_key = make_cache_key("desires", listing.payload_hash, json.dumps(sorted(desires)), settings.model_scoring)
    result = cached_json_call(
        cache_key,
        model=settings.model_scoring,
        system=DESIRE_SYSTEM,
        user_content=f"Listing:\n{listing_text}\n\nDesires to score:\n" + "\n".join(f"- {d}" for d in desires),
        schema=DESIRE_SCHEMA,
        max_tokens=1500,
    )
    if not result:
        return {d: (0.5, "AI scoring unavailable") for d in desires}
    out: dict[str, tuple[float, str]] = {}
    scored = {s["desire"]: s for s in result.get("scores", [])}
    for d in desires:
        s = scored.get(d)
        if s:
            out[d] = (max(0.0, min(1.0, float(s["satisfaction"]))), s["reason"])
        else:
            out[d] = (0.5, "not scored")
    return out


# --- Main scoring ---

def _soft_criteria(profile: SearchProfile) -> list[dict]:
    criteria = [dict(c) for c in (profile.nice_to_haves or [])]
    if not any(c.get("key") == "value" for c in criteria):
        criteria.append({"key": "value", "kind": "structured", "weight": 1})
    return criteria


def compute_match(prop: Property, listing: Listing, profile: SearchProfile) -> MatchScore:
    passed, fail_reason = passes_hard_filters(prop, listing, profile)
    breakdown: list[dict] = []
    score = 0.0

    if passed:
        criteria = _soft_criteria(profile)
        desires = [c["text"] for c in criteria if c.get("kind") == "desire" and c.get("text")]
        desire_scores = score_desires(prop, listing, desires)

        total_w = 0.0
        acc = 0.0
        for c in criteria:
            weight = float(c.get("weight") or 1)
            if c.get("kind") == "desire":
                sat, reason = desire_scores.get(c["text"], (0.5, "not scored"))
                label = c["text"]
            else:
                entry = STRUCTURED_CHECKS.get(c.get("key", ""))
                if not entry:
                    continue
                check, label = entry
                sat, reason = check(prop, listing, profile)
            breakdown.append({
                "label": label, "kind": c.get("kind", "structured"),
                "weight": weight, "satisfaction": round(sat, 2), "reason": reason,
            })
            total_w += weight
            acc += weight * sat
        score = round(100 * acc / total_w, 1) if total_w else 100.0
    else:
        breakdown.append({
            "label": "Requirements", "kind": "filter", "weight": 0,
            "satisfaction": 0.0, "reason": fail_reason,
        })

    rationale = _build_rationale(score, passed, fail_reason, breakdown)
    return MatchScore(
        property_id=prop.id,
        profile_id=profile.id,
        criteria_version=profile.criteria_version,
        passed_filters=passed,
        score=score,
        breakdown=breakdown,
        rationale=rationale,
    )


def _build_rationale(score: float, passed: bool, fail_reason: str, breakdown: list[dict]) -> str:
    if not passed:
        return f"Doesn't meet your requirements: {fail_reason}."
    strong = [b for b in breakdown if b["satisfaction"] >= 0.75 and b["kind"] != "filter"]
    weak = [b for b in breakdown if b["satisfaction"] <= 0.35 and b["kind"] != "filter"]
    parts = []
    if strong:
        parts.append("Strong on " + ", ".join(b["label"].lower() for b in strong[:4]))
    if weak:
        parts.append("falls short on " + ", ".join(b["label"].lower() for b in weak[:4]))
    if not parts:
        return "A reasonable all-round match for your criteria."
    return "; ".join(parts) + "."


def score_profile(profile_id: int) -> int:
    """Score all unscored (property, profile, criteria_version) pairs. Returns count scored."""
    with session_scope() as session:
        profile = session.get(SearchProfile, profile_id)
        if not profile or not profile.active:
            return 0
        criteria_version = profile.criteria_version
        existing = {
            m.property_id
            for m in session.exec(
                select(MatchScore).where(
                    MatchScore.profile_id == profile_id,
                    MatchScore.criteria_version == profile.criteria_version,
                )
            ).all()
        }
        listings = session.exec(
            select(Listing).where(Listing.mode == profile.mode, Listing.status != "removed")
        ).all()
        todo = []
        seen_props = set()
        for listing in listings:
            if listing.property_id in existing or listing.property_id in seen_props:
                continue
            seen_props.add(listing.property_id)
            todo.append((listing.property_id, listing.id))

    count = 0
    for property_id, listing_id in todo:
        try:
            with session_scope() as session:
                profile = session.get(SearchProfile, profile_id)
                prop = session.get(Property, property_id)
                listing = session.get(Listing, listing_id)
                if not (profile and prop and listing):
                    continue
                match = compute_match(prop, listing, profile)
                session.add(match)
                session.commit()
                count += 1
        except Exception:
            log.exception("Scoring failed for property %s", property_id)
    if count:
        log.info("Scored %d properties for profile %s (v%s)", count, profile_id, criteria_version)
    return count


def rescore_profile_async(profile_id: int) -> None:
    threading.Thread(target=score_profile, args=(profile_id,), daemon=True).start()
