"""SQL playground — direct, no LLM."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..coral_client import CORAL
from ..schemas import SQLRequest

router = APIRouter(
    prefix="/api/sql",
    tags=["sql"],
    dependencies=[Depends(current_user)],
)


@router.post("/run")
async def run(req: SQLRequest) -> dict:
    r = await CORAL.sql(req.sql, use_cache=req.use_cache)
    return r.to_dict()
