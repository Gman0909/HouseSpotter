"""Alert logic: new matches over threshold → Telegram/email, deduped by the Notification
ledger; price-drop alerts for saved properties. Called at the end of every poll cycle."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from ..db import session_scope
from ..models import (
    Listing, ListItem, MatchScore, Notification, Property, SearchProfile,
)
from .channels import send_email, send_telegram

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


def _pending_matches(session: Session, profile: SearchProfile, channel: str) -> list[tuple[Property, Listing, MatchScore]]:
    matches = session.exec(
        select(MatchScore).where(
            MatchScore.profile_id == profile.id,
            MatchScore.criteria_version == profile.criteria_version,
            MatchScore.passed_filters == True,  # noqa: E712
            MatchScore.score >= profile.alert_threshold,
        )
    ).all()
    out = []
    for match in matches:
        if _already_sent(session, match.property_id, profile.id, channel, "new_match"):
            continue
        prop = session.get(Property, match.property_id)
        listing = session.exec(
            select(Listing).where(
                Listing.property_id == match.property_id,
                Listing.mode == profile.mode,
                Listing.status != "removed",
            )
        ).first()
        if prop and listing:
            out.append((prop, listing, match))
    return out


def _alerts_for_profile(profile_id: int) -> None:
    with session_scope() as session:
        profile = session.get(SearchProfile, profile_id)
        if not profile or _in_quiet_hours(profile):
            return
        channels = profile.alert_channels or []

        for channel in channels:
            pending = _pending_matches(session, profile, channel)
            if not pending:
                continue
            if profile.alert_digest:
                ok = _send_digest(channel, profile, pending)
                if ok:
                    for prop, _, _ in pending:
                        _record(session, prop.id, profile.id, channel, "new_match")
            else:
                for prop, listing, match in pending:
                    ok = _send_single(channel, profile, prop, listing, match)
                    if ok:
                        _record(session, prop.id, profile.id, channel, "new_match")


def _send_single(channel: str, profile: SearchProfile, prop: Property, listing: Listing, match: MatchScore) -> bool:
    price = _fmt_price(listing.price, listing.mode)
    if channel == "telegram":
        caption = (
            f"🏡 <b>{price}</b> — {prop.address}\n"
            f"⭐ Match score {round(match.score)} for “{profile.name}”\n"
            f"{prop.beds or '?'} bed {prop.property_type or 'property'}"
            + (f" · EPC {prop.epc}" if prop.epc else "")
            + f"\n{match.rationale}\n{listing.url}"
        )
        photo = prop.image_urls[0] if prop.image_urls else None
        return send_telegram(caption, photo_url=photo)
    if channel == "email":
        img = f'<img src="{prop.image_urls[0]}" style="max-width:480px;border-radius:12px"><br>' if prop.image_urls else ""
        body = (
            f"{img}<h2 style='margin:8px 0'>{price} — {prop.address}</h2>"
            f"<p><b>Match score {round(match.score)}</b> for “{profile.name}”<br>"
            f"{prop.beds or '?'} bed {prop.property_type or 'property'}</p>"
            f"<p>{match.rationale}</p>"
            f"<p><a href='{listing.url}'>View on {listing.portal}</a></p>"
        )
        return send_email(f"New match ({round(match.score)}): {prop.address}", body)
    return False


def _send_digest(channel: str, profile: SearchProfile, pending: list) -> bool:
    lines_html, lines_tg = [], []
    for prop, listing, match in pending[:20]:
        price = _fmt_price(listing.price, listing.mode)
        lines_tg.append(f"⭐{round(match.score)} — <b>{price}</b> {prop.address}\n{listing.url}")
        lines_html.append(
            f"<li><b>{price}</b> — {prop.address} (score {round(match.score)}) "
            f"<a href='{listing.url}'>view</a></li>"
        )
    title = f"{len(pending)} new matches for “{profile.name}”"
    if channel == "telegram":
        return send_telegram(f"🏡 <b>{title}</b>\n\n" + "\n\n".join(lines_tg))
    if channel == "email":
        return send_email(title, f"<h2>{title}</h2><ul>{''.join(lines_html)}</ul>")
    return False


def _price_drops_for_profile(profile_id: int) -> None:
    """Alert when a saved property's price drops (once per price point)."""
    with session_scope() as session:
        profile = session.get(SearchProfile, profile_id)
        if not profile or _in_quiet_hours(profile):
            return
        saved_ids = {i.property_id for i in session.exec(select(ListItem)).all()}
        if not saved_ids:
            return
        for property_id in saved_ids:
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
            kind = f"price_drop:{last['price']}"
            prop = session.get(Property, property_id)
            for channel in profile.alert_channels or []:
                if _already_sent(session, property_id, profile.id, channel, kind):
                    continue
                msg = (
                    f"📉 Price drop on a saved property\n<b>{prop.address}</b>\n"
                    f"{_fmt_price(prev['price'], listing.mode)} → <b>{_fmt_price(last['price'], listing.mode)}</b>\n"
                    f"{listing.url}"
                )
                ok = send_telegram(msg) if channel == "telegram" else send_email(
                    f"Price drop: {prop.address}",
                    f"<p>{msg.replace(chr(10), '<br>')}</p>",
                )
                if ok:
                    _record(session, property_id, profile.id, channel, kind)
