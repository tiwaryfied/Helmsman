"""Captain's Log — auto-generated weekly recap for the manager."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..agent import narrate
from ..auth import RequestContext, request_context
from ..coral_client import CORAL
from ..prompts import captains_log_system
from ..sse import sse_response

router = APIRouter(prefix="/api/captains-log", tags=["log"])

WEEK_START = "2026-05-23T00:00:00Z"
WEEK_END = "2026-05-30T23:59:59Z"
WEEK_LABEL = "May 23 – May 30, 2026"


@router.post("/stream")
async def stream(ctx: RequestContext = Depends(request_context)):
    s = ctx.schemas
    cap = ctx.captain_login
    email = ctx.captain_email

    async def gen():
        yield {"type": "status", "text": "Mustering the week's data..."}

        q_merged = f"""
        SELECT number, repo, title, merged_at, html_url, additions, deletions
        FROM {s['github']}.pulls
        WHERE state = 'merged' AND user__login = '{cap}'
          AND merged_at >= '{WEEK_START}' AND merged_at <= '{WEEK_END}'
        ORDER BY merged_at DESC
        LIMIT 30
        """

        q_closed_linear = f"""
        SELECT identifier, title, completed_at, url
        FROM {s['linear']}.issues
        WHERE assignee__email = '{email}'
          AND state__type = 'completed'
          AND completed_at >= '{WEEK_START}' AND completed_at <= '{WEEK_END}'
        ORDER BY completed_at DESC
        LIMIT 30
        """

        q_incidents = f"""
        SELECT short_id, title, first_seen, last_seen, count, user_count, project_slug
        FROM {s['sentry']}.issues
        WHERE status = 'unresolved'
          AND last_seen >= '{WEEK_START}' AND last_seen <= '{WEEK_END}'
        ORDER BY count DESC
        LIMIT 5
        """

        q_open = f"""
        SELECT identifier, title, state__name, priority_label, due_date
        FROM {s['linear']}.issues
        WHERE assignee__email = '{email}'
          AND state__type IN ('started','unstarted')
        ORDER BY priority ASC LIMIT 10
        """

        merged, closed, inc, open_ = await asyncio.gather(
            CORAL.sql(q_merged), CORAL.sql(q_closed_linear),
            CORAL.sql(q_incidents), CORAL.sql(q_open),
        )

        for label, q, r in [
            ("merged_prs", q_merged, merged),
            ("closed_linear", q_closed_linear, closed),
            ("active_sentry", q_incidents, inc),
            ("open_next_week", q_open, open_),
        ]:
            yield {"type": "sql", "label": label, "sql": q.strip()}
            yield {"type": "result", "label": label, "rows": r.rows[:30],
                   "row_count": len(r.rows), "elapsed_ms": r.elapsed_ms,
                   "cache_hit": r.cache_hit, "error": r.error}

        yield {"type": "status", "text": "Drafting your Captain's Log..."}

        payload = {
            "week_label": WEEK_LABEL,
            "manager": "Jules",
            "captain": cap,
            "merged_prs": merged.rows,
            "closed_linear": closed.rows,
            "active_sentry": inc.rows,
            "next_week_open": open_.rows,
        }
        chunks: list[str] = []
        async for tok in narrate(captains_log_system(captain_login=cap), payload, max_tokens=900):
            chunks.append(tok)
            yield {"type": "narrative_delta", "text": tok}
        yield {"type": "answer", "markdown": "".join(chunks)}

    return sse_response(gen())
