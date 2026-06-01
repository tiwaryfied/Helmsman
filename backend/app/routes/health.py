"""Health / system status."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import store
from ..auth import optional_user
from ..config import SETTINGS
from ..coral_client import CORAL
from ..llm import select_provider

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "helmsman"}


@router.get("/system")
async def system(user: dict | None = Depends(optional_user)) -> dict:
    sources = await CORAL.list_sources()
    coral_v = await CORAL.coral_version()
    llm = select_provider()

    if user:
        schemas = store.resolve_user_schemas(int(user["id"]))
        captain = user["captain_login"]
        captain_email = user["captain_email"]
        # Per-user mode summary: 'live' if anything is live, else 'demo'.
        conns = store.connections_list(int(user["id"]))
        any_live = any(c["status"] == "live" for c in conns)
        per_source_mode = "live" if any_live else "demo"
    else:
        schemas = {k: f"{k}_demo" for k in ("github", "linear", "slack", "sentry", "datadog", "calendar")}
        captain = "guest"
        captain_email = "guest@helmsman.local"
        per_source_mode = "demo"

    return {
        "mode": per_source_mode,
        "captain": captain,
        "captain_email": captain_email,
        "coral_version": coral_v,
        "coral_bin": SETTINGS.coral_bin,
        "available_sources": sources,
        "llm_provider": llm.provider,
        "llm_model": llm.model,
        "has_llm": llm.provider != "scripted",
        "schemas": schemas,
        "cache": CORAL.cache_stats(),
        "authenticated": user is not None,
    }
