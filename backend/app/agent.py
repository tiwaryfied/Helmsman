"""Helmsman's agentic SQL planner.

The agent runs a discover → plan → query → reflect loop:

    1. Catalog discovery via `coral.tables` / `coral.columns` once per session.
    2. For each turn:
        a. Stream the LLM's "thought" + proposed SQL as a JSON block.
        b. Execute the SQL through Coral and stream the resulting rows + timing.
        c. Feed the result back to the LLM as a user message.
    3. Stop when the LLM emits an `answer` field (final markdown).

Events emitted (SSE 'data:' JSON lines):
    - {"type": "status", "text": "Discovering Coral catalog..."}
    - {"type": "catalog", "schemas": [...], "tables": [...]}
    - {"type": "thought_delta", "text": "..."}
    - {"type": "sql", "sql": "...", "turn": 1}
    - {"type": "result", "sql": "...", "rows": [...], "elapsed_ms": 23,
       "cache_hit": false, "row_count": 12, "error": null, "turn": 1}
    - {"type": "answer", "markdown": "..."}
    - {"type": "error", "message": "..."}
    - {"type": "done"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator

from .config import SETTINGS
from .coral_client import CORAL, QueryResult
from .llm import acomplete
from .prompts import planner_system, planner_user

log = logging.getLogger("helmsman.agent")

# A streamed JSON event channel uses these shapes.
Event = dict


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _balanced_object(text: str) -> str | None:
    """Return the LAST top-level balanced { ... } in text, if any."""
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for start in reversed(starts):
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start: i + 1]
    return None


def extract_json(text: str) -> dict | None:
    """Robustly extract a single JSON object from streamed model output."""
    m = _JSON_FENCE.search(text)
    candidates: list[str] = []
    if m:
        candidates.append(m.group(1))
    bal = _balanced_object(text)
    if bal:
        candidates.append(bal)
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
async def fetch_catalog() -> dict[str, list[dict]]:
    """Return {schema_name: [{table_name, columns:[...]}]}."""
    tables = await CORAL.list_tables()
    out: dict[str, list[dict]] = {}
    # Fetch columns per (schema, table) in parallel.
    coros = []
    keys: list[tuple[str, str]] = []
    for t in tables:
        if not t.get("schema_name") or not t.get("table_name"):
            continue
        # Skip the meta `coral` schema; agent can still query it directly.
        if t["schema_name"] == "coral":
            continue
        keys.append((t["schema_name"], t["table_name"]))
        coros.append(CORAL.list_columns(t["schema_name"], t["table_name"]))
    cols_per_table = await asyncio.gather(*coros, return_exceptions=True)
    for (schema, table), cols in zip(keys, cols_per_table):
        out.setdefault(schema, []).append({
            "table_name": table,
            "columns": cols if not isinstance(cols, Exception) else [],
        })
    for schema in out:
        out[schema].sort(key=lambda t: t["table_name"])
    return out


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
def _result_preview(r: QueryResult, max_rows: int = 8) -> str:
    """Compact textual preview to feed back into the LLM."""
    if r.error:
        return f"ERROR: {r.error[:500]}"
    if not r.rows:
        return f"OK: 0 rows in {r.elapsed_ms}ms."
    head = r.rows[: max_rows]
    body = json.dumps(head, separators=(",", ":"), default=str)
    extra = f" (+{len(r.rows)-max_rows} more rows truncated)" if len(r.rows) > max_rows else ""
    return (
        f"OK: {len(r.rows)} row(s) in {r.elapsed_ms}ms"
        f"{' [cache hit]' if r.cache_hit else ''}{extra}.\n"
        f"sample = {body}"
    )


async def run_agent(
    question: str,
    max_turns: int = 4,
    catalog: dict[str, list[dict]] | None = None,
    *,
    mode: str | None = None,
    captain_login: str | None = None,
    captain_email: str | None = None,
    schemas: dict[str, str] | None = None,
) -> AsyncIterator[Event]:
    """Stream the full agent loop as events for an SSE endpoint."""
    yield {"type": "status", "text": "Discovering Coral catalog..."}
    if catalog is None:
        catalog = await fetch_catalog()
    yield {
        "type": "catalog",
        "schemas": sorted(catalog.keys()),
        "tables": [
            {"schema_name": s, "table_name": t["table_name"],
             "column_count": len(t["columns"])}
            for s, ts in catalog.items() for t in ts
        ],
    }

    effective_mode = mode or SETTINGS.mode
    system = planner_system(
        catalog,
        effective_mode,
        captain_login=captain_login,
        captain_email=captain_email,
        schemas=schemas,
    )
    messages: list[dict] = [{"role": "user", "content": planner_user(question)}]

    for turn in range(1, max_turns + 1):
        yield {"type": "status", "text": f"Planning (turn {turn} / {max_turns})..."}
        # 1. Stream the LLM's response (thought + SQL or final answer).
        buf = ""
        async for tok in acomplete(system, messages, max_tokens=900, temperature=0.3):
            buf += tok
            yield {"type": "thought_delta", "text": tok, "turn": turn}

        # 2. Parse the JSON envelope.
        parsed = extract_json(buf) or {}
        thought = parsed.get("thought") or ""
        if thought:
            yield {"type": "thought", "text": thought, "turn": turn}

        # 3. If there's a final answer, ship it and stop.
        if "answer" in parsed and parsed.get("answer"):
            yield {"type": "answer", "markdown": parsed["answer"], "turn": turn}
            yield {"type": "done"}
            return

        # 4. Otherwise expect a SQL field.
        sql = (parsed.get("sql") or "").strip()
        if not sql:
            # The model didn't follow the JSON contract — try to coerce.
            yield {
                "type": "error",
                "message": ("Planner did not return a valid SQL block. "
                            "Raw output preview: " + buf[:400]),
                "turn": turn,
            }
            yield {"type": "done"}
            return

        yield {"type": "sql", "sql": sql, "turn": turn}
        result = await CORAL.sql(sql, use_cache=True)
        yield {
            "type": "result",
            "sql": sql,
            "rows": result.rows[:50],
            "row_count": len(result.rows),
            "elapsed_ms": result.elapsed_ms,
            "cache_hit": result.cache_hit,
            "error": result.error,
            "turn": turn,
        }

        # 5. Feed the result back to the model.
        messages.append({"role": "assistant", "content": buf})
        messages.append({
            "role": "user",
            "content": f"Result of turn {turn}:\n{_result_preview(result)}\n\n"
                       "If you have enough, return the final answer JSON. "
                       "Otherwise, return another SQL JSON block.",
        })

    yield {
        "type": "answer",
        "markdown": ("I ran out of turns before reaching a final answer. "
                     "Try narrowing your question, or open the Schema Atlas to "
                     "explore the catalog directly."),
        "turn": max_turns,
    }
    yield {"type": "done"}


# ---------------------------------------------------------------------------
# Specialised one-shot helper for narrative-style answers (Morning Brief etc.)
# ---------------------------------------------------------------------------
async def narrate(system: str, payload: dict, max_tokens: int = 900,
                  temperature: float = 0.5) -> AsyncIterator[str]:
    """Stream a narrative answer from a pre-baked structured payload."""
    user = (
        "Here is the structured data gathered from Coral. Write the response.\n\n"
        f"```json\n{json.dumps(payload, indent=2, default=str)}\n```"
    )
    async for tok in acomplete(system, [{"role": "user", "content": user}],
                               max_tokens=max_tokens, temperature=temperature):
        yield tok
