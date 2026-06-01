"""Helmsman FastAPI app entry point."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import store
from .config import SETTINGS
from .routes import (
    ask, auth_routes, blockers, brief, captains_log, catalog,
    connections, health, incident, sql, voyages,
)

logging.basicConfig(
    level=os.getenv("HELMSMAN_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)

app = FastAPI(
    title="Helmsman",
    description="Your AI Chief of Staff for engineering — powered by Coral.",
    version="0.1.0",
)

# In local dev / hackathon judging context the frontend talks to the API
# via the Next.js rewrite (same-origin), so allow_credentials works cleanly.
# When developers hit the API from a different origin (e.g. http://127.0.0.1:3000
# vs http://localhost:3000) the explicit allow_origin_regex lets cookies flow.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    store.init()


app.include_router(health.router)
app.include_router(auth_routes.router)
app.include_router(connections.router)
app.include_router(catalog.router)
app.include_router(sql.router)
app.include_router(ask.router)
app.include_router(brief.router)
app.include_router(blockers.router)
app.include_router(incident.router)
app.include_router(captains_log.router)
app.include_router(voyages.router)


@app.get("/")
async def root() -> dict:
    return {
        "name": "Helmsman",
        "version": "0.1.0",
        "docs": "/docs",
    }
