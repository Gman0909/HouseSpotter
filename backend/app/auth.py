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


@router.get("/me")
def me(user: User = Depends(require_user)):
    return {"username": user.username}


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
