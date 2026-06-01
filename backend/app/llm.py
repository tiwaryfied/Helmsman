"""LLM adapter for Helmsman.

Supports Google Gemini, Anthropic, and OpenAI. The agent loop in `agent.py`
calls a single `acomplete()` function that yields token deltas for
streaming-style UX.

If no API key is configured we fall back to a structured-summary stream so
the UI still surfaces real Coral data even without a model.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

from .config import SETTINGS

log = logging.getLogger("helmsman.llm")


@dataclass
class LLMConfig:
    provider: str          # "gemini" | "anthropic" | "openai" | "scripted"
    model: str
    api_key: str | None


def _cfg(provider: str) -> LLMConfig | None:
    if provider == "gemini" and SETTINGS.gemini_api_key:
        return LLMConfig("gemini", SETTINGS.gemini_model, SETTINGS.gemini_api_key)
    if provider == "anthropic" and SETTINGS.anthropic_api_key:
        return LLMConfig("anthropic", SETTINGS.anthropic_model, SETTINGS.anthropic_api_key)
    if provider == "openai" and SETTINGS.openai_api_key:
        return LLMConfig("openai", SETTINGS.openai_model, SETTINGS.openai_api_key)
    return None


def select_provider() -> LLMConfig:
    """Pick a provider honoring HELMSMAN_LLM_PROVIDER, falling back to auto."""
    pref = SETTINGS.llm_provider_pref
    if pref != "auto":
        chosen = _cfg(pref)
        if chosen:
            return chosen
    # auto: Gemini > Anthropic > OpenAI > scripted
    for p in ("gemini", "anthropic", "openai"):
        chosen = _cfg(p)
        if chosen:
            return chosen
    return LLMConfig("scripted", "scripted", None)


async def acomplete(system: str, messages: list[dict], max_tokens: int = 1200,
                    temperature: float = 0.4) -> AsyncIterator[str]:
    """Stream a completion as token chunks."""
    cfg = select_provider()
    if cfg.provider == "gemini":
        async for chunk in _gemini_stream(system, messages, max_tokens, temperature, cfg):
            yield chunk
    elif cfg.provider == "anthropic":
        async for chunk in _anthropic_stream(system, messages, max_tokens, temperature, cfg):
            yield chunk
    elif cfg.provider == "openai":
        async for chunk in _openai_stream(system, messages, max_tokens, temperature, cfg):
            yield chunk
    else:
        async for chunk in _scripted_stream(messages):
            yield chunk


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------
async def _gemini_stream(system, messages, max_tokens, temperature, cfg) -> AsyncIterator[str]:
    """Stream from Google's Generative Language (Gemini) API.

    Maps OpenAI/Anthropic-style {role, content} messages to Gemini's
    {role, parts:[{text}]} format. The system prompt is sent via
    `systemInstruction`. Streaming responses come back as SSE events
    when `?alt=sse` is appended.
    """
    contents = []
    for m in messages:
        role = m.get("role", "user")
        # Gemini uses "user" and "model" (no "system"); collapse assistant -> model.
        if role == "assistant":
            role = "model"
        elif role not in ("user", "model"):
            role = "user"
        contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})

    payload = {
        "systemInstruction": {"role": "system", "parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            # Gemini 2.5 models are "thinking" models: without this they spend
            # the output-token budget on hidden reasoning and truncate the
            # visible answer. We surface our own agent reasoning, so disable it.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{cfg.model}:streamGenerateContent?alt=sse&key={cfg.api_key}"
    )
    headers = {"content-type": "application/json"}
    timeout = httpx.Timeout(120.0, connect=20.0)

    # Retry transient network failures, but only while we have not yet emitted
    # any text (so we never duplicate a partially streamed narrative).
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        produced = False
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        yield f"\n[gemini error {resp.status_code}: {body.decode('utf-8', 'replace')[:300]}]"
                        return
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]" or not data:
                            continue
                        try:
                            evt = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        for cand in evt.get("candidates", []):
                            for part in (cand.get("content") or {}).get("parts", []):
                                txt = part.get("text")
                                if txt:
                                    produced = True
                                    yield txt
            return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.RemoteProtocolError, httpx.PoolTimeout) as exc:
            detail = str(exc) or type(exc).__name__
            if produced or attempt == max_attempts:
                log.warning("gemini stream failed after %d attempt(s): %s", attempt, detail)
                yield (
                    f"\n[gemini network error after {attempt} attempt(s): {detail}. "
                    "The Coral data above is still valid — retry to get a model narrative.]"
                )
                return
            log.warning("gemini connect issue (%s), retrying %d/%d", detail, attempt, max_attempts)
            await asyncio.sleep(0.6 * attempt)
        except Exception as exc:  # noqa: BLE001 — surface unexpected errors clearly
            log.exception("gemini stream failed")
            yield f"\n[gemini error: {str(exc) or type(exc).__name__}]"
            return


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
async def _anthropic_stream(system, messages, max_tokens, temperature, cfg) -> AsyncIterator[str]:
    """Stream from the Anthropic Messages API."""
    payload = {
        "model": cfg.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": messages,
        "stream": True,
    }
    headers = {
        "x-api-key": cfg.api_key or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        try:
            async with client.stream("POST", "https://api.anthropic.com/v1/messages",
                                     json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    yield f"\n[anthropic error {resp.status_code}: {body.decode('utf-8', 'replace')[:300]}]"
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        evt = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("type") == "content_block_delta":
                        d = evt.get("delta", {})
                        if d.get("type") == "text_delta":
                            yield d.get("text", "")
        except Exception as exc:
            log.exception("anthropic stream failed")
            yield f"\n[anthropic transport error: {exc}]"


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
async def _openai_stream(system, messages, max_tokens, temperature, cfg) -> AsyncIterator[str]:
    full = [{"role": "system", "content": system}] + messages
    payload = {
        "model": cfg.model, "messages": full, "stream": True,
        "max_tokens": max_tokens, "temperature": temperature,
    }
    headers = {
        "authorization": f"Bearer {cfg.api_key}",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        try:
            async with client.stream("POST", "https://api.openai.com/v1/chat/completions",
                                     json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    yield f"\n[openai error {resp.status_code}: {body.decode('utf-8', 'replace')[:300]}]"
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        evt = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in evt.get("choices", []):
                        delta = choice.get("delta", {}).get("content")
                        if delta:
                            yield delta
        except Exception as exc:
            log.exception("openai stream failed")
            yield f"\n[openai transport error: {exc}]"


# ---------------------------------------------------------------------------
# Scripted fallback
# ---------------------------------------------------------------------------
SCRIPTED_INTRO = (
    "_Running without an LLM key. The narrative below is a structured "
    "summary of what Coral returned. Set `GEMINI_API_KEY` (or "
    "`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) in `.env` and restart for a "
    "model-written briefing._\n\n"
)


def _scripted_narrative_from_json(text: str) -> str:
    """Inspect the user message (JSON blob) and produce a useful markdown summary."""
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if not match:
        return SCRIPTED_INTRO + "(no structured payload to summarize.)"
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return SCRIPTED_INTRO + "(payload was not valid JSON.)"

    lines: list[str] = [SCRIPTED_INTRO.rstrip(), "## Summary"]
    for key, val in payload.items():
        if key in ("now", "captain", "mode", "schemas_used", "week_label", "manager"):
            continue
        if isinstance(val, list):
            lines.append(f"- **{key}** — {len(val)} row(s)")
            for row in val[:3]:
                if isinstance(row, dict):
                    if "title" in row and "number" in row:
                        lines.append(
                            f"  - #{row.get('number')} {row.get('title')}"
                        )
                    elif "identifier" in row and "title" in row:
                        lines.append(
                            f"  - {row['identifier']} — {row['title']}"
                        )
                    elif "summary" in row and "start_at" in row:
                        lines.append(
                            f"  - {row.get('start_at','')[:16]}  {row.get('summary')}"
                        )
                    elif "short_id" in row:
                        lines.append(
                            f"  - {row.get('short_id')} — {row.get('title','')} ({row.get('count')} events)"
                        )
                    elif "text" in row and "channel_name" in row:
                        lines.append(
                            f"  - #{row['channel_name']} — {str(row.get('text',''))[:100]}"
                        )
                    else:
                        # generic fallback: first two keys
                        items = list(row.items())[:2]
                        lines.append(
                            "  - " + " · ".join(f"{k}={v}" for k, v in items)
                        )
        elif val is not None:
            lines.append(f"- **{key}** — {val}")
    return "\n".join(lines)


async def _scripted_stream(messages) -> AsyncIterator[str]:
    """Trickle a canned narrative token-by-token to mimic streaming.

    If the user message contains a fenced JSON payload (which our agents do
    when they hand a structured-data blob to narrate), we summarize it nicely.
    """
    last = (messages or [{}])[-1].get("content", "") or ""
    text = _scripted_narrative_from_json(last) if "```json" in last else (
        SCRIPTED_INTRO
        + "Coral and the data layer are working. Open the **Schema atlas** "
        "or the **Watchlists** page to explore cross-source joins without an LLM."
    )
    for ch in text:
        yield ch
        await asyncio.sleep(0.003)


# ---------------------------------------------------------------------------
# One-shot completion (non-streaming convenience)
# ---------------------------------------------------------------------------
async def acomplete_text(system: str, messages: list[dict], max_tokens: int = 800,
                         temperature: float = 0.3) -> str:
    out: list[str] = []
    async for tok in acomplete(system, messages, max_tokens, temperature):
        out.append(tok)
    return "".join(out)
