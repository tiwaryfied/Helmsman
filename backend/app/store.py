"""SQLite store for Helmsman app state (saved Voyages, chat history)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import SETTINGS

DB_PATH = SETTINGS.repo_root / "backend" / "helmsman.db"
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with _lock, _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email           TEXT NOT NULL UNIQUE,
                password_hash   TEXT NOT NULL,
                display_name    TEXT NOT NULL,
                captain_login   TEXT NOT NULL,
                captain_email   TEXT NOT NULL,
                onboarded_at    TEXT,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connections (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                source_kind     TEXT NOT NULL,
                status          TEXT NOT NULL,           -- 'live' | 'demo' | 'disconnected'
                schema_name     TEXT NOT NULL,           -- e.g. 'github' or 'github_demo'
                token_encrypted TEXT,                    -- Fernet ciphertext (or NULL for demo)
                token_meta      TEXT,                    -- JSON with non-secret meta (e.g. {api_key_set:true})
                last_tested_at  TEXT,
                last_error      TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                UNIQUE(user_id, source_kind),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS voyages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                description     TEXT,
                sql             TEXT NOT NULL,
                cadence         TEXT NOT NULL DEFAULT 'daily',
                alert_when      TEXT,
                last_run_at     TEXT,
                last_row_count  INTEGER,
                last_elapsed_ms INTEGER,
                last_status     TEXT,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS voyage_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                voyage_id       INTEGER NOT NULL,
                ran_at          TEXT NOT NULL,
                row_count       INTEGER,
                elapsed_ms      INTEGER,
                status          TEXT,
                error           TEXT,
                sample_rows     TEXT,
                FOREIGN KEY (voyage_id) REFERENCES voyages(id)
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation  TEXT NOT NULL,
                role          TEXT NOT NULL,
                kind          TEXT NOT NULL,
                content       TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );
        """)
        # Seed voyages if empty so the page isn't blank on first run.
        cur = c.execute("SELECT COUNT(*) AS n FROM voyages")
        if cur.fetchone()["n"] == 0:
            seed_voyages(c)


def seed_voyages(c) -> None:
    """Create a few story-consistent starter voyages."""
    s = SETTINGS.schemas
    now = _now_iso()
    starter = [
        (
            "PRs awaiting my review",
            "Open PRs that have me as a requested reviewer, sorted by staleness.",
            (
                "SELECT pr.pull_number, p.repo, p.title, p.user__login AS author, p.html_url, "
                f"ROUND((EXTRACT(epoch FROM to_timestamp('2026-05-30T09:00:00Z')) - EXTRACT(epoch FROM to_timestamp(p.updated_at))) / 86400.0, 1) AS days_stale "
                f"FROM {s['github']}.pull_reviewers pr "
                f"JOIN {s['github']}.pulls p ON pr.owner=p.owner AND pr.repo=p.repo AND pr.pull_number=p.number "
                f"WHERE pr.reviewer_login='{SETTINGS.captain_login}' AND p.state='open' "
                "ORDER BY p.updated_at ASC LIMIT 20"
            ),
            "daily",
            "rows >= 3",
        ),
        (
            "Linear ⇄ GitHub PR linkage",
            "All Linear issues with linked GitHub PRs (a cross-source JOIN flex).",
            (
                "SELECT i.identifier, i.title AS linear_title, i.state__name AS linear_state, "
                "p.number AS pr_number, p.state AS pr_state, p.html_url "
                f"FROM {s['linear']}.attachments a "
                f"JOIN {s['linear']}.issues i ON i.id = a.issue_id "
                f"JOIN {s['github']}.pulls p ON p.html_url = a.url "
                "ORDER BY p.number DESC LIMIT 30"
            ),
            "weekly",
            None,
        ),
        (
            "Stale review queue (>2 days)",
            "Surfaces any PR where the requested reviewer hasn't acted in 48+ hours.",
            (
                "SELECT pr.reviewer_login, p.repo, pr.pull_number, p.title, "
                f"ROUND((EXTRACT(epoch FROM to_timestamp('2026-05-30T09:00:00Z')) - EXTRACT(epoch FROM to_timestamp(p.updated_at))) / 86400.0, 1) AS days_stale "
                f"FROM {s['github']}.pull_reviewers pr "
                f"JOIN {s['github']}.pulls p ON pr.owner=p.owner AND pr.repo=p.repo AND pr.pull_number=p.number "
                "WHERE p.state='open' "
                f"  AND (EXTRACT(epoch FROM to_timestamp('2026-05-30T09:00:00Z')) - EXTRACT(epoch FROM to_timestamp(p.updated_at))) / 86400.0 > 2 "
                "ORDER BY days_stale DESC LIMIT 25"
            ),
            "daily",
            "rows >= 5",
        ),
        (
            "Active Sentry issues on recent releases",
            "Cross-references Sentry issues with the PRs in their release tag.",
            (
                "SELECT s.short_id, s.title, s.count, s.user_count, s.tags__pr, s.release, "
                "p.title AS pr_title, p.html_url "
                f"FROM {s['sentry']}.issues s "
                f"LEFT JOIN {s['github']}.pulls p ON CAST(p.number AS VARCHAR) = s.tags__pr "
                "WHERE s.status = 'unresolved' "
                "ORDER BY s.count DESC LIMIT 20"
            ),
            "hourly",
            "rows >= 1",
        ),
    ]
    for name, desc, sql, cadence, alert in starter:
        c.execute(
            "INSERT INTO voyages (name, description, sql, cadence, alert_when, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, desc, sql, cadence, alert, now),
        )


