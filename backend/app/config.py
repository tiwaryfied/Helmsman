"""Runtime configuration for Helmsman."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root (one dir above /backend).
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / "backend" / ".env")  # secondary location


def _detect_coral_bin() -> str:
    """Return an absolute path to the coral binary, preferring $CORAL_BIN."""
    explicit = os.getenv("CORAL_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    which = shutil.which("coral")
    if which:
        return which
    candidate = Path.home() / ".local" / "bin" / "coral"
    if candidate.exists():
        return str(candidate)
    return "coral"  # last resort; calls will fail gracefully


@dataclass(frozen=True)
class Settings:
    coral_bin: str
    mode: str                     # "demo" or "live"
    captain_login: str
    captain_email: str
    anthropic_api_key: str | None
    anthropic_model: str
    openai_api_key: str | None
    openai_model: str
    gemini_api_key: str | None
    gemini_model: str
    llm_provider_pref: str         # "auto" | "gemini" | "anthropic" | "openai"
    port: int
    repo_root: Path

    @property
    def has_llm(self) -> bool:
        return bool(
            self.anthropic_api_key or self.openai_api_key or self.gemini_api_key
        )

    @property
    def schemas(self) -> dict[str, str]:
        """Map of canonical source -> live SQL schema name based on mode."""
        if self.mode == "live":
            return {
                "github": "github",     "linear": "linear",
                "slack": "slack",       "sentry": "sentry",
                "datadog": "datadog",   "calendar": "google_calendar",
            }
        return {
            "github": "github_demo",     "linear": "linear_demo",
            "slack": "slack_demo",       "sentry": "sentry_demo",
            "datadog": "datadog_demo",   "calendar": "calendar_demo",
        }


def load() -> Settings:
    return Settings(
        coral_bin=_detect_coral_bin(),
        mode=(os.getenv("HELMSMAN_MODE") or "demo").lower(),
        captain_login=os.getenv("HELMSMAN_CAPTAIN", "captain"),
        captain_email=os.getenv("HELMSMAN_CAPTAIN_EMAIL", "captain@coralreef.dev"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("HELMSMAN_ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("HELMSMAN_OPENAI_MODEL", "gpt-4o-mini"),
        gemini_api_key=(
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or None
        ),
        gemini_model=os.getenv("HELMSMAN_GEMINI_MODEL", "gemini-2.5-flash"),
        llm_provider_pref=(os.getenv("HELMSMAN_LLM_PROVIDER") or "auto").lower(),
        port=int(os.getenv("HELMSMAN_PORT", "8787")),
        repo_root=REPO_ROOT,
    )


SETTINGS = load()
