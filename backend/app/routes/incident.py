"""Incident War Room — multi-hop agent that correlates Sentry × GitHub × Linear × Slack."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from .. import store
from ..agent import narrate
from ..auth import RequestContext, request_context
from ..coral_client import CORAL
from ..prompts import incident_system
from ..schemas import IncidentRequest
from ..sse import sse_response

router = APIRouter(prefix="/api/incident", tags=["incident"])


@router.post("/stream")
async def stream(
    req: IncidentRequest,
    ctx: RequestContext = Depends(request_context),
):
    s = ctx.schemas
    symptom = req.symptom.replace("'", "''")
    conns = {c["source_kind"]: c for c in store.connections_list(ctx.user_id)}

    async def gen():
        yield {"type": "status", "text": "Triangulating across Sentry, deploys, Linear, Slack, Datadog..."}

        q_sentry = f"""
        SELECT short_id, title, culprit, count, user_count, level, status,
               first_seen, last_seen, project_slug, tags__pr, tags__route, release, stack_top, permalink
        FROM {s['sentry']}.issues
        WHERE status = 'unresolved'
          AND (LOWER(title) LIKE LOWER('%{symptom}%')
               OR LOWER(culprit) LIKE LOWER('%{symptom}%')
               OR LOWER(tags__route) LIKE LOWER('%{symptom}%'))
        ORDER BY count DESC
        LIMIT 10
        """

        q_recent_prs = f"""
        SELECT number, owner, repo, title, user__login AS author, merged_at, html_url, body
        FROM {s['github']}.pulls
        WHERE state = 'merged'
          AND merged_at >= '2026-05-25T00:00:00Z'
        ORDER BY merged_at DESC
        LIMIT 15
        """

        q_linear_inc = f"""
        SELECT identifier, title, state__name, priority_label, assignee__name, url
        FROM {s['linear']}.issues
        WHERE (LOWER(title) LIKE LOWER('%{symptom}%')
               OR LOWER(description) LIKE LOWER('%{symptom}%')
               OR priority_label = 'Urgent')
        ORDER BY priority ASC, updated_at DESC
        LIMIT 10
        """

        q_slack = f"""
        SELECT channel_name, user__name AS who, text, iso_ts, thread_ts
        FROM {s['slack']}.messages
        WHERE LOWER(text) LIKE LOWER('%{symptom}%')
           OR LOWER(text) LIKE LOWER('%incident%')
           OR LOWER(text) LIKE LOWER('%rollback%')
        ORDER BY iso_ts DESC
        LIMIT 15
        """

        q_dd = f"""
        SELECT service, route, value_pct, timestamp
        FROM {s['datadog']}.metrics
        WHERE LOWER(route) LIKE LOWER('%{symptom}%')
           OR LOWER(metric) LIKE '%4xx%'
           OR LOWER(metric) LIKE '%5xx%'
        ORDER BY timestamp DESC
        LIMIT 40
        """

        q_bridge = f"""
        SELECT i.identifier AS linear_id, i.title AS linear_title,
               p.number AS pr, p.title AS pr_title, p.state AS pr_state,
               p.user__login AS pr_author, p.merged_at, p.html_url
        FROM {s['linear']}.attachments a
        JOIN {s['linear']}.issues i ON i.id = a.issue_id
        JOIN {s['github']}.pulls p ON p.html_url = a.url
        WHERE LOWER(i.title) LIKE LOWER('%{symptom}%')
           OR LOWER(p.title)  LIKE LOWER('%{symptom}%')
           OR LOWER(p.body)   LIKE LOWER('%{symptom}%')
        ORDER BY p.merged_at DESC NULLS LAST
        LIMIT 10
        """

        labels_and_kinds = [
            ("sentry_errors",    ("sentry",)),
            ("recent_prs",       ("github",)),
            ("linear_issues",    ("linear",)),
            ("slack_chatter",    ("slack",)),
            ("datadog_metrics",  ("datadog",)),
            ("linear_x_github",  ("linear", "github")),
        ]
        labels = [lk[0] for lk in labels_and_kinds]
        queries = [q_sentry, q_recent_prs, q_linear_inc, q_slack, q_dd, q_bridge]
        results = await asyncio.gather(*(CORAL.sql(q) for q in queries))

        for (label, kinds), q, r in zip(labels_and_kinds, queries, results):
            yield {"type": "sql", "label": label, "sql": q.strip()}
            yield {
                "type": "result", "label": label, "rows": r.rows[:30],
                "row_count": len(r.rows), "elapsed_ms": r.elapsed_ms,
                "cache_hit": r.cache_hit, "error": r.error,
                "sources": [
                    {
                        "kind": k,
                        "schema": (conns.get(k) or {}).get("schema_name", f"{k}_demo"),
                        "status": (conns.get(k) or {}).get("status", "disconnected"),
                    }
                    for k in kinds
                ],
            }

        yield {"type": "status", "text": "Forming root-cause hypothesis..."}

        payload = {
            "symptom": req.symptom,
            "now": "2026-05-30T09:00:00Z",
            "sentry_errors": results[0].rows,
            "recent_prs": results[1].rows,
            "linear_issues": results[2].rows,
            "slack_chatter": results[3].rows,
            "datadog_metrics": results[4].rows[:20],
            "linear_x_github": results[5].rows,
        }
        chunks: list[str] = []
        async for tok in narrate(incident_system(), payload, max_tokens=800):
            chunks.append(tok)
            yield {"type": "narrative_delta", "text": tok}
        yield {"type": "answer", "markdown": "".join(chunks)}

    return sse_response(gen())
