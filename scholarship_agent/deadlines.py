"""Deadline normalisation and status tracking (Asia/Kathmandu for display).

Rules enforced here:
* A missing or unparseable deadline is ``Unknown`` - never ``Open``.
* A deadline in the past is ``Passed`` - never ``Open``.
* If the source states no timezone, the *earliest possible* interpretation (end of
  day at UTC+14) is used, so the app never says "open" when it might have closed.
* Conflicting dates are flagged, and the earliest date drives the status.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import APP_TIMEZONE

STATUS_OPEN = "Open"
STATUS_CLOSING_SOON = "Closing soon"
STATUS_PASSED = "Passed"
STATUS_UNKNOWN = "Unknown"
ALL_STATUSES = [STATUS_OPEN, STATUS_CLOSING_SOON, STATUS_PASSED, STATUS_UNKNOWN]

_ABBREVIATIONS = {
    "UTC": 0, "GMT": 0, "Z": 0, "CET": 60, "CEST": 120, "EET": 120, "EEST": 180, "WET": 0, "WEST": 60,
    "EST": -300, "EDT": -240, "PST": -480, "PDT": -420, "MST": -420, "MDT": -360, "CDT": -300,
    "JST": 540, "KST": 540, "NPT": 345, "SGT": 480, "HKT": 480, "AEST": 600, "AEDT": 660, "MSK": 180,
    "NZST": 720, "NZDT": 780,
}  # CST, IST and BST are deliberately omitted: each names more than one timezone.

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?\s*(Z|[+-]\d{2}:?\d{2})?$"
)
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DMY_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})\.?,?\s+(\d{{4}})\b", re.I)
_MDY_RE = re.compile(rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_ISO_FIND_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


@dataclass
class DeadlineInfo:
    """The computed deadline picture for one opportunity.

    Attributes:
        status: One of :data:`ALL_STATUSES`.
        deadline_utc: Normalised deadline instant in UTC, or None.
        local_display: Deadline shown in Asia/Kathmandu, or '' if unknown.
        original_text: The deadline wording exactly as extracted.
        timezone_label: The timezone wording as extracted.
        days_left: Whole days until the deadline (negative if passed), or None.
        flags: Human-readable warnings (conflicts, assumed timezone, ...).
        cycle: Application cycle/intake year if one was extracted.
        conflict: True if sources disagree on the date.
        timezone_assumed: True if no usable timezone was stated.
    """

    status: str = STATUS_UNKNOWN
    deadline_utc: Optional[datetime] = None
    local_display: str = ""
    original_text: str = ""
    timezone_label: str = ""
    days_left: Optional[int] = None
    flags: list = field(default_factory=list)
    cycle: str = ""
    conflict: bool = False
    timezone_assumed: bool = False


def parse_timezone(label: str) -> Optional[tzinfo]:
    """Turn a timezone description into a ``tzinfo``.

    Supports IANA names (``Europe/Berlin``), ``UTC``/``GMT`` with optional offsets
    (``UTC+1``, ``GMT-05:30``) and unambiguous abbreviations (``CET``, ``JST``).

    Args:
        label: Timezone wording from the source.

    Returns:
        A ``tzinfo``, or None if empty, unknown or ambiguous (e.g. ``IST``).
    """
    label = (label or "").strip()
    if not label:
        return None
    try:
        return ZoneInfo(label)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        pass
    m = re.fullmatch(r"(?i)(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", label.replace("−", "-"))
    if m:
        sign = -1 if m.group(1) == "-" else 1
        minutes = int(m.group(2)) * 60 + int(m.group(3) or 0)
        if minutes <= 14 * 60:
            return timezone(timedelta(minutes=sign * minutes))
        return None
    key = label.upper().strip("() ")
    if key in _ABBREVIATIONS:
        return timezone(timedelta(minutes=_ABBREVIATIONS[key]))
    return None


def _parse_offset(text: str) -> tzinfo:
    """Parse an ISO offset such as ``Z`` or ``+05:45`` into a ``tzinfo``."""
    if text == "Z":
        return timezone.utc
    sign = -1 if text[0] == "-" else 1
    digits = text[1:].replace(":", "")
    return timezone(timedelta(minutes=sign * (int(digits[:2]) * 60 + int(digits[2:] or 0))))


def parse_iso_deadline(value: str) -> Optional[tuple]:
    """Parse an ISO date or datetime string.

    Args:
        value: e.g. ``2027-01-15``, ``2027-01-15T23:59`` or ``2027-01-15T23:59+01:00``.

    Returns:
        ``(date, time_or_None, tzinfo_or_None)`` or None when invalid.
    """
    m = _ISO_RE.match((value or "").strip())
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        t = None
        if m.group(4) is not None:
            t = time(int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
    except ValueError:
        return None
    tz = _parse_offset(m.group(7)) if m.group(7) else None
    return d, t, tz


def find_dates_in_text(text: str) -> list:
    """Find unambiguous full dates inside free text.

    Only formats with a month *name* or ISO ``YYYY-MM-DD`` are recognised;
    ambiguous numeric forms like ``03/04/2027`` are deliberately ignored.

    Args:
        text: Source text.

    Returns:
        A list of ``(iso_date, surrounding_snippet)`` tuples in order of appearance.
    """
    found: list = []

    def add(y: int, mo: int, d: int, start: int, end: int) -> None:
        try:
            iso_date = date(y, mo, d).isoformat()
        except ValueError:
            return
        snippet = re.sub(r"\s+", " ", text[max(0, start - 80): end + 40]).strip()
        found.append((start, iso_date, snippet))

    for m in _DMY_RE.finditer(text):
        add(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)), m.start(), m.end())
    for m in _MDY_RE.finditer(text):
        add(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)), m.start(), m.end())
    for m in _ISO_FIND_RE.finditer(text):
        add(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.start(), m.end())
    found.sort(key=lambda x: x[0])
    return [(iso_date, snip) for _, iso_date, snip in found]


def _resolve_instant(value: str, tz_label: str) -> Optional[tuple]:
    """Convert an ISO deadline string and timezone wording into a UTC instant.

    Returns:
        ``(utc_datetime, timezone_assumed)`` or None if ``value`` is not a valid ISO date.
    """
    parsed = parse_iso_deadline(value)
    if parsed is None:
        return None
    d, t, explicit_tz = parsed
    tz = explicit_tz or parse_timezone(tz_label)
    assumed = tz is None
    if tz is None:
        tz = timezone(timedelta(hours=14))  # earliest possible zone: never over-claims "open"
    local = datetime.combine(d, t if t is not None else time(23, 59, 59), tzinfo=tz)
    return local.astimezone(timezone.utc), assumed


def compute_deadline(
    record: dict,
    now: Optional[datetime] = None,
    closing_soon_days: int = 30,
    other_dates: Optional[list] = None,
) -> DeadlineInfo:
    """Compute deadline status for a record.

    Args:
        record: A structured record (uses ``deadline_date``, ``deadline_timezone``,
            ``application_deadline`` and ``application_cycle``).
        now: Current time (UTC-aware); defaults to the real clock. Injectable for tests.
        closing_soon_days: Deadlines within this many days are ``Closing soon``.
        other_dates: ISO dates from other sources. If they differ from the record's
            date the result is flagged as conflicting and the earliest date is used.

    Returns:
        A :class:`DeadlineInfo`. Status is ``Unknown`` when no valid date exists.
    """
    now = now or datetime.now(timezone.utc)
    info = DeadlineInfo(
        original_text=(record.get("application_deadline") or "").strip(),
        timezone_label=(record.get("deadline_timezone") or "").strip(),
        cycle=(record.get("application_cycle") or "").strip(),
    )
    candidates = []
    main = (record.get("deadline_date") or "").strip()
    if main:
        candidates.append(main)
    for extra in other_dates or []:
        extra = (extra or "").strip()
        if extra:
            candidates.append(extra)

    resolved = []
    for value in candidates:
        res = _resolve_instant(value, info.timezone_label)
        if res is None:
            info.flags.append(f"Deadline value '{value}' is not a valid date and was ignored.")
        else:
            resolved.append((value, res[0], res[1]))

    if not resolved:
        info.status = STATUS_UNKNOWN
        if info.original_text:
            info.flags.append("A deadline was mentioned but could not be turned into an exact date. Check the source.")
        else:
            info.flags.append("No deadline found. The opportunity is NOT assumed to be open.")
        return info

    distinct = {v for v, _, _ in resolved}
    if len(distinct) > 1:
        info.conflict = True
        info.flags.append(
            "Sources give different deadlines (" + ", ".join(sorted(distinct)) + "). "
            "The earliest is used for status; confirm which date applies to your programme."
        )
    _, instant, assumed = min(resolved, key=lambda r: r[1])
    info.timezone_assumed = assumed
    if assumed:
        info.flags.append("No usable timezone stated; the earliest possible interpretation (UTC+14) is used.")
    info.deadline_utc = instant
    info.local_display = instant.astimezone(ZoneInfo(APP_TIMEZONE)).strftime("%d %b %Y, %H:%M") + " (Nepal time)"
    delta = instant - now
    info.days_left = math.floor(delta.total_seconds() / 86400)
    if instant < now:
        info.status = STATUS_PASSED
    elif delta <= timedelta(days=closing_soon_days):
        info.status = STATUS_CLOSING_SOON
    else:
        info.status = STATUS_OPEN
    return info
