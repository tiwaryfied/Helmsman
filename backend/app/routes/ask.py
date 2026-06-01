"""Ask Helmsman: streaming NL → multi-source SQL agent."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import store
from ..agent import run_agent
from ..auth import RequestContext, request_context
from ..schemas import AskRequest
from ..sse import sse_response

router = APIRouter(prefix="/api/ask", tags=["agent"])


def _per_user_mode(ctx: RequestContext) -> str:
    conns = store.connections_list(ctx.user_id)
    return "live" if any(c["status"] == "live" for c in conns) else "demo"


@router.post("/stream")
async def ask_stream(
    req: AskRequest,
    ctx: RequestContext = Depends(request_context),
):
    return sse_response(
        run_agent(
            req.question,
            max_turns=max(1, min(req.max_turns, 6)),
            mode=_per_user_mode(ctx),
            captain_login=ctx.captain_login,
            captain_email=ctx.captain_email,
            schemas=ctx.schemas,
        )
    )