# ---------------------------------------------------------------------------
# Voyage CRUD
# ---------------------------------------------------------------------------
def voyages_list() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM voyages ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def voyage_get(vid: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM voyages WHERE id = ?", (vid,)).fetchone()
        return dict(r) if r else None


def voyage_create(name, description, sql, cadence, alert_when) -> int:
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO voyages (name, description, sql, cadence, alert_when, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, sql, cadence, alert_when, _now_iso()),
        )
        return int(cur.lastrowid)


def voyage_delete(vid: int) -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM voyage_runs WHERE voyage_id = ?", (vid,))
        c.execute("DELETE FROM voyages WHERE id = ?", (vid,))


def voyage_record_run(vid: int, row_count: int, elapsed_ms: int,
                      status: str, error: str | None, sample_rows: list[dict]) -> None:
    with _lock, _conn() as c:
        now = _now_iso()
        c.execute(
            "INSERT INTO voyage_runs (voyage_id, ran_at, row_count, elapsed_ms, status, error, sample_rows) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (vid, now, row_count, elapsed_ms, status, error, json.dumps(sample_rows[:10])),
        )
        c.execute(
            "UPDATE voyages SET last_run_at = ?, last_row_count = ?, last_elapsed_ms = ?, last_status = ? "
            "WHERE id = ?",
            (now, row_count, elapsed_ms, status, vid),
        )


def voyage_recent_runs(vid: int, limit: int = 20) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM voyage_runs WHERE voyage_id = ? ORDER BY id DESC LIMIT ?",
            (vid, limit),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            if d.get("sample_rows"):
                try:
                    d["sample_rows"] = json.loads(d["sample_rows"])
                except json.JSONDecodeError:
                    d["sample_rows"] = []
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def user_create(
    email: str,
    password_hash: str,
    display_name: str,
    captain_login: str,
    captain_email: str,
) -> dict:
    with _lock, _conn() as c:
        now = _now_iso()
        cur = c.execute(
            "INSERT INTO users (email, password_hash, display_name, captain_login, captain_email, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (email.lower().strip(), password_hash, display_name, captain_login, captain_email, now),
        )
        uid = int(cur.lastrowid)
        # Seed default 'disconnected' connections for every source the app knows.
        for kind in ("github", "linear", "slack", "sentry", "datadog", "calendar"):
            c.execute(
                "INSERT INTO connections (user_id, source_kind, status, schema_name, created_at, updated_at) "
                "VALUES (?, ?, 'disconnected', ?, ?, ?)",
                (uid, kind, _default_demo_schema(kind), now, now),
            )
        r = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(r)


def user_by_email(email: str) -> dict | None:
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(r) if r else None


def user_by_id(uid: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(r) if r else None


def user_mark_onboarded(uid: int) -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE users SET onboarded_at = ? WHERE id = ?", (_now_iso(), uid))


def user_update_captain(uid: int, captain_login: str, captain_email: str) -> None:
    with _lock, _conn() as c:
        c.execute(
            "UPDATE users SET captain_login = ?, captain_email = ? WHERE id = ?",
            (captain_login, captain_email, uid),
        )


def user_count() -> int:
    with _conn() as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])


