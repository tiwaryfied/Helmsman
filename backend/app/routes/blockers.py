"""Blocker Radar — PRs blocking the Captain and PRs the Captain is blocking on."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from .. import store
from ..auth import RequestContext, request_context
from ..coral_client import CORAL

router = APIRouter(prefix="/api/blockers", tags=["blockers"])

DEMO_CLOCK = "2026-05-30T09:00:00Z"


@router.get("")
async def blockers(ctx: RequestContext = Depends(request_context)):
    s = ctx.schemas
    cap = ctx.captain_login

    q_blocking_others = f"""
    SELECT pr.pull_number, p.repo, p.title, p.user__login AS author, p.html_url,
      ROUND((EXTRACT(epoch FROM to_timestamp('{DEMO_CLOCK}'))
           - EXTRACT(epoch FROM to_timestamp(p.updated_at))) / 86400.0, 1) AS days_stale
    FROM {s['github']}.pull_reviewers pr
    JOIN {s['github']}.pulls p
      ON pr.owner = p.owner AND pr.repo = p.repo AND pr.pull_number = p.number
    WHERE pr.reviewer_login = '{cap}' AND p.state = 'open'
    ORDER BY p.updated_at ASC
    LIMIT 25
    """

    q_blocked_on_others = f"""
    SELECT p.number AS pull_number, p.repo, p.title, p.html_url, p.requested_reviewers,
      ROUND((EXTRACT(epoch FROM to_timestamp('{DEMO_CLOCK}'))
           - EXTRACT(epoch FROM to_timestamp(p.updated_at))) / 86400.0, 1) AS days_stale
    FROM {s['github']}.pulls p
    WHERE p.state = 'open' AND p.user__login = '{cap}'
    ORDER BY p.updated_at ASC
    LIMIT 25
    """

    blocking, blocked = await asyncio.gather(
        CORAL.sql(q_blocking_others),
        CORAL.sql(q_blocked_on_others),
    )
    conn = store.connection_get(ctx.user_id, "github") or {}
    sources = [
        {
            "kind": "github",
            "schema": conn.get("schema_name", "github_demo"),
            "status": conn.get("status", "disconnected"),
        }
    ]
    out_blocking = blocking.to_dict()
    out_blocked = blocked.to_dict()
    out_blocking["sources"] = sources
    out_blocked["sources"] = sources
    return {
        "blocking_others": out_blocking,
        "blocked_on_others": out_blocked,
    }
