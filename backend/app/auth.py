from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlmodel import Session, select

from .config import settings
from .db import get_session
from .models import User

COOKIE_NAME = "hs_session"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _serializer() -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError("HS_SESSION_SECRET is not set")
    return URLSafeTimedSerializer(settings.session_secret, salt="hs-session")


def create_session_token(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def require_user(request: Request, session: Session = Depends(get_session)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        data = _serializer().loads(token, max_age=settings.session_days * 86400)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=401, detail="Invalid session")
    user = session.get(User, data["uid"])
    if not user:
        raise HTTPException(status_code=401, detail="Unknown user")
    return user


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody, response: Response, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == body.username)).first()
    if not user or not verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user.id),
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        # Not `secure`: served over plain HTTP inside the tailnet (WireGuard-encrypted).
    )
    return {"ok": True, "username": user.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    return user


_bot_username_cache: dict[str, str] = {}


def _bot_username() -> str | None:
    """The shared bot's @username (for setup instructions), cached per token."""
    token = settings.telegram_bot_token
    if not token:
        return None
    if token not in _bot_username_cache:
        try:
            import httpx

            data = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=6).json()
            if data.get("ok"):
                _bot_username_cache[token] = data["result"].get("username", "")
        except Exception:
            return None  # transient — don't cache failures
    return _bot_username_cache.get(token) or None


@router.get("/me")
def me(user: User = Depends(require_user)):
    return {
        "username": user.username,
        "is_admin": user.is_admin,
        "telegram_chat_id": user.telegram_chat_id,
        "email_to": user.email_to,
        # channel readiness: server half (admin-configured) + this user's target
        "channels": {
            "telegram": {
                "server": bool(settings.telegram_bot_token),
                "user": bool(user.telegram_chat_id),
                "bot_username": _bot_username(),
            },
            "email": {"server": bool(settings.smtp_host), "user": bool(user.email_to)},
        },
    }


@router.patch("/me/alerts")
def update_my_alerts(body: dict, user: User = Depends(require_user), session: Session = Depends(get_session)):
    """Per-user alert targets — where this user's Telegram/email alerts go."""
    if "telegram_chat_id" in body:
        user.telegram_chat_id = str(body["telegram_chat_id"] or "").strip()
    if "email_to" in body:
        user.email_to = str(body["email_to"] or "").strip()
    session.add(user)
    session.commit()
    return {"ok": True, "telegram_chat_id": user.telegram_chat_id, "email_to": user.email_to}


@router.post("/me/detect-telegram")
def detect_my_telegram(user: User = Depends(require_user), session: Session = Depends(get_session)):
    """Set this user's chat ID from the bot's latest updates. The user must have
    messaged the bot (pressed Start) just before clicking — we take the newest chat."""
    if not settings.telegram_bot_token:
        raise HTTPException(422, "The admin needs to set up the Telegram bot first")
    import httpx

    try:
        resp = httpx.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates", timeout=15
        )
        data = resp.json()
    except Exception:
        raise HTTPException(502, "Couldn't reach Telegram")
    if not data.get("ok"):
        raise HTTPException(502, f"Telegram error: {data.get('description', 'invalid token?')}")
    for update in reversed(data.get("result", [])):
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("id"):
            user.telegram_chat_id = str(chat["id"])
            session.add(user)
            session.commit()
            name = chat.get("first_name") or chat.get("username") or "?"
            return {
                "ok": True,
                "chat_id": user.telegram_chat_id,
                "detail": f"Found chat {user.telegram_chat_id} ({name}) — saved. If that's not you, message the bot and detect again.",
            }
    raise HTTPException(404, "No messages found — open the bot in Telegram, press Start (or send it any message), then try again")


@router.post("/me/test-telegram")
def test_my_telegram(user: User = Depends(require_user)):
    if not user.telegram_chat_id:
        raise HTTPException(422, "Set your chat ID first")
    from .notify.channels import send_telegram

    ok = send_telegram("✅ HouseSpotter: your personal Telegram alerts are working.", chat_id=user.telegram_chat_id)
    if not ok:
        raise HTTPException(502, "Telegram rejected the message — check the chat ID (and that you've messaged the bot)")
    return {"ok": True, "detail": "Test message sent"}


# --- Admin: user management ---

@router.get("/users")
def list_users(session: Session = Depends(get_session), _admin: User = Depends(require_admin)):
    return [
        {"id": u.id, "username": u.username, "is_admin": u.is_admin, "created_at": u.created_at}
        for u in session.exec(select(User).order_by(User.id)).all()
    ]


@router.post("/users")
def create_user(body: dict, session: Session = Depends(get_session), _admin: User = Depends(require_admin)):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or len(password) < 8:
        raise HTTPException(422, "Username required; password must be at least 8 characters")
    if session.exec(select(User).where(User.username == username)).first():
        raise HTTPException(422, "Username already taken")
    user = User(username=username, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, session: Session = Depends(get_session), admin: User = Depends(require_admin)):
    if user_id == admin.id:
        raise HTTPException(422, "You can't delete your own account")
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404)
    from .userdata import delete_user_data

    delete_user_data(session, user_id)
    session.delete(target)
    session.commit()
    return {"ok": True}


class AccountBody(BaseModel):
    current_password: str
    new_username: str | None = None
    new_password: str | None = None


@router.post("/account")
def update_account(
    body: AccountBody,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Change username and/or password. Requires the current password.
    (The HS_USERNAME/HS_PASSWORD env values only seed the first user — the database
    is authoritative after that.)"""
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(401, "Current password is incorrect")
    changed = []
    if body.new_username and body.new_username.strip() and body.new_username.strip() != user.username:
        user.username = body.new_username.strip()
        changed.append("username")
    if body.new_password:
        if len(body.new_password) < 8:
            raise HTTPException(422, "New password must be at least 8 characters")
        user.password_hash = hash_password(body.new_password)
        changed.append("password")
    if not changed:
        raise HTTPException(422, "Nothing to change")
    session.add(user)
    session.commit()
    return {"ok": True, "username": user.username, "changed": changed}
