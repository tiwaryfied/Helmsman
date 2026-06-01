"""Thin async wrapper around the `coral` CLI.

Helmsman never speaks SQL to anything but Coral. Every cross-source JOIN,
schema discovery call, and incident-room query flows through this client.

Features:
    - Async subprocess execution (FastAPI-friendly).
    - JSON output parsing.
    - In-process result cache (so judges can SEE Coral's caching at work in
      timings, in addition to whatever Coral does internally).
    - Per-query timing.
    - Catalog helpers (tables / columns / functions).
    - Source-install helpers (`coral source add` / `coral source test` /
      `coral source remove`) so the UI can wire up real Coral sources from
      pasted user tokens.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .config import SETTINGS


@contextmanager
def _silent():
    """Swallow exceptions in best-effort cleanup paths (e.g. killing a hung proc)."""
    try:
        yield
    except Exception:
        pass

log = logging.getLogger("helmsman.coral")


@dataclass
class QueryResult:
    sql: str
    rows: list[dict[str, Any]]
    elapsed_ms: int
    cache_hit: bool = False
    error: str | None = None
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql, "rows": self.rows, "elapsed_ms": self.elapsed_ms,
            "cache_hit": self.cache_hit, "error": self.error,
            "row_count": len(self.rows), "truncated": self.truncated,
        }


@dataclass
class _CacheEntry:
    result: QueryResult
    inserted_at: float


class CoralClient:
    def __init__(self, coral_bin: str | None = None, ttl_seconds: int = 60,
                 cache_size: int = 256, max_rows: int = 500):
        self.coral_bin = coral_bin or SETTINGS.coral_bin
        self.ttl_seconds = ttl_seconds
        self.cache_size = cache_size
        self.max_rows = max_rows
        self._cache: dict[str, _CacheEntry] = {}

    # ------------------------------------------------------------------
    # Core query path
    # ------------------------------------------------------------------
    async def sql(self, query: str, use_cache: bool = True) -> QueryResult:
        """Execute a SQL query through `coral sql --format json`."""
        key = hashlib.sha1(query.strip().encode("utf-8")).hexdigest()
        if use_cache:
            hit = self._cache.get(key)
            if hit and (time.time() - hit.inserted_at) < self.ttl_seconds:
                # Return a copy with a flag so callers can show "cache hit".
                cached = QueryResult(
                    sql=hit.result.sql, rows=hit.result.rows,
                    elapsed_ms=hit.result.elapsed_ms, cache_hit=True,
                    error=hit.result.error, truncated=hit.result.truncated,
                )
                log.debug("cache hit  %s rows", len(cached.rows))
                return cached

        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                self.coral_bin, "sql", "--format", "json", query,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
        except FileNotFoundError as exc:
            return QueryResult(sql=query, rows=[], elapsed_ms=0,
                               error=f"coral CLI not found at '{self.coral_bin}': {exc}")
        except Exception as exc:
            return QueryResult(sql=query, rows=[], elapsed_ms=0,
                               error=f"coral invocation failed: {exc}")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if proc.returncode != 0:
            err = (stderr_b or stdout_b).decode("utf-8", errors="replace").strip()
            return QueryResult(sql=query, rows=[], elapsed_ms=elapsed_ms,
                               error=err or f"coral exited with code {proc.returncode}")

        text = stdout_b.decode("utf-8", errors="replace").strip()
        if not text:
            rows: list[dict[str, Any]] = []
        else:
            try:
                rows = json.loads(text)
                if not isinstance(rows, list):
                    rows = [rows]
            except json.JSONDecodeError as exc:
                return QueryResult(sql=query, rows=[], elapsed_ms=elapsed_ms,
                                   error=f"coral returned non-JSON: {exc}\n\n{text[:400]}")

        truncated = False
        if len(rows) > self.max_rows:
            rows = rows[: self.max_rows]
            truncated = True

        result = QueryResult(sql=query, rows=rows, elapsed_ms=elapsed_ms,
                             cache_hit=False, truncated=truncated)
        if use_cache and result.error is None:
            self._cache_put(key, result)
        return result

    def _cache_put(self, key: str, result: QueryResult) -> None:
        if len(self._cache) >= self.cache_size:
            oldest = min(self._cache.items(), key=lambda kv: kv[1].inserted_at)[0]
            self._cache.pop(oldest, None)
        self._cache[key] = _CacheEntry(result=result, inserted_at=time.time())

    def cache_stats(self) -> dict[str, int]:
        return {"entries": len(self._cache), "max": self.cache_size,
                "ttl_seconds": self.ttl_seconds}

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Catalog helpers
    # ------------------------------------------------------------------
    async def list_tables(self) -> list[dict[str, str]]:
        r = await self.sql(
            "SELECT schema_name, table_name FROM coral.tables ORDER BY 1, 2",
            use_cache=True,
        )
        return r.rows if not r.error else []

    async def list_columns(self, schema: str, table: str) -> list[dict[str, Any]]:
        q = (
            "SELECT column_name, data_type, description, ordinal_position "
            f"FROM coral.columns WHERE schema_name = '{schema}' AND table_name = '{table}' "
            "ORDER BY ordinal_position"
        )
        r = await self.sql(q, use_cache=True)
        return r.rows if not r.error else []

    async def list_table_functions(self) -> list[dict[str, str]]:
        r = await self.sql(
            "SELECT schema_name, function_name FROM coral.table_functions "
            "ORDER BY 1, 2",
            use_cache=True,
        )
        return r.rows if not r.error else []

    async def list_sources(self) -> list[str]:
        """Return the distinct schemas Coral exposes."""
        tables = await self.list_tables()
        return sorted({t["schema_name"] for t in tables if "schema_name" in t})

    async def coral_version(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.coral_bin, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, _ = await proc.communicate()
            return stdout_b.decode("utf-8", errors="replace").strip()
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------
    # Source-install helpers
    # ------------------------------------------------------------------
    async def _run(self, *args: str, env: dict[str, str] | None = None,
                    timeout: float = 45.0) -> tuple[int, str, str]:
        """Run an arbitrary ``coral`` subcommand and return (code, stdout, stderr)."""
        full_env = os.environ.copy()
        if env:
            full_env.update({k: v for k, v in env.items() if v is not None})
        try:
            proc = await asyncio.create_subprocess_exec(
                self.coral_bin, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
            )
        except FileNotFoundError as exc:
            return (127, "", f"coral CLI not found at '{self.coral_bin}': {exc}")
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            with _silent():
                proc.kill()
                await proc.wait()
            return (124, "", f"coral {args[0]} timed out after {timeout}s")
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )

    async def source_list(self) -> list[str]:
        """Return installed source names (one per line from `coral source list`)."""
        code, stdout, stderr = await self._run("source", "list")
        if code != 0:
            log.warning("coral source list failed: %s", stderr.strip())
            return []
        names: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            # Skip headers, blank lines, and decorative bars.
            if not line or line.lower().startswith(("name", "----", "==")) or "─" in line:
                continue
            first = line.split()[0]
            if first and first[0].isalpha():
                names.append(first)
        return names

    async def source_add_builtin(
        self,
        kind: str,
        env: dict[str, str],
        *,
        replace: bool = True,
    ) -> tuple[bool, str | None]:
        """Install a built-in Coral source (github / linear / slack / sentry / datadog).

        ``kind`` is the canonical source name as Coral knows it (``google_calendar``
        for calendar). ``env`` carries the secrets required by that source's
        Coral spec (e.g. ``{"GITHUB_TOKEN": "ghp_..."}``).
        """
        installed = await self.source_list()
        if kind in installed:
            if not replace:
                return (True, None)
            await self._run("source", "remove", kind)

        code, stdout, stderr = await self._run("source", "add", kind, env=env)
        if code != 0:
            err = (stderr or stdout).strip() or f"coral source add {kind} exited {code}"
            return (False, err[:1500])

        # Validate with `coral source test`; this rejects bad tokens early so
        # the UI can surface them.
        ok, err = await self.source_test(kind)
        if not ok:
            # Roll back the install so we don't leave a half-broken source.
            await self._run("source", "remove", kind)
            return (False, err)
        return (True, None)

    async def source_test(self, kind: str) -> tuple[bool, str | None]:
        code, stdout, stderr = await self._run("source", "test", kind, timeout=30.0)
        if code == 0:
            return (True, None)
        return (False, (stderr or stdout).strip()[:1500] or f"exit {code}")

    async def source_remove(self, kind: str) -> tuple[bool, str | None]:
        code, _, stderr = await self._run("source", "remove", kind)
        if code != 0 and "not found" not in stderr.lower():
            return (False, stderr.strip()[:500] or f"exit {code}")
        return (True, None)


# Single shared client instance.
CORAL = CoralClient()
