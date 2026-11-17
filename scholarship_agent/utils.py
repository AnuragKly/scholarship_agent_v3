"""Shared helpers: logging with secret redaction, URL normalisation, text and JSON utilities."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
]
_REGISTERED_SECRETS: list = []
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid"}


def register_secrets(secrets: Iterable[str]) -> None:
    """Remember secret values so :func:`redact` can mask them anywhere in text.

    Args:
        secrets: Secret strings (API keys). Short or empty values are ignored.
    """
    for s in secrets:
        if s and len(s) >= 6 and s not in _REGISTERED_SECRETS:
            _REGISTERED_SECRETS.append(s)


def redact(text: str) -> str:
    """Mask API keys and bearer tokens in ``text``.

    Args:
        text: Any string (log message, exception text).

    Returns:
        The text with secrets replaced by ``***``.
    """
    out = str(text)
    for secret in _REGISTERED_SECRETS:
        out = out.replace(secret, "***")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("***", out)
    return out


class _RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the formatted message; always keep the record."""
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # never let logging break the app
            pass
        return True


class _RedactingFormatter(logging.Formatter):
    """Formatter that also redacts exception tracebacks."""

    def formatException(self, ei) -> str:  # noqa: N802 (stdlib signature)
        """Format an exception, then redact secrets from the traceback text."""
        return redact(super().formatException(ei))


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure the ``scholarship_agent`` logger once, with redaction.

    Args:
        level: Logging level name.

    Returns:
        The configured package logger.
    """
    logger = logging.getLogger("scholarship_agent")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not any(getattr(h, "_sa_handler", False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler._sa_handler = True  # type: ignore[attr-defined]
        handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(_RedactingFilter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> str:
    """Format a datetime as an ISO-8601 string in UTC ('' for None)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def safe_error(exc: BaseException) -> str:
    """Return a short, secret-free description of an exception for the UI/logs.

    Args:
        exc: The exception.

    Returns:
        ``ClassName: message`` with secrets redacted and length capped.
    """
    return redact(f"{exc.__class__.__name__}: {exc}")[:300]


def normalize_url(url: str) -> str:
    """Normalise a URL for duplicate detection.

    Lower-cases scheme and host, strips ``www.``, default ports, fragments,
    trailing slashes and tracking parameters (``utm_*`` etc.), and sorts the
    remaining query parameters.

    Args:
        url: Any URL string.

    Returns:
        The normalised URL, or ``""`` if ``url`` is empty or unparseable.
    """
    if not url or not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "")
    if len(path) > 1:
        path = path.rstrip("/")
    query_pairs = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    # http and https versions of the same page are treated as identical.
    return urlunsplit(("https" if scheme in ("http", "https") else scheme, netloc, path, urlencode(query_pairs), ""))


def domain_of(url: str) -> str:
    """Return the lower-case host of ``url`` without a leading ``www.``."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def normalize_text_key(text: str) -> str:
    """Lower-case, strip punctuation and collapse whitespace for fuzzy matching."""
    text = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def collapse_whitespace(text: str) -> str:
    """Collapse all whitespace runs in ``text`` into single spaces."""
    return re.sub(r"\s+", " ", text or "").strip()


def to_list(value: Any) -> list:
    """Coerce a value to a list of non-empty strings.

    Args:
        value: None, a string, a list/tuple, or a JSON-encoded list string.

    Returns:
        A list of stripped, non-empty strings.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("["):
            try:
                return to_list(json.loads(value))
            except ValueError:
                pass
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def dumps(value: Any) -> str:
    """Serialise to compact JSON, keeping non-ASCII text readable."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def loads(text: Optional[str], default: Any = None) -> Any:
    """Parse JSON, returning ``default`` for empty or malformed input."""
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def rows_to_csv(rows: list, columns: list) -> str:
    """Render dictionaries to CSV text.

    Args:
        rows: List of dict rows.
        columns: Column keys, in output order. List/dict values are JSON-encoded;
            values starting with ``= + - @`` are prefixed with ``'`` to prevent
            spreadsheet formula injection.

    Returns:
        CSV text with a header row.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        out = []
        for col in columns:
            val = row.get(col, "")
            if isinstance(val, (list, dict)):
                val = "; ".join(map(str, val)) if isinstance(val, list) else dumps(val)
            val = "" if val is None else str(val)
            if val and val[0] in "=+-@":
                val = "'" + val
            out.append(val)
        writer.writerow(out)
    return buf.getvalue()
