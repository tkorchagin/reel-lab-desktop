"""Anonymous browser-session dependency."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Generator
from typing import Annotated

from fastapi import Cookie, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from reel_lab.db.base import SessionLocal
from reel_lab.db.models import User, UserSession

SESSION_COOKIE = "reel_lab_session"


def database_session() -> Generator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_user(
    response: Response,
    session: Annotated[Session, Depends(database_session)],
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    """Resolve or create one local anonymous user from an HttpOnly cookie."""
    token_hash = session_token_hash(token) if token else None
    user = (
        session.scalar(
            select(User)
            .join(UserSession, UserSession.user_id == User.id)
            .where(UserSession.token_hash == token_hash)
        )
        if token_hash
        else None
    )
    if user:
        return user
    token = secrets.token_urlsafe(32)
    token_hash = session_token_hash(token)
    user = User(token_hash=token_hash)
    session.add(user)
    session.flush()
    session.add(UserSession(token_hash=token_hash, user_id=user.id))
    set_session_cookie(response, token)
    return user


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
