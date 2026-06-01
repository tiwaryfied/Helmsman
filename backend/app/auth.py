"""Authentication + per-user request context for Helmsman.

Single-machine, local-first auth: bcrypt-hashed passwords stored in SQLite,
session id signed with itsdangerous and dropped in an httponly cookie.

The session secret + the token-encryption key are derived from a local secret
file (``backend/.helmsman_secret``) that is created on first start. Keeping the
secret on disk means restarts don't log everyone out and stored source tokens
remain decryptable across runs.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Cookie, Depends, HTTPException, Response, status
from itsdangerous import BadSignature, URLSafeSerializer

from . import store
from .config import SETTINGS

log = logging.getLogger("helmsman.auth")

COOKIE_NAME = "helmsman_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 14  # 14 days

_SECRET_FILE = SETTINGS.repo_root / "backend" / ".helmsman_secret"


def _load_or_create_secret() -> bytes:
    """Persistent, local-only secret used to sign sessions and derive Fernet."""
    override = os.getenv("HELMSMAN_SESSION_SECRET")
    if override:
        return override.encode("utf-8")
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _SECRET_FILE.exists():
        _SECRET_FILE.write_bytes(secrets.token_bytes(48))
        try:
            os.chmod(_SECRET_FILE, 0o600)
        except OSError:
            pass
    return _SECRET_FILE.read_bytes()


_SECRET = _load_or_create_secret()
_signer = URLSafeSerializer(_SECRET, salt="helmsman-session.v1")


def _fernet_key() -> bytes:
    import base64
    import hashlib

    h = hashlib.sha256(_SECRET + b"|fernet").digest()
    return base64.urlsafe_b64encode(h)


_fernet = Fernet(_fernet_key())


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def make_session_token(user_id: int) -> str:
    return _signer.dumps({"uid": int(user_id)})


def verify_session_token(token: str) -> int | None:
    try:
        data = _signer.loads(token)
        if isinstance(data, dict) and "uid" in data:
            return int(data["uid"])
    except BadSignature:
        return None
    return None


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=make_session_token(user_id),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # local-first: served over http://localhost
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# Token encryption (for storing PATs / API keys at rest)
# ---------------------------------------------------------------------------
def encrypt_token(plain: str) -> str:
    return _fernet.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str | None:
    try:
        return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
@dataclass
class RequestContext:
    """Resolved per-request context: identity + schema map."""

    user: dict
    schemas: dict[str, str]

    @property
    def captain_login(self) -> str:
        return self.user["captain_login"]

    @property
    def captain_email(self) -> str:
        return self.user["captain_email"]

    @property
    def user_id(self) -> int:
        return int(self.user["id"])


def _current_user(token: Optional[str]) -> dict | None:
    if not token:
        return None
    uid = verify_session_token(token)
    if uid is None:
        return None
    return store.user_by_id(uid)


def current_user(
    session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> dict:
    """Required-auth dependency: 401 if not signed in."""
    u = _current_user(session)
    if not u:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
        )
    return u


def optional_user(
    session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> dict | None:
    """Soft auth dependency for routes that work in both states."""
    return _current_user(session)


def request_context(user: dict = Depends(current_user)) -> RequestContext:
    schemas = store.resolve_user_schemas(int(user["id"]))
    return RequestContext(user=user, schemas=schemas)
