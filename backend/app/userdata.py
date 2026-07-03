"""Cascade deletion of a user's data (profiles, lists, milestones, chats and all
their dependents). Used by admin user-deletion."""
import logging

from sqlalchemy import delete as sa_delete, update as sa_update
from sqlmodel import Session, select

from .models import (
    AreaResult, AreaSearch, ChatMessage, ListItem, MatchScore, Meta, Milestone,
    Notification, ProfileSnapshot, SavedList, ScrapeRun, SearchProfile, TravelTime,
)

log = logging.getLogger("housespotter.userdata")


def delete_profile_data(session: Session, profile_id: int) -> None:
    """Everything hanging off one search profile (not the profile row itself)."""
    session.exec(sa_delete(MatchScore).where(MatchScore.profile_id == profile_id))
    session.exec(sa_delete(Notification).where(Notification.profile_id == profile_id))
    session.exec(sa_delete(ProfileSnapshot).where(ProfileSnapshot.profile_id == profile_id))
    session.exec(sa_update(ScrapeRun).where(ScrapeRun.profile_id == profile_id).values(profile_id=None))
    session.exec(sa_update(ChatMessage).where(ChatMessage.profile_id == profile_id).values(profile_id=None))
    for search in session.exec(select(AreaSearch).where(AreaSearch.profile_id == profile_id)).all():
        session.exec(sa_delete(AreaResult).where(AreaResult.area_search_id == search.id))
        status_row = session.get(Meta, f"research_status:search:{search.id}")
        if status_row:
            session.delete(status_row)
        session.delete(search)


def delete_user_data(session: Session, user_id: int) -> None:
    for profile in session.exec(select(SearchProfile).where(SearchProfile.user_id == user_id)).all():
        delete_profile_data(session, profile.id)
        session.delete(profile)
    for saved in session.exec(select(SavedList).where(SavedList.user_id == user_id)).all():
        session.exec(sa_delete(ListItem).where(ListItem.list_id == saved.id))
        session.delete(saved)
    for milestone in session.exec(select(Milestone).where(Milestone.user_id == user_id)).all():
        session.exec(sa_delete(TravelTime).where(TravelTime.milestone_id == milestone.id))
        session.delete(milestone)
    session.exec(sa_delete(ChatMessage).where(ChatMessage.user_id == user_id))
    session.commit()
    log.info("Deleted all data for user %s", user_id)
