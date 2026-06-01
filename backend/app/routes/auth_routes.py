"""Auth routes — signup, login, logout, me."""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field

from .. import store
from ..auth import (
    clear_session_cookie,
    current_user,
    hash_password,
    optional_user,
    set_session_cookie,
    verify_password,
)

log = logging.getLogger("helmsman.auth_routes")

router = APIRouter(prefix="/api/auth", tags=["auth"])


_LOGIN_RE = re.compile(r"[^a-z0-9_-]+")


def _slug_from_email(email: str) -> str:
    base = email.split("@", 1)[0].lower()
    base = _LOGIN_RE.sub("-", base).strip("-") or "captain"
    return base[:32]


class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=80)
    captain_login: Optional[str] = Field(default=None, max_length=80)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


def _public_user(u: dict, conns: list[dict] | None = None) -> dict:
    return {
        "id": u["id"],
        "email": u["email"],
        "display_name": u["display_name"],
        "captain_login": u["captain_login"],
        "captain_email": u["captain_email"],
        "onboarded": bool(u.get("onboarded_at")),
        "onboarded_at": u.get("onboarded_at"),
        "created_at": u["created_at"],
        "connections": conns if conns is not None else store.connections_list(int(u["id"])),
    }


@router.post("/signup")
async def signup(body: SignupBody, response: Response) -> dict:
    if store.user_by_email(body.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )
    # Demo data is seeded with `captain_login='captain'` and
    # `captain_email='captain@coralreef.dev'`. We default to those values when
    # the user leaves the field blank so seeded demo widgets light up
    # immediately for first-time users. When they connect real sources they
    # can change the login in Settings.
    raw_login = (body.captain_login or "").strip().lower()
    login = _LOGIN_RE.sub("-", raw_login).strip("-") if raw_login else "captain"
    if not login:
        login = "captain"
    captain_email = "captain@coralreef.dev" if login == "captain" else str(body.email)
    user = store.user_create(
        email=str(body.email),
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip(),
        captain_login=login,
        captain_email=captain_email,
    )
    set_session_cookie(response, int(user["id"]))
    log.info("signup user_id=%s email=%s", user["id"], user["email"])
    return _public_user(user)


@router.post("/login")
async def login(body: LoginBody, response: Response) -> dict:
    user = store.user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong email or password.",
        )
    set_session_cookie(response, int(user["id"]))
    log.info("login  user_id=%s email=%s", user["id"], user["email"])
    return _public_user(user)


@router.post("/logout")
async def logout(response: Response) -> dict:
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user: dict | None = Depends(optional_user)) -> dict:
    """Returns the authenticated user or ``{authenticated: false}``."""
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": _public_user(user)}


@router.post("/complete-onboarding")
async def complete_onboarding(user: dict = Depends(current_user)) -> dict:
    store.user_mark_onboarded(int(user["id"]))
    return {"ok": True}


class CaptainUpdate(BaseModel):
    captain_login: Optional[str] = Field(default=None, max_length=80)
    captain_email: Optional[EmailStr] = None


@router.patch("/captain")
async def update_captain(
    body: CaptainUpdate,
    user: dict = Depends(current_user),
) -> dict:
    """Update the user's captain identity (the username that appears in every SQL filter)."""
    new_login = user["captain_login"]
    new_email = user["captain_email"]
    if body.captain_login is not None:
        login = _LOGIN_RE.sub("-", body.captain_login.strip().lower()).strip("-")
        if not login:
            raise HTTPException(status_code=400, detail="captain_login cannot be empty.")
        new_login = login
    if body.captain_email is not None:
        new_email = str(body.captain_email)
    store.user_update_captain(int(user["id"]), new_login, new_email)
    return _public_user(store.user_by_id(int(user["id"])))  # type: ignore[arg-type]
