# Helmsman

> An agentic command center for engineers, powered by [Coral](https://github.com/withcoral/coral).

Helmsman unifies GitHub, Linear, Slack, Sentry, Datadog, and Google Calendar
behind Coral's local-first SQL runtime, and lets a single agent plan, query,
join, and reason across all of them in one conversation. Ask in plain English;
the agent discovers the schema, plans cross-source joins, runs them through
Coral, and narrates the result — with every query, timing, and cache hit
visible on screen.

Built for the **Pirates of the Coral Bean** hackathon (WeMakeDevs, 2026).

---

## Why it is different

Most "AI productivity" demos call one tool at a time, then stitch JSON together
inside the prompt. Helmsman does not. Every feature relies on **Coral's
cross-source SQL joins** — the capability that lets an agent reason across data
sources in a single query, with caching, schema learning, and no glue code.

> "Across all tasks, the model was 20% more accurate and 2x more cost efficient
> using Coral than using direct provider MCPs." — Coral benchmark report

Helmsman is what that productivity gain looks like as a product.

---

## Features

| Page | What it does | Coral capability on display |
| --- | --- | --- |
| **Brief** | One narrative briefing across calendar, PRs, tickets, and Slack mentions | Six parallel queries, multi-source joins |
| **Ask** | Streaming natural language to multi-source SQL with visible agent reasoning | `coral.tables` schema discovery + multi-step planning |
| **Blockers** | PRs waiting on you and PRs you are waiting on, ranked by staleness | Self-join on `pulls` + `pull_reviewers` |
| **Incident** | Type a symptom; the agent correlates Sentry, deploys, tickets, Slack, Datadog | Multi-hop reasoning across 5+ tables |
| **Atlas** | Catalog browser + SQL playground with cache-hit timings | Live `coral.tables` / `coral.columns` views |
| **Digest** | An auto-generated weekly update for your manager | Aggregation across three sources, model-narrated |
| **Watchlists** | Save and schedule recurring multi-source queries with alert rules | Cached `coral sql` execution as cron-like jobs |

---

## Architecture

```
                  +----------------+
   Next.js UI --->|  FastAPI       |--+
   (port 3000)    |  (port 8787)   |  |
                  +-------+--------+  |
                          | subprocess|
                          v           |
                   +--------------+   |
                   |  coral sql   |   |   100% local
                   |  coral CLI   |   |   credentials never leave
                   +-------+------+   |   the machine
                           |          |
        +----------+-------+----------+--------+
        v          v       v                   v
     GitHub     Linear   Slack ...         Local JSONL
     (live)     (live)   (live)            (demo mode)
```

- **Backend**: Python 3.12, FastAPI, SSE streaming, SQLite for app state.
- **Agent**: provider-agnostic — Gemini, Anthropic, or OpenAI — with a
  discover -> plan -> query -> reflect loop. No model training involved; this
  is a product built on top of existing LLM APIs and Coral.
- **Frontend**: Next.js 14, Tailwind CSS, a custom component library, Recharts,
  and a CSS-only motion layer (scroll reveals, route transitions).
- **Coral**: subprocess shell-out to `coral sql --format json`.
- **Demo mode**: six Coral file-backed source specs over seeded JSONL, so the
  product is fully explorable with zero personal access tokens.

---

## Quickstart

```bash
# 1. Install Coral (one-time, ~190MB)
curl -fsSL https://withcoral.com/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# 2. Install Helmsman's demo Coral sources
./scripts/install_demo_sources.sh

# 3. Set an LLM key (any one of Gemini / Anthropic / OpenAI)
cp .env.example .env
# edit .env and set GEMINI_API_KEY=...

# 4. Start backend + frontend
./scripts/start.sh
```

Open `http://localhost:3000`.

The agent works with any one of the three providers. If no key is set, the app
still runs: it shows real Coral query results with a structured fallback
summary in place of the model narrative.

### LLM provider selection

Keys are read from `.env`. If multiple are present, the order is
`Gemini > Anthropic > OpenAI`. Override with `HELMSMAN_LLM_PROVIDER`:

```bash
GEMINI_API_KEY=...                  # default model: gemini-2.5-flash
HELMSMAN_LLM_PROVIDER=gemini        # auto | gemini | anthropic | openai
```

### Going live (real data)

```bash
GITHUB_TOKEN=ghp_xxx   coral source add github
LINEAR_API_KEY=lin_xxx coral source add linear
SLACK_BOT_TOKEN=xoxb_xxx coral source add slack
# ...etc
```

Set `HELMSMAN_MODE=live` in `.env` and restart the backend. The same queries
run against the live schemas.

---

## Deployment

The frontend is a static Next.js build and includes a **showcase mode**: when
no backend is reachable (for example on a static host), it serves pre-recorded
Coral responses from `frontend/public/showcase/` so the deployed URL stays fully
interactive. Set `NEXT_PUBLIC_HELMSMAN_SHOWCASE=1` to force it on.

For a live backend, containerize the FastAPI service together with the Coral
binary and the seeded demo sources, and point the frontend's `/api` proxy at it.

---

## Hackathon eligibility

- Star the [Coral repo](https://github.com/withcoral/coral).
- Join the [Coral community](https://withcoral.com).
- Demo video: _link after recording._
- Live demo: _link after deploy._

---

## Credits

- [Coral](https://withcoral.com) — the unified SQL data layer.
- [Next.js](https://nextjs.org), [Tailwind CSS](https://tailwindcss.com),
  [Recharts](https://recharts.org), [Lucide](https://lucide.dev).

---

## License

Apache 2.0 — same as Coral.
