"""Voyages — saved & scheduled multi-source SQL workflows with alert rules."""
from __future__ import annotations

import operator
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import store
from ..auth import current_user
from ..coral_client import CORAL
from ..schemas import VoyageCreate, VoyageRow

router = APIRouter(
    prefix="/api/voyages",
    tags=["voyages"],
    dependencies=[Depends(current_user)],
)


def _alert_triggered(rule: str | None, rows: list[dict]) -> bool:
    """Evaluate a simple natural-language alert rule like 'rows >= 3'.

    Supported rules:
        rows OP <int>           e.g. "rows > 0", "rows >= 5"
        any                     always triggers
        none                    never triggers
    """
    if not rule:
        return False
    rule = rule.strip().lower()
    if rule == "any":
        return True
    if rule in ("none", ""):
        return False
    m = re.match(r"^rows\s*(>=|<=|==|=|>|<)\s*(\d+)$", rule)
    if not m:
        return False
    op, n = m.group(1), int(m.group(2))
    ops = {
        ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
        "==": operator.eq, "=": operator.eq,
    }
    return ops[op](len(rows), n)


@router.get("", response_model=list[VoyageRow])
async def list_voyages() -> list[VoyageRow]:
    rows = store.voyages_list()
    return [VoyageRow(**r) for r in rows]


@router.post("", response_model=VoyageRow)
async def create_voyage(req: VoyageCreate) -> VoyageRow:
    new_id = store.voyage_create(
        req.name, req.description, req.sql, req.cadence, req.alert_when,
    )
    return VoyageRow(**store.voyage_get(new_id))


@router.delete("/{vid}")
async def delete_voyage(vid: int) -> dict:
    if not store.voyage_get(vid):
        raise HTTPException(status_code=404, detail="Voyage not found")
    store.voyage_delete(vid)
    return {"deleted": vid}


@router.post("/{vid}/run")
async def run_voyage(vid: int) -> dict[str, Any]:
    v = store.voyage_get(vid)
    if not v:
        raise HTTPException(status_code=404, detail="Voyage not found")
    r = await CORAL.sql(v["sql"], use_cache=True)
    status = "error" if r.error else ("alert" if _alert_triggered(v["alert_when"], r.rows) else "ok")
    store.voyage_record_run(
        vid=vid, row_count=len(r.rows), elapsed_ms=r.elapsed_ms,
        status=status, error=r.error, sample_rows=r.rows[:10],
    )
    return {
        "voyage": store.voyage_get(vid),
        "result": r.to_dict(),
        "alert_triggered": status == "alert",
    }


@router.get("/{vid}/runs")
async def voyage_runs(vid: int, limit: int = 20) -> list[dict]:
    if not store.voyage_get(vid):
        raise HTTPException(status_code=404, detail="Voyage not found")
    return store.voyage_recent_runs(vid, limit)
