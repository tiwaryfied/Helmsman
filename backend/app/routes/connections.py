"""Source connection management.

Each user has six "connection slots" — one per supported source kind. Each
slot can be in one of three states:

    * ``live``         — user pasted a real API token; Coral has the source
                         installed and ``coral source test`` passed.
    * ``demo``         — user opted in to seeded JSONL data via the
                         ``*_demo`` schemas Helmsman ships with.
    * ``disconnected`` — slot is untouched. Queries gracefully fall back to
                         the seeded demo schema.

All token persistence is encrypted at rest with Fernet; tokens never leave the
machine and never appear in API responses.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .. import store
from ..auth import current_user, encrypt_token
from ..coral_client import CORAL

log = logging.getLogger("helmsman.connections")

router = APIRouter(prefix="/api/connections", tags=["connections"])


# Per-source spec: how the user obtains a token, what Coral expects.
SOURCE_SPECS: dict[str, dict] = {
    "github": {
        "label": "GitHub",
        "description": "Pulls, reviews, commits across your repos.",
        "tables": ["pulls", "pull_reviewers", "reviews", "issues", "commits", "repos"],
        "live_schema": "github",
        "demo_schema": "github_demo",
        "token_fields": [
            {
                "name": "token",
                "label": "Personal Access Token",
                "env": "GITHUB_TOKEN",
                "placeholder": "ghp_…",
                "help_url": "https://github.com/settings/tokens?type=beta",
                "help_text": "Generate a fine-grained PAT with read access to repos, issues, and pull requests.",
            },
        ],
        "live_supported": True,
    },
    "linear": {
        "label": "Linear",
        "description": "Issues, projects, and PR attachments.",
        "tables": ["issues", "projects", "attachments", "users"],
        "live_schema": "linear",
        "demo_schema": "linear_demo",
        "token_fields": [
            {
                "name": "token",
                "label": "API Key",
                "env": "LINEAR_API_KEY",
                "placeholder": "lin_api_…",
                "help_url": "https://linear.app/settings/api",
                "help_text": "Create a personal API key under Settings → API.",
            },
        ],
        "live_supported": True,
    },
    "slack": {
        "label": "Slack",
        "description": "Channels, messages, and @mentions.",
        "tables": ["channels", "messages", "message_mentions", "users"],
        "live_schema": "slack",
        "demo_schema": "slack_demo",
        "token_fields": [
            {
                "name": "token",
                "label": "Bot Token",
                "env": "SLACK_BOT_TOKEN",
                "placeholder": "xoxb-…",
                "help_url": "https://api.slack.com/apps",
                "help_text": "Install a Slack app to your workspace and copy its Bot User OAuth Token.",
            },
        ],
        "live_supported": True,
    },
    "sentry": {
        "label": "Sentry",
        "description": "Unresolved issues, releases, and events.",
        "tables": ["issues", "events", "releases", "projects"],
        "live_schema": "sentry",
        "demo_schema": "sentry_demo",
        "token_fields": [
            {
                "name": "token",
                "label": "Auth Token",
                "env": "SENTRY_AUTH_TOKEN",
                "placeholder": "sntrys_…",
                "help_url": "https://sentry.io/settings/account/api/auth-tokens/",
                "help_text": "Create a user auth token with project:read + event:read scopes.",
            },
        ],
        "live_supported": True,
    },
    "datadog": {
        "label": "Datadog",
        "description": "Metrics, monitors, and recent alerts.",
        "tables": ["metrics", "monitors", "events"],
        "live_schema": "datadog",
        "demo_schema": "datadog_demo",
        "token_fields": [
            {
                "name": "api_key",
                "label": "API Key",
                "env": "DD_API_KEY",
                "placeholder": "…",
                "help_url": "https://app.datadoghq.com/organization-settings/api-keys",
                "help_text": "Create an API key in Organization Settings → API keys.",
            },
            {
                "name": "app_key",
                "label": "Application Key",
                "env": "DD_APP_KEY",
                "placeholder": "…",
                "help_url": "https://app.datadoghq.com/organization-settings/application-keys",
                "help_text": "Create an Application key with metrics_read + monitors_read scopes.",
            },
        ],
        "live_supported": True,
    },
    "calendar": {
        "label": "Google Calendar",
        "description": "Today's events, attendees, and locations.",
        "tables": ["events", "event_attendees", "calendars"],
        "live_schema": "google_calendar",
        "demo_schema": "calendar_demo",
        "token_fields": [],
        "live_supported": False,
        "live_disabled_reason": (
            "Google Calendar needs an OAuth consent flow that's larger than a "
            "hackathon window. Demo data ships with realistic events so this "
            "slot still feels alive — and you can swap in a real Coral "
            "`google_calendar` source from your terminal whenever you're ready."
        ),
    },
}


@router.get("/specs")
async def specs() -> dict:
    """Static catalog of supported sources + token field definitions."""
    return {"sources": SOURCE_SPECS, "order": list(SOURCE_SPECS.keys())}


@router.get("")
async def list_connections(user: dict = Depends(current_user)) -> dict:
    conns = store.connections_list(int(user["id"]))
    installed = await CORAL.source_list()
    return {
        "connections": conns,
        "coral_installed_sources": installed,
        "specs": SOURCE_SPECS,
        "order": list(SOURCE_SPECS.keys()),
    }


class ConnectBody(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict)
    """Map of token field name → secret value. See SOURCE_SPECS[kind].token_fields."""


def _spec_for(kind: str) -> dict:
    if kind not in SOURCE_SPECS:
        raise HTTPException(status_code=404, detail=f"Unknown source '{kind}'")
    return SOURCE_SPECS[kind]


@router.post("/{kind}/connect")
async def connect(kind: str, body: ConnectBody, user: dict = Depends(current_user)) -> dict:
    spec = _spec_for(kind)
    if not spec.get("live_supported", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=spec.get("live_disabled_reason") or "Live connection not supported.",
        )

    env: dict[str, str] = {}
    primary_secret_value: Optional[str] = None
    for f in spec["token_fields"]:
        name = f["name"]
        env_key = f["env"]
        val = (body.fields.get(name) or "").strip()
        if not val:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing required field: {f['label']}",
            )
        env[env_key] = val
        if primary_secret_value is None:
            primary_secret_value = val

    ok, err = await CORAL.source_add_builtin(spec["live_schema"], env=env)
    if not ok:
        store.connection_record_error(int(user["id"]), kind, err or "unknown error")
        log.warning("connect failed kind=%s err=%s", kind, err)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Coral rejected the credentials for {spec['label']}: {err}",
        )

    encrypted = encrypt_token(primary_secret_value or "")
    meta = {
        "fields_set": list(env.keys()),
        "live_schema": spec["live_schema"],
    }
    conn = store.connection_set_live(int(user["id"]), kind, encrypted, token_meta=meta)
    log.info("connect ok kind=%s user_id=%s", kind, user["id"])
    return {"connection": conn}


@router.post("/{kind}/demo")
async def use_demo(kind: str, user: dict = Depends(current_user)) -> dict:
    _spec_for(kind)
    conn = store.connection_set_demo(int(user["id"]), kind)
    return {"connection": conn}


@router.post("/{kind}/test")
async def test_connection(kind: str, user: dict = Depends(current_user)) -> dict:
    spec = _spec_for(kind)
    conn = store.connection_get(int(user["id"]), kind)
    if not conn or conn["status"] != "live":
        return {"ok": False, "error": "Source isn't connected (live).", "connection": conn}
    ok, err = await CORAL.source_test(spec["live_schema"])
    if not ok:
        store.connection_record_error(int(user["id"]), kind, err or "test failed")
    return {"ok": ok, "error": err, "connection": store.connection_get(int(user["id"]), kind)}


@router.delete("/{kind}")
async def disconnect(kind: str, user: dict = Depends(current_user)) -> dict:
    spec = _spec_for(kind)
    # Only attempt to remove the Coral source if we were live for it.
    conn = store.connection_get(int(user["id"]), kind)
    if conn and conn["status"] == "live":
        await CORAL.source_remove(spec["live_schema"])
    conn = store.connection_set_disconnected(int(user["id"]), kind)
    return {"connection": conn}
