"""Configuration loading and validation.

Secrets (API keys) are read only from environment variables, optionally
populated from a ``.env`` file. Applicant details do NOT live here; they are
stored in SQLite and edited in the Settings page.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_TIMEZONE = "Asia/Kathmandu"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_DB_RELATIVE = Path("data") / "scholarships_v3.db"


@dataclass(frozen=True)
class ConfigIssue:
    """A problem or warning found while validating configuration.

    Attributes:
        level: Either ``"error"`` (app cannot work as configured) or ``"warning"``.
        message: Human-readable, actionable explanation. Never contains secrets.
    """

    level: str
    message: str


@dataclass(frozen=True)
class Config:
    """Immutable application configuration.

    Secret fields use ``repr=False`` so they never appear in logs or tracebacks
    that print the object.
    """

    openai_api_key: str = field(default="", repr=False)
    openai_model: str = DEFAULT_MODEL
    tavily_api_key: str = field(default="", repr=False)
    db_path: Path = PROJECT_ROOT / DEFAULT_DB_RELATIVE
    log_level: str = "INFO"
    fetch_timeout_seconds: float = 15.0
    max_response_bytes: int = 2_000_000
    max_page_chars: int = 60_000
    closing_soon_days: int = 30
    issues: tuple = ()

    @property
    def has_openai(self) -> bool:
        """Return True when an OpenAI API key is configured."""
        return bool(self.openai_api_key.strip())

    @property
    def has_search(self) -> bool:
        """Return True when a Tavily search API key is configured."""
        return bool(self.tavily_api_key.strip())

    def secrets(self) -> list:
        """Return the non-empty secret values, used only for log redaction."""
        return [s for s in (self.openai_api_key, self.tavily_api_key) if s.strip()]


def _parse_number(raw: Optional[str], default, cast, minimum, name: str, issues: list):
    """Parse a numeric environment value, falling back to a default with a warning.

    Args:
        raw: Raw string from the environment (may be None or empty).
        default: Value used when ``raw`` is missing or invalid.
        cast: ``int`` or ``float``.
        minimum: Smallest acceptable value.
        name: Variable name, used in warning text.
        issues: List that receives a ``ConfigIssue`` when the value is rejected.

    Returns:
        The parsed number or ``default``.
    """
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw.strip())
    except ValueError:
        issues.append(ConfigIssue("warning", f"{name} is not a valid number; using default {default}."))
        return default
    if value < minimum:
        issues.append(ConfigIssue("warning", f"{name} must be at least {minimum}; using default {default}."))
        return default
    return value


def load_config(env: Optional[Mapping[str, str]] = None, load_dotenv_file: bool = True) -> Config:
    """Build a :class:`Config` from environment variables.

    Args:
        env: Optional mapping to read instead of ``os.environ`` (used by tests).
        load_dotenv_file: When True and ``env`` is None, load ``.env`` from the
            project root first (existing real environment variables win).

    Returns:
        A validated ``Config``. Invalid optional values fall back to defaults and
        are reported in ``Config.issues``; this function does not raise.
    """
    if env is None:
        if load_dotenv_file:
            try:
                from dotenv import load_dotenv

                dotenv_path = PROJECT_ROOT / ".env"
                if dotenv_path.exists():
                    load_dotenv(dotenv_path, override=False)
            except ImportError:  # python-dotenv is optional at runtime
                pass
        env = os.environ

    issues: list = []
    db_raw = (env.get("SCHOLARSHIP_DB_PATH") or "").strip()
    db_path = Path(db_raw).expanduser() if db_raw else PROJECT_ROOT / DEFAULT_DB_RELATIVE
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    level = (env.get("LOG_LEVEL") or "INFO").strip().upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        issues.append(ConfigIssue("warning", "LOG_LEVEL is invalid; using INFO."))
        level = "INFO"

    cfg = Config(
        openai_api_key=(env.get("OPENAI_API_KEY") or "").strip(),
        openai_model=(env.get("OPENAI_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        tavily_api_key=(env.get("TAVILY_API_KEY") or "").strip(),
        db_path=db_path,
        log_level=level,
        fetch_timeout_seconds=_parse_number(
            env.get("FETCH_TIMEOUT_SECONDS"), 15.0, float, 1.0, "FETCH_TIMEOUT_SECONDS", issues
        ),
        max_response_bytes=_parse_number(
            env.get("MAX_RESPONSE_BYTES"), 2_000_000, int, 10_000, "MAX_RESPONSE_BYTES", issues
        ),
        max_page_chars=_parse_number(env.get("MAX_PAGE_CHARS"), 60_000, int, 1_000, "MAX_PAGE_CHARS", issues),
        closing_soon_days=_parse_number(env.get("CLOSING_SOON_DAYS"), 30, int, 1, "CLOSING_SOON_DAYS", issues),
        issues=tuple(issues),
    )
    return cfg


def validate_config(cfg: Config) -> list:
    """Return all configuration issues, including missing-key warnings.

    Missing API keys are warnings, not errors: the app still runs (fallback
    extraction, manual URL research) but says clearly what is unavailable.

    Args:
        cfg: The configuration to inspect.

    Returns:
        A list of :class:`ConfigIssue`. Messages never include secret values.
    """
    issues = list(cfg.issues)
    if not cfg.has_openai:
        issues.append(
            ConfigIssue(
                "warning",
                "OPENAI_API_KEY is not set. AI extraction is disabled; only a clearly labelled "
                "heuristic fallback will be available. Add the key to your .env file.",
            )
        )
    if not cfg.has_search:
        issues.append(
            ConfigIssue(
                "warning",
                "TAVILY_API_KEY is not set. Search is disabled; you can still paste an official URL "
                "manually on the Discover page.",
            )
        )
    try:
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(cfg.db_path.parent, os.W_OK):
            issues.append(ConfigIssue("error", f"Database folder is not writable: {cfg.db_path.parent}"))
    except OSError as exc:
        issues.append(ConfigIssue("error", f"Cannot create database folder: {exc.__class__.__name__}"))
    return issues
