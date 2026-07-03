from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..models import ChatMessage

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_user)])


@router.get("/{session_id}")
def history(session_id: str, session: Session = Depends(get_session)):
    return session.exec(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
    ).all()


@router.post("/{session_id}")
def send(session_id: str, body: dict, session: Session = Depends(get_session)):
    text = (body.get("message") or "").strip()
    if not text:
        raise HTTPException(422, "message required")
    from ..llm.intake import intake_turn

    return intake_turn(session, session_id, text)
