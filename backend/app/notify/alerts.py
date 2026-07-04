"""Alert logic: new matches over BOTH thresholds → Telegram/email, deduped by the
Notification ledger; price-drop alerts for saved and matched properties. New-match
alerts only cover properties still 'new' (recent + unviewed by the owner) — anything
older or already seen can only come back as a clearly-labelled price drop."""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from ..db import session_scope
from ..models import (
    Listing, ListItem, MatchScore, Notification, Property, PropertyView, SavedList,
    SearchProfile, User, utcnow,
)
from .channels import send_email, send_telegram

NEW_WINDOW_HOURS = 48  # matches the frontend's "New" badge window

log = logging.getLogger("housespotter.notify")

LONDON = ZoneInfo("Europe/London")


def _in_quiet_hours(profile: SearchProfile) -> bool:
    qh = profile.quiet_hours
    if not qh or not qh.get("start") or not qh.get("end"):
        return False
    now = datetime.now(LONDON).strftime("%H:%M")
    start, end = qh["start"], qh["end"]
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # overnight window


def _already_sent(session: Session, property_id: int, profile_id: int, channel: str, kind: str) -> bool:
    return session.exec(
        select(Notification).where(
            Notification.property_id == property_id,
            Notification.profile_id == profile_id,
            Notification.channel == channel,
            Notification.kind == kind,
        )
    ).first() is not None


def _record(session: Session, property_id: int, profile_id: int, channel: str, kind: str) -> None:
    session.add(Notification(property_id=property_id, profile_id=profile_id, channel=channel, kind=kind))
    session.commit()


def _fmt_price(price: int | None, mode: str) -> str:
    if price is None:
        return "POA"
    return f"£{price:,} pcm" if mode == "rent" else f"£{price:,}"


def send_alerts_for_new_matches() -> None:
    with session_scope() as session:
        profiles = session.exec(
            select(SearchProfile).where(SearchProfile.active == True)  # noqa: E712
        ).all()
        profile_ids = [p.id for p in profiles]

    for profile_id in profile_ids:
        try:
            _alerts_for_profile(profile_id)
            _price_drops_for_profile(profile_id)
        except Exception:
            log.exception("Alerting failed for profile %s", profile_id)


def _access_typical(property_id: int, user_id: int | None) -> int | None:
    from ..research.travel import access_score_single

    access, _ = access_score_single(property_id, user_id)
    return access["typical"] if access else None


def _meets_thresholds(profile: SearchProfile, score: float, access: int | None) -> bool:
    """BOTH thresholds must pass. The access gate only applies when an access score
    exists (owner has milestones) — otherwise it is ignored, not a blocker."""
    if score < profile.alert_threshold:
        return False
    if profile.alert_min_access and access is not None and access < profile.alert_min_access:
        return False
    return True


def _is_new(listing: Listing) -> bool:
    fs = listing.first_seen
    if fs.tzinfo is None:
        fs = fs.replace(tzinfo=timezone.utc)
    return fs >= utcnow() - timedelta(hours=NEW_WINDOW_HOURS)


def _pending_matches(
    session: Session, profile: SearchProfile, channel: str
) -> list[tuple[Property, Listing, MatchScore, int | None]]:
    matches = session.exec(
        select(MatchScore).where(
            MatchScore.profile_id == profile.id,
            MatchScore.criteria_version == profile.criteria_version,
            MatchScore.passed_filters == True,  # noqa: E712
            MatchScore.score >= profile.alert_threshold,
        )
    ).all()
    viewed_ids = {
        v.property_id
        for v in session.exec(
            select(PropertyView).where(PropertyView.user_id == profile.user_id)
        ).all()
    }
    out = []
    for match in matches:
        if _already_sent(session, match.property_id, profile.id, channel, "new_match"):
            continue
        # Only genuinely NEW properties get new-match alerts: recent first-seen and
        # not yet viewed by the owner. Older/seen stock can only alert on price drops.
        if match.property_id in viewed_ids:
            continue
        prop = session.get(Property, match.property_id)
        listing = session.exec(
            select(Listing).where(
                Listing.property_id == match.property_id,
                Listing.mode == profile.mode,
                Listing.status != "removed",
            )
        ).first()
        if not (prop and listing) or not _is_new(listing):
            continue
        access = _access_typical(prop.id, profile.user_id)
        if not _meets_thresholds(profile, match.score, access):
            continue
        out.append((prop, listing, match, access))
    return out


def _alerts_for_profile(profile_id: int) -> None:
    with session_scope() as session:
        profile = session.get(SearchProfile, profile_id)
        if not profile or _in_quiet_hours(profile):
            return
        owner = session.get(User, profile.user_id) if profile.user_id else None
        channels = profile.alert_channels or []

        for channel in channels:
            pending = _pending_matches(session, profile, channel)
            if not pending:
                continue
            if profile.alert_digest:
                ok = _send_digest(channel, profile, pending, owner)
                if ok:
                    for prop, _, _, _ in pending:
                        _record(session, prop.id, profile.id, channel, "new_match")
            else:
                for prop, listing, match, access in pending:
                    ok = _send_single(channel, profile, prop, listing, match, owner, access)
                    if ok:
                        _record(session, prop.id, profile.id, channel, "new_match")


def _score_line(match_score: float, access: int | None) -> str:
    line = f"⭐ Match {round(match_score)}"
    if access is not None:
        line += f" · ⚡ Access {access}"
    return line


