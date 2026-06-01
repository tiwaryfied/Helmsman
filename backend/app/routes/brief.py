"""Morning Brief — the flagship multi-source query."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from .. import store
from ..agent import narrate
from ..auth import RequestContext, request_context
from ..config import SETTINGS
from ..coral_client import CORAL
from ..prompts import morning_brief_system
from ..sse import sse_response

router = APIRouter(prefix="/api/brief", tags=["brief"])

DEMO_CLOCK = "2026-05-30T09:00:00Z"


def _q_today_events(ctx: RequestContext) -> str:
    s = ctx.schemas
    cap = ctx.captain_login
    return f"""
    SELECT e.id, e.summary, e.start_at, e.end_at, e.location, e.description, e.organizer
    FROM {s['calendar']}.events e
    JOIN {s['calendar']}.event_attendees ea ON ea.event_id = e.id
    WHERE ea.attendee_login = '{cap}'
      AND e.start_at >= '2026-05-30T00:00:00Z'
      AND e.start_at <  '2026-05-31T00:00:00Z'
    ORDER BY e.start_at
    LIMIT 25
    """


def _q_review_queue(ctx: RequestContext) -> str:
    s = ctx.schemas
    cap = ctx.captain_login
    return f"""
    SELECT pr.pull_number, p.repo, p.title, p.user__login AS author, p.html_url,
      ROUND((EXTRACT(epoch FROM to_timestamp('{DEMO_CLOCK}'))
           - EXTRACT(epoch FROM to_timestamp(p.updated_at))) / 86400.0, 1) AS days_stale
    FROM {s['github']}.pull_reviewers pr
    JOIN {s['github']}.pulls p
      ON pr.owner = p.owner AND pr.repo = p.repo AND pr.pull_number = p.number
    WHERE pr.reviewer_login = '{cap}' AND p.state = 'open'
    ORDER BY p.updated_at ASC
    LIMIT 20
    """


def _q_my_open_prs(ctx: RequestContext) -> str:
    s = ctx.schemas
    cap = ctx.captain_login
    return f"""
    SELECT p.number, p.repo, p.title, p.html_url, p.requested_reviewers,
      ROUND((EXTRACT(epoch FROM to_timestamp('{DEMO_CLOCK}'))
           - EXTRACT(epoch FROM to_timestamp(p.updated_at))) / 86400.0, 1) AS days_stale
    FROM {s['github']}.pulls p
    WHERE p.state = 'open' AND p.user__login = '{cap}'
    ORDER BY p.updated_at ASC
    LIMIT 20
    """


def _q_my_open_linear(ctx: RequestContext) -> str:
    s = ctx.schemas
    email = ctx.captain_email
    return f"""
    SELECT identifier, title, state__name, priority_label, due_date, url
    FROM {s['linear']}.issues
    WHERE assignee__email = '{email}'
      AND state__type IN ('started', 'unstarted', 'triage')
    ORDER BY priority ASC, due_date ASC NULLS LAST
    LIMIT 20
    """


def _q_slack_mentions(ctx: RequestContext) -> str:
    s = ctx.schemas
    cap = ctx.captain_login
    return f"""
    SELECT channel_name, author_login, text, iso_ts
    FROM {s['slack']}.message_mentions
    WHERE mentioned_login = '{cap}'
    ORDER BY iso_ts DESC
    LIMIT 20
    """


def _q_active_fires(ctx: RequestContext) -> str:
    s = ctx.schemas
    return f"""
    SELECT short_id, title, level, count, user_count, last_seen, project_slug, tags__pr, tags__route, permalink
    FROM {s['sentry']}.issues
    WHERE status = 'unresolved' AND level IN ('error', 'fatal')
    ORDER BY count DESC
    LIMIT 10
    """


# Per-widget mapping: (key, builder_fn, source_kinds_used)
WIDGETS = [
    ("today_events",  _q_today_events,  ("calendar",)),
    ("review_queue",  _q_review_queue,  ("github",)),
    ("my_open_prs",   _q_my_open_prs,   ("github",)),
    ("my_open_linear",_q_my_open_linear,("linear",)),
    ("slack_mentions",_q_slack_mentions,("slack",)),
    ("active_fires",  _q_active_fires,  ("sentry",)),
]


def _connection_summary(ctx: RequestContext) -> dict[str, dict]:
    """Per-source connection info embedded in every Brief response."""
    rows = store.connections_list(ctx.user_id)
    return {
        r["source_kind"]: {
            "status": r["status"],
            "schema_name": r["schema_name"],
            "is_live": r["status"] == "live",
        }
        for r in rows
    }


def _widget_sources(kinds: tuple[str, ...], conn_map: dict[str, dict]) -> list[dict]:
    """For each source kind a widget uses, surface (kind, schema, status) so the UI can render an honest badge."""
    out: list[dict] = []
    for k in kinds:
        c = conn_map.get(k, {})
        out.append(
            {
                "kind": k,
                "schema": c.get("schema_name", f"{k}_demo"),
                "status": c.get("status", "disconnected"),
            }
        )
    return out


async def _gather(ctx: RequestContext):
    coros = [CORAL.sql(builder(ctx)) for _, builder, _ in WIDGETS]
    results = await asyncio.gather(*coros)
    return dict(zip([k for k, _, _ in WIDGETS], results))


@router.get("/today")
async def today(ctx: RequestContext = Depends(request_context)):
    """Non-streaming JSON snapshot used by the dashboard widgets."""
    data = await _gather(ctx)
    conn_map = _connection_summary(ctx)
    out: dict = {}
    for key, _, kinds in WIDGETS:
        r = data[key]
        d = r.to_dict()
        d["sources"] = _widget_sources(kinds, conn_map)
        out[key] = d
    out["_connections"] = conn_map
    return out


@router.post("/stream")
async def stream(ctx: RequestContext = Depends(request_context)):
    """Stream the SQL-then-narrate pipeline as SSE events."""
    conn_map = _connection_summary(ctx)

    async def gen():
        yield {"type": "status", "text": "Running 6 Coral queries in parallel..."}

        results = await _gather(ctx)
        for key, builder, kinds in WIDGETS:
            r = results[key]
            yield {"type": "sql", "label": key, "sql": builder(ctx).strip()}
            yield {
                "type": "result",
                "label": key,
                "rows": r.rows[:30],
                "row_count": len(r.rows),
                "elapsed_ms": r.elapsed_ms,
                "cache_hit": r.cache_hit,
                "error": r.error,
                "sources": _widget_sources(kinds, conn_map),
            }

        yield {"type": "status", "text": "Briefing the Captain..."}

        payload = {
            "now": DEMO_CLOCK,
            "captain": ctx.captain_login,
            "schemas_used": list(ctx.schemas.values()),
            "connections": conn_map,
            "today_events": results["today_events"].rows,
            "review_queue": results["review_queue"].rows,
            "my_open_prs": results["my_open_prs"].rows,
            "my_open_linear": results["my_open_linear"].rows,
            "slack_mentions": results["slack_mentions"].rows,
            "active_fires": results["active_fires"].rows,
        }

        chunks: list[str] = []
        async for tok in narrate(morning_brief_system(), payload,
                                  max_tokens=900, temperature=0.45):
            chunks.append(tok)
            yield {"type": "narrative_delta", "text": tok}
        yield {"type": "answer", "markdown": "".join(chunks)}

    return sse_response(gen())
