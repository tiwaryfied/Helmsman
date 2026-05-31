#!/usr/bin/env python3
"""
Helmsman seed-data generator.

Produces a story-consistent week of JSONL data across GitHub, Linear, Slack,
Sentry, Datadog, and Google Calendar — designed so every Helmsman feature
(Morning Brief, Blocker Radar, Incident War Room, Captain's Log) lands in
the demo with realistic, *correlated* data across sources.

The narrative:
    You are @captain, a senior engineer at Coralreef Labs working on
    "Marlin" (an analytics SaaS). Yesterday a deploy broke /reports/export
    because PR #4521 mishandled cohort_id. Sentry is spiking, oncall is
    chatting, the rollback PR is open. You also have 3 PRs waiting on
    your review (oldest: 5 days), 2 of your own PRs are blocked, and
    today has 3 meetings.

Run: python3 scripts/seed_data.py
Output: ./data/<source>/<table>.jsonl
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(20260530)  # deterministic data for stable demos
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# "Now" is fixed so the demo is reproducible regardless of when you run it.
NOW = datetime(2026, 5, 30, 9, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_ago(n: float) -> str:
    return iso(NOW - timedelta(days=n))


def hours_ago(n: float) -> str:
    return iso(NOW - timedelta(hours=n))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"  wrote {len(rows):>4} rows -> {path.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# Cast of characters
# ---------------------------------------------------------------------------
TEAM = [
    ("captain",   "Aria Reyes",      "captain@coralreef.dev",   "Staff Engineer"),
    ("bosun",     "Mateo Cruz",      "bosun@coralreef.dev",     "Senior Engineer"),
    ("navigator", "Priya Shah",      "navigator@coralreef.dev", "Senior Engineer"),
    ("gunner",    "Lin Park",        "gunner@coralreef.dev",    "Engineer"),
    ("lookout",   "Sam Okafor",      "lookout@coralreef.dev",   "Engineer"),
    ("quartermaster", "Jules Bell",  "qm@coralreef.dev",        "Engineering Manager"),
]
LOGIN_TO_NAME = {l: n for l, n, _, _ in TEAM}
LOGIN_TO_EMAIL = {l: e for l, n, e, _ in TEAM}

ORG = "coralreef"
REPOS = ["marlin-api", "marlin-web", "marlin-infra"]


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
def gen_github() -> None:
    print("github/")

    # Pull requests — the central narrative anchor.
    pulls: list[dict] = []

    pulls.append({
        "number": 4521, "owner": ORG, "repo": "marlin-api",
        "title": "Refactor cohort handling for reports/export",
        "state": "merged",
        "user__login": "navigator", "user__name": LOGIN_TO_NAME["navigator"],
        "html_url": f"https://github.com/{ORG}/marlin-api/pull/4521",
        "created_at": days_ago(3.2), "updated_at": days_ago(1.0), "merged_at": days_ago(1.0),
        "closed_at": days_ago(1.0), "base_ref": "main", "head_ref": "nav/cohort-refactor",
        "additions": 412, "deletions": 318, "changed_files": 11,
        "body": "Refactors cohort_id resolution path. Moves lookup from request middleware to a per-report resolver. Linked to MAR-892.",
        "labels": ["backend", "performance"], "requested_reviewers": ["captain", "bosun"],
        "review_decision": "APPROVED",
    })

    # The hotfix PR that's open right now in response to the incident.
    pulls.append({
        "number": 4528, "owner": ORG, "repo": "marlin-api",
        "title": "fix: guard cohort_id KeyError in export pipeline",
        "state": "open",
        "user__login": "navigator", "user__name": LOGIN_TO_NAME["navigator"],
        "html_url": f"https://github.com/{ORG}/marlin-api/pull/4528",
        "created_at": hours_ago(2.5), "updated_at": hours_ago(0.3), "merged_at": None,
        "closed_at": None, "base_ref": "main", "head_ref": "nav/cohort-keyerror-hotfix",
        "additions": 24, "deletions": 6, "changed_files": 2,
        "body": "Hotfix for incident INC-2026-0530. Adds a fallback when `cohort_id` missing from legacy payloads. Adds regression test.",
        "labels": ["bug", "hotfix", "incident"], "requested_reviewers": ["captain"],
        "review_decision": "REVIEW_REQUIRED",
    })

    # 3 stale PRs waiting on the captain's review.
    stale_specs = [
        (4501, "marlin-web",  "bosun",     "Migrate dashboard filters to URL state", 5.1, "Reviewers seem busy. Bumping for visibility."),
        (4509, "marlin-api",  "gunner",    "Paginate /audit-log endpoint",            3.4, "Switches from offset to keyset pagination."),
        (4514, "marlin-infra","lookout",   "Bump terraform-aws-modules to 5.7",       2.7, "Mostly mechanical; one rds.tf cleanup."),
    ]
    for n, repo, author, title, age, body in stale_specs:
        pulls.append({
            "number": n, "owner": ORG, "repo": repo, "title": title, "state": "open",
            "user__login": author, "user__name": LOGIN_TO_NAME[author],
            "html_url": f"https://github.com/{ORG}/{repo}/pull/{n}",
            "created_at": days_ago(age), "updated_at": days_ago(age - 0.4),
            "merged_at": None, "closed_at": None,
            "base_ref": "main", "head_ref": f"{author}/feat-{n}",
            "additions": random.randint(40, 260), "deletions": random.randint(20, 120),
            "changed_files": random.randint(3, 12), "body": body,
            "labels": random.sample(["backend","frontend","infra","perf","tests","docs"], 2),
            "requested_reviewers": ["captain"],
            "review_decision": "REVIEW_REQUIRED",
        })

    # 2 of the captain's own PRs blocked on others.
    blocked_specs = [
        (4519, "marlin-api", "captain", "Introduce QueryPlan abstraction for /reports",      3.1, ["navigator","bosun"], "Splits report execution into plan + execute. Big refactor; please read the design doc linked in MAR-901."),
        (4525, "marlin-web", "captain", "Add per-cohort breakdown chart on Dashboards page", 0.8, ["bosun"], "Renders the new cohort breakdown next to the conversion funnel."),
    ]
    for n, repo, author, title, age, reviewers, body in blocked_specs:
        pulls.append({
            "number": n, "owner": ORG, "repo": repo, "title": title, "state": "open",
            "user__login": author, "user__name": LOGIN_TO_NAME[author],
            "html_url": f"https://github.com/{ORG}/{repo}/pull/{n}",
            "created_at": days_ago(age), "updated_at": days_ago(age - 0.2),
            "merged_at": None, "closed_at": None,
            "base_ref": "main", "head_ref": f"{author}/feat-{n}",
            "additions": random.randint(140, 480), "deletions": random.randint(40, 180),
            "changed_files": random.randint(6, 18), "body": body,
            "labels": ["feature","backend" if repo == "marlin-api" else "frontend"],
            "requested_reviewers": reviewers,
            "review_decision": "REVIEW_REQUIRED",
        })

    # 7 PRs the captain merged this week — for Captain's Log.
    weekly_merged = [
        (4503, "marlin-api", "captain", "Add structured logging to ingest worker",     5.6),
        (4504, "marlin-web", "captain", "Fix flaky e2e test for filter persistence",   5.0),
        (4506, "marlin-api", "captain", "Cache cohort definitions for 60s",            4.2),
        (4508, "marlin-api", "captain", "Promote /reports/run to v2 schema",           3.8),
        (4511, "marlin-web", "captain", "Dark-mode polish for dashboard cards",        3.0),
        (4516, "marlin-api", "captain", "Tighten rate limit on /api/v1/exports",       2.0),
        (4520, "marlin-infra","captain","Terraform: introduce per-env state bucket",   1.2),
    ]
    for n, repo, author, title, age in weekly_merged:
        pulls.append({
            "number": n, "owner": ORG, "repo": repo, "title": title, "state": "merged",
            "user__login": author, "user__name": LOGIN_TO_NAME[author],
            "html_url": f"https://github.com/{ORG}/{repo}/pull/{n}",
            "created_at": days_ago(age + 0.5), "updated_at": days_ago(age),
            "merged_at": days_ago(age), "closed_at": days_ago(age),
            "base_ref": "main", "head_ref": f"{author}/work-{n}",
            "additions": random.randint(30, 220), "deletions": random.randint(10, 90),
            "changed_files": random.randint(2, 9), "body": f"Routine work. See PR description on GitHub.",
            "labels": random.sample(["backend","frontend","infra","perf","tests","docs"], 2),
            "requested_reviewers": [],
            "review_decision": "APPROVED",
        })

    # A handful of background PRs by others, opened/merged earlier in the week.
    background_titles = [
        ("Bump deps for security advisory",        "lookout", "marlin-api"),
        ("Add Playwright smoke for /pricing",      "gunner",  "marlin-web"),
        ("Refactor cron scheduler to leader lock", "bosun",   "marlin-infra"),
        ("Improve onboarding email copy",          "navigator","marlin-web"),
        ("Profile slow query on segments table",   "bosun",   "marlin-api"),
    ]
    for i, (title, author, repo) in enumerate(background_titles):
        n = 4490 + i
        age = random.uniform(4.0, 7.0)
        pulls.append({
            "number": n, "owner": ORG, "repo": repo, "title": title, "state": "merged",
            "user__login": author, "user__name": LOGIN_TO_NAME[author],
            "html_url": f"https://github.com/{ORG}/{repo}/pull/{n}",
            "created_at": days_ago(age + 1), "updated_at": days_ago(age),
            "merged_at": days_ago(age), "closed_at": days_ago(age),
            "base_ref": "main", "head_ref": f"{author}/bg-{n}",
            "additions": random.randint(10, 120), "deletions": random.randint(5, 80),
            "changed_files": random.randint(1, 6), "body": "",
            "labels": ["chore"], "requested_reviewers": [], "review_decision": "APPROVED",
        })

    write_jsonl(DATA / "github" / "pulls.jsonl", pulls)

    # Normalized many-to-many: one row per (pull, requested reviewer).
    # Lets the agent write clean joins instead of fighting JSON arrays.
    pull_reviewers: list[dict] = []
    for p in pulls:
        for rv in p.get("requested_reviewers", []):
            pull_reviewers.append({
                "owner": p["owner"], "repo": p["repo"],
                "pull_number": p["number"],
                "reviewer_login": rv,
                "reviewer_name": LOGIN_TO_NAME.get(rv, rv),
                "pull_state": p["state"],
                "pull_created_at": p["created_at"],
                "pull_updated_at": p["updated_at"],
            })
    write_jsonl(DATA / "github" / "pull_reviewers.jsonl", pull_reviewers)

    # Reviews table — links pulls to reviewers, with timestamps and ages.
    reviews: list[dict] = []
    rid = 8001
    # For each stale PR, the captain has not reviewed yet -> no review row for them.
    # But there are "REQUEST_CHANGES"-style comments from others to make it lively.
    for p in pulls:
        if p["state"] == "merged" and p["user__login"] != "captain":
            # Captain reviewed and approved most of these.
            if random.random() < 0.7:
                reviews.append({
                    "id": rid, "owner": ORG, "repo": p["repo"], "pull_number": p["number"],
                    "user__login": "captain", "user__name": LOGIN_TO_NAME["captain"],
                    "state": "APPROVED", "submitted_at": p["merged_at"],
                    "body": "LGTM. Thanks for picking this up.",
                })
                rid += 1
        elif p["state"] == "merged" and p["user__login"] == "captain":
            rv = random.choice(["bosun", "navigator"])
            reviews.append({
                "id": rid, "owner": ORG, "repo": p["repo"], "pull_number": p["number"],
                "user__login": rv, "user__name": LOGIN_TO_NAME[rv],
                "state": "APPROVED", "submitted_at": p["merged_at"],
                "body": "Approved.",
            })
            rid += 1
    # And a review on PR 4521 (the offender) — captain approved it 1.2 days ago.
    reviews.append({
        "id": rid, "owner": ORG, "repo": "marlin-api", "pull_number": 4521,
        "user__login": "captain", "user__name": LOGIN_TO_NAME["captain"],
        "state": "APPROVED", "submitted_at": days_ago(1.2),
        "body": "Looks good. Watch the export pipeline closely after rollout — the cohort lookup is in the hot path.",
    })
    write_jsonl(DATA / "github" / "reviews.jsonl", reviews)

    # Issues (a few open, story-relevant).
    issues = [
        {
            "number": 312, "owner": ORG, "repo": "marlin-api",
            "title": "Customer-reported: /reports/export returns 500 since 05/29",
            "state": "open", "user__login": "bosun", "user__name": LOGIN_TO_NAME["bosun"],
            "assignee__login": "captain", "html_url": f"https://github.com/{ORG}/marlin-api/issues/312",
            "created_at": hours_ago(7.0), "updated_at": hours_ago(0.5),
            "labels": ["bug", "customer", "incident"],
            "body": "Three enterprise customers reporting export failures. Logs show KeyError 'cohort_id'. Tracking under MAR-893.",
        },
        {
            "number": 305, "owner": ORG, "repo": "marlin-web",
            "title": "Discussion: do we still need the legacy filter sidebar?",
            "state": "open", "user__login": "navigator", "user__name": LOGIN_TO_NAME["navigator"],
            "assignee__login": None, "html_url": f"https://github.com/{ORG}/marlin-web/issues/305",
            "created_at": days_ago(6.0), "updated_at": days_ago(2.4), "labels": ["discussion"],
            "body": "Following up on the dashboard refresh thread.",
        },
        {
            "number": 308, "owner": ORG, "repo": "marlin-infra",
            "title": "Plan: migrate to per-env Terraform state buckets",
            "state": "open", "user__login": "captain", "user__name": LOGIN_TO_NAME["captain"],
            "assignee__login": "captain", "html_url": f"https://github.com/{ORG}/marlin-infra/issues/308",
            "created_at": days_ago(4.0), "updated_at": days_ago(1.2), "labels": ["chore","infra"],
            "body": "Tracked under MAR-901.",
        },
    ]
    write_jsonl(DATA / "github" / "issues.jsonl", issues)

    # Commits — for the merged PRs (just key entries on main near the deploy).
    commits: list[dict] = []
    base_sha = "a1b2c3d4e5"
    for i, p in enumerate(sorted([x for x in pulls if x["state"] == "merged"],
                                  key=lambda x: x["merged_at"], reverse=True)):
        commits.append({
            "sha": f"{base_sha[:9]}{i:03d}",
            "owner": ORG, "repo": p["repo"],
            "author__login": p["user__login"], "author__name": LOGIN_TO_NAME[p["user__login"]],
            "committed_at": p["merged_at"], "message": p["title"],
            "pull_number": p["number"],
        })
    write_jsonl(DATA / "github" / "commits.jsonl", commits)

    # Repos.
    repos = []
    for r in REPOS:
        repos.append({
            "owner": ORG, "name": r, "full_name": f"{ORG}/{r}",
            "description": f"Marlin {r.split('-')[1]} service",
            "default_branch": "main", "private": True,
            "language": "Python" if r.endswith("api") else ("TypeScript" if r.endswith("web") else "HCL"),
            "stargazers_count": random.randint(2, 12), "forks_count": 0,
            "open_issues_count": random.randint(3, 9),
            "pushed_at": days_ago(0.05),
        })
    write_jsonl(DATA / "github" / "repos.jsonl", repos)


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------
def gen_linear() -> None:
    print("linear/")
    issues = [
        # The two issues that are at the center of the incident
        {
            "id": "iss_mar_892", "identifier": "MAR-892", "team_key": "MAR",
            "title": "Refactor cohort handling for /reports/export",
            "state__name": "Done", "state__type": "completed",
            "priority": 2, "priority_label": "High",
            "assignee__email": LOGIN_TO_EMAIL["navigator"], "assignee__name": LOGIN_TO_NAME["navigator"],
            "creator__email": LOGIN_TO_EMAIL["captain"], "creator__name": LOGIN_TO_NAME["captain"],
            "created_at": days_ago(9.5), "updated_at": days_ago(1.0),
            "completed_at": days_ago(1.0), "due_date": None, "cycle__name": "Cycle 42",
            "url": "https://linear.app/coralreef/issue/MAR-892",
            "description": "Cohort handling is brittle. Move resolution to dedicated module. Linked PR: marlin-api#4521.",
        },
        {
            "id": "iss_mar_893", "identifier": "MAR-893", "team_key": "MAR",
            "title": "Incident: /reports/export 500s after cohort refactor",
            "state__name": "In Progress", "state__type": "started",
            "priority": 1, "priority_label": "Urgent",
            "assignee__email": LOGIN_TO_EMAIL["navigator"], "assignee__name": LOGIN_TO_NAME["navigator"],
            "creator__email": LOGIN_TO_EMAIL["bosun"], "creator__name": LOGIN_TO_NAME["bosun"],
            "created_at": hours_ago(7.0), "updated_at": hours_ago(0.5),
            "completed_at": None, "due_date": iso(NOW), "cycle__name": "Cycle 42",
            "url": "https://linear.app/coralreef/issue/MAR-893",
            "description": "KeyError 'cohort_id' in export pipeline; rollback or hotfix decision needed. See marlin-api#4528 (hotfix open).",
        },
        # Other in-flight tickets
        {
            "id": "iss_mar_901", "identifier": "MAR-901", "team_key": "MAR",
            "title": "Design: QueryPlan abstraction for /reports",
            "state__name": "In Review", "state__type": "started",
            "priority": 2, "priority_label": "High",
            "assignee__email": LOGIN_TO_EMAIL["captain"], "assignee__name": LOGIN_TO_NAME["captain"],
            "creator__email": LOGIN_TO_EMAIL["captain"], "creator__name": LOGIN_TO_NAME["captain"],
            "created_at": days_ago(6.0), "updated_at": days_ago(0.6),
            "completed_at": None, "due_date": iso(NOW + timedelta(days=4)),
            "cycle__name": "Cycle 42",
            "url": "https://linear.app/coralreef/issue/MAR-901",
            "description": "Design doc + RFC for splitting reports into Plan + Execute. Architecture review today at 11:00.",
        },
        {
            "id": "iss_mar_888", "identifier": "MAR-888", "team_key": "MAR",
            "title": "Migrate dashboard filters to URL state",
            "state__name": "In Review", "state__type": "started",
            "priority": 3, "priority_label": "Medium",
            "assignee__email": LOGIN_TO_EMAIL["bosun"], "assignee__name": LOGIN_TO_NAME["bosun"],
            "creator__email": LOGIN_TO_EMAIL["bosun"], "creator__name": LOGIN_TO_NAME["bosun"],
            "created_at": days_ago(8.0), "updated_at": days_ago(2.0),
            "completed_at": None, "due_date": iso(NOW + timedelta(days=2)),
            "cycle__name": "Cycle 42",
            "url": "https://linear.app/coralreef/issue/MAR-888",
            "description": "Linked PR: marlin-web#4501 awaiting review.",
        },
        {
            "id": "iss_mar_870", "identifier": "MAR-870", "team_key": "MAR",
            "title": "Per-env Terraform state buckets",
            "state__name": "In Progress", "state__type": "started",
            "priority": 3, "priority_label": "Medium",
            "assignee__email": LOGIN_TO_EMAIL["captain"], "assignee__name": LOGIN_TO_NAME["captain"],
            "creator__email": LOGIN_TO_EMAIL["captain"], "creator__name": LOGIN_TO_NAME["captain"],
            "created_at": days_ago(12.0), "updated_at": days_ago(1.0),
            "completed_at": None, "due_date": iso(NOW + timedelta(days=3)),
            "cycle__name": "Cycle 42",
            "url": "https://linear.app/coralreef/issue/MAR-870",
            "description": "Tracked PR: marlin-infra#4520 (merged) + follow-up issues.",
        },
        {
            "id": "iss_mar_864", "identifier": "MAR-864", "team_key": "MAR",
            "title": "Paginate /audit-log endpoint",
            "state__name": "In Review", "state__type": "started",
            "priority": 3, "priority_label": "Medium",
            "assignee__email": LOGIN_TO_EMAIL["gunner"], "assignee__name": LOGIN_TO_NAME["gunner"],
            "creator__email": LOGIN_TO_EMAIL["quartermaster"], "creator__name": LOGIN_TO_NAME["quartermaster"],
            "created_at": days_ago(10.0), "updated_at": days_ago(1.3),
            "completed_at": None, "due_date": iso(NOW + timedelta(days=2)),
            "cycle__name": "Cycle 42",
            "url": "https://linear.app/coralreef/issue/MAR-864",
            "description": "Linked PR: marlin-api#4509.",
        },
    ]
    write_jsonl(DATA / "linear" / "issues.jsonl", issues)

    # Attachments link Linear issues to GitHub PRs.
    attachments = [
        ("iss_mar_892", "Refactor cohort handling", f"https://github.com/{ORG}/marlin-api/pull/4521"),
        ("iss_mar_893", "Hotfix PR",                 f"https://github.com/{ORG}/marlin-api/pull/4528"),
        ("iss_mar_901", "QueryPlan PR",              f"https://github.com/{ORG}/marlin-api/pull/4519"),
        ("iss_mar_888", "URL state PR",              f"https://github.com/{ORG}/marlin-web/pull/4501"),
        ("iss_mar_870", "Terraform state buckets",   f"https://github.com/{ORG}/marlin-infra/pull/4520"),
        ("iss_mar_864", "Audit log pagination",      f"https://github.com/{ORG}/marlin-api/pull/4509"),
    ]
    rows = [{
        "id": f"att_{i}", "issue_id": iid, "issue_identifier": iid.upper().replace("ISS_", "").replace("_", "-"),
        "title": title, "url": url, "created_at": days_ago(random.uniform(0.5, 8.0)),
    } for i, (iid, title, url) in enumerate(attachments)]
    write_jsonl(DATA / "linear" / "attachments.jsonl", rows)

    cycles = [{
        "id": "cyc_42", "name": "Cycle 42", "starts_at": days_ago(7), "ends_at": iso(NOW + timedelta(days=7)),
        "team_key": "MAR", "progress": 0.62,
    }]
    write_jsonl(DATA / "linear" / "cycles.jsonl", cycles)


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------
def gen_slack() -> None:
    print("slack/")
    channels = [
        {"id": "C-ONCALL",   "name": "oncall",        "is_private": False, "topic": "Production incidents"},
        {"id": "C-ENG",      "name": "engineering",   "is_private": False, "topic": "All things eng"},
        {"id": "C-MARLIN",   "name": "marlin",        "is_private": False, "topic": "Marlin product"},
        {"id": "C-RELEASES", "name": "releases",      "is_private": False, "topic": "Deploy notifications"},
        {"id": "C-CAPM",     "name": "captain-1on1",  "is_private": True,  "topic": "Aria <> Jules"},
    ]
    write_jsonl(DATA / "slack" / "channels.jsonl", channels)

    msgs: list[dict] = []
    mid = 90000

    def m(channel, user, text, hours, thread_ts=None, mentions=None):
        nonlocal mid
        ts = (NOW - timedelta(hours=hours)).timestamp()
        msgs.append({
            "id": str(mid), "channel_id": channel, "channel_name": next(c["name"] for c in channels if c["id"]==channel),
            "user__login": user, "user__name": LOGIN_TO_NAME.get(user, user),
            "ts": f"{ts:.6f}", "iso_ts": hours_ago(hours), "text": text,
            "thread_ts": thread_ts, "mentions": mentions or [],
        })
        mid += 1
        return f"{ts:.6f}"

    # Incident thread in #oncall
    root = m("C-ONCALL", "bosun",
             "Production: spike of 5xx on /api/v1/reports/export starting ~02:30 UTC. Three enterprise customers paged us. cc <@captain>",
             6.5, mentions=["captain"])
    m("C-ONCALL", "navigator", "On it. Looks like cohort_id is missing for legacy payloads after the refactor in #4521.", 6.4, thread_ts=root)
    m("C-ONCALL", "captain",   "Rolling back vs hotfix? I lean hotfix if we can land in <1h.", 6.3, thread_ts=root)
    m("C-ONCALL", "navigator", "Hotfix WIP at marlin-api#4528. ETA 30m, can <@captain> review when up?", 3.0, thread_ts=root, mentions=["captain"])
    m("C-ONCALL", "captain",   "Yes, ping me when ready.", 2.7, thread_ts=root)
    m("C-ONCALL", "navigator", "Up: https://github.com/coralreef/marlin-api/pull/4528 — please review.", 2.4, thread_ts=root)

    # Releases channel — deploy notice tied to PR 4521
    m("C-RELEASES", "bosun", "🚀 deploy marlin-api@main · pr#4521 (Refactor cohort handling) · prod · OK", 25.0)
    m("C-RELEASES", "bosun", "🚀 deploy marlin-web@main · pr#4520 (state buckets) · prod · OK", 28.0)
    m("C-RELEASES", "bosun", "🚀 deploy marlin-api@main · pr#4516 (rate limit) · prod · OK", 47.0)

    # Engineering chatter
    m("C-ENG", "quartermaster", "Reminder: cycle 42 review on Friday. Please update Linear by EOD Wed.", 22.0)
    m("C-ENG", "lookout",       "<@captain> can you take a look at marlin-infra#4514 when you have a sec?", 50.0, mentions=["captain"])
    m("C-ENG", "gunner",        "Anyone seen the audit-log perf charts? My PR speeds it up but I want a second opinion.", 70.0)
    m("C-ENG", "captain",       "Quick sanity check: Q for the team — is anyone still relying on the legacy filter sidebar in the dashboard?", 30.0)
    m("C-ENG", "navigator",     "Nope, safe to remove imo.", 29.5)

    # 1:1 channel — context to surface gently in Captain's Log
    m("C-CAPM", "quartermaster", "Heads up: I'd like to talk about scope for cycle 43 in our 1:1 today.", 5.0, mentions=["captain"])
    m("C-CAPM", "quartermaster", "Also, great execution on the export rate-limit work this week.", 5.0)

    write_jsonl(DATA / "slack" / "messages.jsonl", msgs)

    # Normalized mentions: one row per (message, mentioned_login).
    mention_rows = []
    for msg in msgs:
        for who in msg.get("mentions", []):
            mention_rows.append({
                "message_id": msg["id"],
                "channel_id": msg["channel_id"],
                "channel_name": msg["channel_name"],
                "iso_ts": msg["iso_ts"],
                "mentioned_login": who,
                "mentioned_name": LOGIN_TO_NAME.get(who, who),
                "author_login": msg["user__login"],
                "text": msg["text"],
            })
    write_jsonl(DATA / "slack" / "message_mentions.jsonl", mention_rows)


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------
def gen_sentry() -> None:
    print("sentry/")
    issues = [
        {
            "id": "SENTRY-7741", "short_id": "MARLIN-API-7741",
            "title": "KeyError: 'cohort_id'",
            "culprit": "reports.export.run_export",
            "level": "error", "status": "unresolved",
            "first_seen": hours_ago(6.6), "last_seen": hours_ago(0.2),
            "count": 1842, "user_count": 137,
            "project_slug": "marlin-api", "permalink": "https://sentry.example/coralreef/marlin-api/issues/7741/",
            "release": "marlin-api@2026.05.29-1",
            "tags__pr": "4521", "tags__route": "/api/v1/reports/export",
            "stack_top": "reports/export.py:172  payload['cohort_id']  -> KeyError",
        },
        {
            "id": "SENTRY-7702", "short_id": "MARLIN-WEB-7702",
            "title": "TypeError: Cannot read properties of undefined (reading 'breakdown')",
            "culprit": "components.DashboardCard.useEffect",
            "level": "error", "status": "unresolved",
            "first_seen": days_ago(2.0), "last_seen": hours_ago(3.2),
            "count": 87, "user_count": 22,
            "project_slug": "marlin-web", "permalink": "https://sentry.example/coralreef/marlin-web/issues/7702/",
            "release": "marlin-web@2026.05.28-1",
            "tags__pr": "4511", "tags__route": "/dashboard",
            "stack_top": "DashboardCard.tsx:142  data.breakdown.map(...)  -> TypeError",
        },
        {
            "id": "SENTRY-7610", "short_id": "MARLIN-API-7610",
            "title": "TimeoutError: psycopg2 statement timeout",
            "culprit": "reports.segments.query",
            "level": "warning", "status": "ignored",
            "first_seen": days_ago(11.0), "last_seen": days_ago(2.0),
            "count": 14, "user_count": 6,
            "project_slug": "marlin-api", "permalink": "https://sentry.example/coralreef/marlin-api/issues/7610/",
            "release": "marlin-api@2026.05.26-1",
            "tags__pr": None, "tags__route": "/api/v1/segments",
            "stack_top": "segments/query.py:88  cur.execute(sql)  -> TimeoutError",
        },
    ]
    write_jsonl(DATA / "sentry" / "issues.jsonl", issues)


# ---------------------------------------------------------------------------
# Datadog
# ---------------------------------------------------------------------------
def gen_datadog() -> None:
    print("datadog/")
    # Simulated metric points for the past 8 hours on the affected route.
    rows = []
    base_rate_4xx = 0.4
    for h in range(0, 8 * 12):  # 5-min buckets
        minutes_back = h * 5
        spike = 0
        # Spike begins ~6.5h ago, decays after hotfix not deployed yet.
        if 60 <= minutes_back <= 6.5 * 60:
            spike = 7.8 + random.uniform(-1.5, 1.5)
        rate_4xx = base_rate_4xx + spike
        rows.append({
            "metric": "http.requests.4xx_rate",
            "service": "marlin-api",
            "route": "/api/v1/reports/export",
            "value_pct": round(rate_4xx, 2),
            "timestamp": iso(NOW - timedelta(minutes=minutes_back)),
        })
    write_jsonl(DATA / "datadog" / "metrics.jsonl", rows)

    monitors = [
        {
            "id": 11023, "name": "marlin-api 4xx rate > 3% (5m)",
            "status": "Alert", "service": "marlin-api",
            "query": "avg(last_5m):sum:trace.http.request{service:marlin-api,http.status_code:4*}.as_rate() > 0.03",
            "tags": ["service:marlin-api","team:marlin","severity:high"],
            "modified_at": hours_ago(6.2),
        },
        {
            "id": 11050, "name": "marlin-web JS error rate > 1%/m",
            "status": "OK", "service": "marlin-web",
            "query": "avg(last_5m):...", "tags": ["service:marlin-web","team:marlin"],
            "modified_at": days_ago(1.4),
        },
    ]
    write_jsonl(DATA / "datadog" / "monitors.jsonl", monitors)


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------
def gen_calendar() -> None:
    print("calendar/")
    today = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    events = [
        {
            "id": "evt_standup_530", "summary": "Daily standup — Marlin",
            "start_at": iso(today.replace(hour=9, minute=30)),
            "end_at":   iso(today.replace(hour=9, minute=45)),
            "attendees": ["captain","bosun","navigator","gunner","lookout","quartermaster"],
            "organizer": "quartermaster", "location": "Zoom",
            "description": "Daily Marlin standup.",
        },
        {
            "id": "evt_archreview_530", "summary": "Architecture review — QueryPlan RFC",
            "start_at": iso(today.replace(hour=11, minute=0)),
            "end_at":   iso(today.replace(hour=12, minute=0)),
            "attendees": ["captain","bosun","navigator","quartermaster"],
            "organizer": "captain", "location": "Zoom",
            "description": "RFC review for MAR-901. Bring questions about plan vs execute split.",
        },
        {
            "id": "evt_1on1_530", "summary": "1:1 — Aria <> Jules",
            "start_at": iso(today.replace(hour=15, minute=0)),
            "end_at":   iso(today.replace(hour=15, minute=30)),
            "attendees": ["captain","quartermaster"],
            "organizer": "quartermaster", "location": "Zoom",
            "description": "Weekly 1:1. Talk cycle 43 scope.",
        },
        {
            "id": "evt_cycle_review", "summary": "Cycle 42 review",
            "start_at": iso((today + timedelta(days=2)).replace(hour=14, minute=0)),
            "end_at":   iso((today + timedelta(days=2)).replace(hour=15, minute=0)),
            "attendees": ["captain","bosun","navigator","gunner","lookout","quartermaster"],
            "organizer": "quartermaster", "location": "Zoom",
            "description": "Cycle 42 retro + cycle 43 planning.",
        },
        {
            "id": "evt_focus_530", "summary": "Focus block — review queue",
            "start_at": iso(today.replace(hour=13, minute=0)),
            "end_at":   iso(today.replace(hour=14, minute=30)),
            "attendees": ["captain"],
            "organizer": "captain", "location": "",
            "description": "No meetings. Clear review backlog.",
        },
    ]
    write_jsonl(DATA / "calendar" / "events.jsonl", events)

    # Normalized attendees: one row per (event, attendee).
    attendees = []
    for e in events:
        for who in e["attendees"]:
            attendees.append({
                "event_id": e["id"], "summary": e["summary"],
                "start_at": e["start_at"], "end_at": e["end_at"],
                "attendee_login": who,
                "attendee_name": LOGIN_TO_NAME.get(who, who),
                "organizer": e["organizer"],
            })
    write_jsonl(DATA / "calendar" / "event_attendees.jsonl", attendees)


def main() -> None:
    print(f"Seeding Helmsman demo data into {DATA}/ (fixed clock = {iso(NOW)})")
    gen_github()
    gen_linear()
    gen_slack()
    gen_sentry()
    gen_datadog()
    gen_calendar()
    print("Done.")


if __name__ == "__main__":
    main()