def _send_single(channel: str, profile: SearchProfile, prop: Property, listing: Listing, match: MatchScore, owner: User | None = None, access: int | None = None) -> bool:
    chat_id = owner.telegram_chat_id if owner and owner.telegram_chat_id else None
    email_to = owner.email_to if owner and owner.email_to else None
    price = _fmt_price(listing.price, listing.mode)
    if channel == "telegram":
        caption = (
            f"🏡 <b>{price}</b> — {prop.address}\n"
            f"{_score_line(match.score, access)} for “{profile.name}”\n"
            f"{prop.beds or '?'} bed {prop.property_type or 'property'}"
            + (f" · EPC {prop.epc}" if prop.epc else "")
            + f"\n{match.rationale}\n{listing.url}"
        )
        photo = prop.image_urls[0] if prop.image_urls else None
        return send_telegram(caption, photo_url=photo, chat_id=chat_id)
    if channel == "email":
        img = f'<img src="{prop.image_urls[0]}" style="max-width:480px;border-radius:12px"><br>' if prop.image_urls else ""
        body = (
            f"{img}<h2 style='margin:8px 0'>{price} — {prop.address}</h2>"
            f"<p><b>{_score_line(match.score, access)}</b> for “{profile.name}”<br>"
            f"{prop.beds or '?'} bed {prop.property_type or 'property'}</p>"
            f"<p>{match.rationale}</p>"
            f"<p><a href='{listing.url}'>View on {listing.portal}</a></p>"
        )
        return send_email(f"New match ({round(match.score)}): {prop.address}", body, to=email_to)
    return False


def _send_digest(channel: str, profile: SearchProfile, pending: list, owner: User | None = None) -> bool:
    chat_id = owner.telegram_chat_id if owner and owner.telegram_chat_id else None
    email_to = owner.email_to if owner and owner.email_to else None
    lines_html, lines_tg = [], []
    for prop, listing, match, access in pending[:20]:
        price = _fmt_price(listing.price, listing.mode)
        scores = f"⭐{round(match.score)}" + (f" ⚡{access}" if access is not None else "")
        lines_tg.append(f"{scores} — <b>{price}</b> {prop.address}\n{listing.url}")
        lines_html.append(
            f"<li><b>{price}</b> — {prop.address} ({scores}) "
            f"<a href='{listing.url}'>view</a></li>"
        )
    title = f"{len(pending)} new matches for “{profile.name}”"
    if channel == "telegram":
        return send_telegram(f"🏡 <b>{title}</b>\n\n" + "\n\n".join(lines_tg), chat_id=chat_id)
    if channel == "email":
        return send_email(title, f"<h2>{title}</h2><ul>{''.join(lines_html)}</ul>", to=email_to)
    return False


def _price_drops_for_profile(profile_id: int) -> None:
    """Alert on price drops (once per price point) for:
    - properties the owner saved to a list (explicit interest — no threshold gate), and
    - matched properties meeting BOTH alert thresholds (these may be old or already
      viewed — a price drop is the one event that brings them back, clearly labelled).
    """
    with session_scope() as session:
        profile = session.get(SearchProfile, profile_id)
        if not profile or _in_quiet_hours(profile):
            return
        owner = session.get(User, profile.user_id) if profile.user_id else None
        owner_list_ids = {
            s.id for s in session.exec(
                select(SavedList).where(SavedList.user_id == profile.user_id)
            ).all()
        }
        saved_ids = {
            i.property_id for i in session.exec(select(ListItem)).all()
            if i.list_id in owner_list_ids
        }
        match_by_prop = {
            m.property_id: m
            for m in session.exec(
                select(MatchScore).where(
                    MatchScore.profile_id == profile.id,
                    MatchScore.criteria_version == profile.criteria_version,
                    MatchScore.passed_filters == True,  # noqa: E712
                    MatchScore.score >= profile.alert_threshold,
                )
            ).all()
        }
        candidates = saved_ids | set(match_by_prop)
        for property_id in candidates:
            listing = session.exec(
                select(Listing).where(
                    Listing.property_id == property_id,
                    Listing.mode == profile.mode,
                    Listing.status != "removed",
                )
            ).first()
            if not listing or len(listing.price_history) < 2:
                continue
            last, prev = listing.price_history[-1], listing.price_history[-2]
            if last["price"] >= prev["price"]:
                continue
            match = match_by_prop.get(property_id)
            access = _access_typical(property_id, profile.user_id)
            saved = property_id in saved_ids
            # Threshold gate for match-driven drops; saved properties are exempt
            if not saved and (not match or not _meets_thresholds(profile, match.score, access)):
                continue
            kind = f"price_drop:{last['price']}"
            prop = session.get(Property, property_id)
            head = "📉 Price drop on a saved property" if saved else f"📉 Price drop on a match for “{profile.name}”"
            scores = _score_line(match.score, access) if match else (f"⚡ Access {access}" if access is not None else "")
            for channel in profile.alert_channels or []:
                if _already_sent(session, property_id, profile.id, channel, kind):
                    continue
                msg = (
                    f"{head}\n<b>{prop.address}</b>\n"
                    f"{_fmt_price(prev['price'], listing.mode)} → <b>{_fmt_price(last['price'], listing.mode)}</b>"
                    + (f"\n{scores}" if scores else "")
                    + f"\n{listing.url}"
                )
                ok = send_telegram(
                    msg, chat_id=(owner.telegram_chat_id if owner and owner.telegram_chat_id else None)
                ) if channel == "telegram" else send_email(
                    f"Price drop: {prop.address}",
                    f"<p>{msg.replace(chr(10), '<br>')}</p>",
                    to=(owner.email_to if owner and owner.email_to else None),
                )
                if ok:
                    _record(session, property_id, profile.id, channel, kind)