# ---------------------------------------------------------------------------
# Connections (per-user, per-source)
# ---------------------------------------------------------------------------
SOURCE_KINDS = ("github", "linear", "slack", "sentry", "datadog", "calendar")


def _default_demo_schema(kind: str) -> str:
    """Each source's seeded demo schema name."""
    return f"{kind}_demo"


def _default_live_schema(kind: str) -> str:
    """Each source's live schema name when connected via real Coral source."""
    # Coral's built-in source name for Google Calendar is 'google_calendar'.
    if kind == "calendar":
        return "google_calendar"
    return kind


def live_schema_for(kind: str) -> str:
    return _default_live_schema(kind)


def demo_schema_for(kind: str) -> str:
    return _default_demo_schema(kind)


def connections_list(user_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM connections WHERE user_id = ? ORDER BY source_kind",
            (user_id,),
        ).fetchall()
        return [_decorate_connection(dict(r)) for r in rows]


def connection_get(user_id: int, kind: str) -> dict | None:
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM connections WHERE user_id = ? AND source_kind = ?",
            (user_id, kind),
        ).fetchone()
        return _decorate_connection(dict(r)) if r else None


def connection_set_demo(user_id: int, kind: str) -> dict:
    with _lock, _conn() as c:
        now = _now_iso()
        c.execute(
            "UPDATE connections SET status='demo', schema_name=?, token_encrypted=NULL, "
            "token_meta=NULL, last_tested_at=?, last_error=NULL, updated_at=? "
            "WHERE user_id=? AND source_kind=?",
            (_default_demo_schema(kind), now, now, user_id, kind),
        )
    return connection_get(user_id, kind)  # type: ignore[return-value]


def connection_set_disconnected(user_id: int, kind: str) -> dict:
    with _lock, _conn() as c:
        now = _now_iso()
        c.execute(
            "UPDATE connections SET status='disconnected', schema_name=?, token_encrypted=NULL, "
            "token_meta=NULL, last_tested_at=NULL, last_error=NULL, updated_at=? "
            "WHERE user_id=? AND source_kind=?",
            (_default_demo_schema(kind), now, user_id, kind),
        )
    return connection_get(user_id, kind)  # type: ignore[return-value]


def connection_set_live(
    user_id: int,
    kind: str,
    token_encrypted: str,
    token_meta: dict | None = None,
) -> dict:
    with _lock, _conn() as c:
        now = _now_iso()
        c.execute(
            "UPDATE connections SET status='live', schema_name=?, token_encrypted=?, "
            "token_meta=?, last_tested_at=?, last_error=NULL, updated_at=? "
            "WHERE user_id=? AND source_kind=?",
            (
                _default_live_schema(kind),
                token_encrypted,
                json.dumps(token_meta or {}),
                now,
                now,
                user_id,
                kind,
            ),
        )
    return connection_get(user_id, kind)  # type: ignore[return-value]


def connection_record_error(user_id: int, kind: str, err: str) -> None:
    with _lock, _conn() as c:
        now = _now_iso()
        c.execute(
            "UPDATE connections SET last_error=?, last_tested_at=?, updated_at=? "
            "WHERE user_id=? AND source_kind=?",
            (err[:1000], now, now, user_id, kind),
        )


def _decorate_connection(row: dict) -> dict:
    """Strip secrets, parse meta, add convenience flags before returning to API."""
    out = dict(row)
    out.pop("token_encrypted", None)  # never leave the server
    meta = out.get("token_meta")
    if isinstance(meta, str) and meta:
        try:
            out["token_meta"] = json.loads(meta)
        except json.JSONDecodeError:
            out["token_meta"] = {}
    elif meta is None:
        out["token_meta"] = {}
    out["is_live"] = out.get("status") == "live"
    return out


def resolve_user_schemas(user_id: int) -> dict[str, str]:
    """Return the canonical-source -> Coral-schema map for this user.

    Connected sources hit their live Coral schema (e.g. 'github').
    Demo sources hit the seeded JSONL schema (e.g. 'github_demo').
    Disconnected sources gracefully fall back to demo so the app stays usable
    even when the user has skipped onboarding.
    """
    conns = connections_list(user_id)
    out: dict[str, str] = {}
    for c in conns:
        kind = c["source_kind"]
        if c["status"] == "live":
            out[kind] = c["schema_name"] or _default_live_schema(kind)
        else:
            out[kind] = _default_demo_schema(kind)
    for k in SOURCE_KINDS:
        out.setdefault(k, _default_demo_schema(k))
    return out
