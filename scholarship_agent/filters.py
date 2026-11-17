"""Filtering, sorting, summary statistics and CSV export for saved opportunities.

All filters combine with AND. Missing values become an explicit 'Unknown' category
(never silently dropped), so a user can filter *for* unknowns as well.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .changes import display_value
from .deadlines import STATUS_CLOSING_SOON, compute_deadline
from .utils import rows_to_csv

UNKNOWN = "Unknown"
EXPORT_COLUMNS = [
    "id", "name", "provider", "country", "degree_level", "funding_type", "funding_class", "subjects",
    "programmes", "deadline_status", "deadline_date", "deadline_local", "deadline_original", "application_cycle",
    "nepal_eligibility", "overall_eligibility", "match_score", "research_status", "needs_review", "source_type",
    "source_domain", "official_url", "extraction_method", "retrieved_at", "verified_at", "updated_at",
]
SORT_OPTIONS = {
    "Match score": "match_score",
    "Deadline": "deadline_date",
    "Name": "name",
    "Country": "country",
    "Recently updated": "updated_at",
    "Recently added": "created_at",
}
_FULL_PARTIAL = {"fully_funded": "Full", "partially_funded": "Partial", "tuition_only": "Partial", "stipend_only": "Partial"}


def enrich(opps: list, now: Optional[datetime] = None, closing_soon_days: int = 30) -> list:
    """Add computed, display-ready fields to saved opportunities.

    Computed at read time so deadline status is always current (never stored stale).

    Args:
        opps: Opportunities from the repository.
        now: Current time (UTC-aware); injectable for tests.
        closing_soon_days: Threshold for 'Closing soon'.

    Returns:
        New dicts with ``deadline_status``, ``deadline_local``, ``days_left``,
        ``deadline_flags``, ``funding_class``, ``subjects`` and ``programmes`` added.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for o in opps:
        conflict_dates = []
        for c in o.get("conflicts", []):
            if c.get("field") == "deadline_date":
                conflict_dates += [v["value"] for v in c.get("values", [])]
        info = compute_deadline(o.get("record", {}), now, closing_soon_days, conflict_dates)
        e = dict(o)
        e.update({
            "deadline_status": info.status,
            "deadline_local": info.local_display,
            "days_left": info.days_left,
            "deadline_flags": info.flags,
            "deadline_conflict": info.conflict,
            "funding_class": _FULL_PARTIAL.get(o.get("funding_type", "unknown"), UNKNOWN),
            "subjects": display_value(o.get("subject_areas", [])),
            "programmes": display_value(o.get("eligible_programmes", [])),
        })
        out.append(e)
    return out


def facet(opp: dict, field: str) -> list:
    """Return the filterable values of ``field`` for one opportunity (never empty).

    Empty, 'unknown' and 'not_stated' values are mapped to the 'Unknown' category.
    """
    if field == "subjects":
        vals = opp.get("subject_areas", []) or []
        return [str(v) for v in vals] or [UNKNOWN]
    value = opp.get(field, "")
    if value is None or str(value).strip().lower() in ("", "unknown", "not_stated"):
        return [UNKNOWN]
    return [str(value)]


def facet_options(opps: list, field: str) -> list:
    """Return sorted distinct facet values present in ``opps`` ('Unknown' last)."""
    values = {v for o in opps for v in facet(o, field)}
    known = sorted(v for v in values if v != UNKNOWN)
    return known + ([UNKNOWN] if UNKNOWN in values else [])


def apply_filters(opps: list, criteria: dict) -> list:
    """Filter and sort enriched opportunities.

    Args:
        opps: Output of :func:`enrich`.
        criteria: Optional keys - ``countries``, ``providers``, ``subjects``, ``degree_levels``,
            ``funding_types``, ``funding_classes``, ``deadline_statuses``, ``nepal_eligibility``,
            ``overall_eligibility``, ``research_statuses``, ``source_types`` (lists of facet values;
            empty/missing = no filter); ``date_from``/``date_to`` (ISO dates) with
            ``include_unknown_dates`` (default True); ``min_score``/``max_score``; ``text``;
            ``needs_review_only``; ``sort_by`` (a :data:`SORT_OPTIONS` value) and ``descending``.

    Returns:
        The matching opportunities in the requested order.
    """
    mapping = [
        ("countries", "country"), ("providers", "provider"), ("subjects", "subjects"),
        ("degree_levels", "degree_level"), ("funding_types", "funding_type"),
        ("funding_classes", "funding_class"), ("deadline_statuses", "deadline_status"),
        ("nepal_eligibility", "nepal_eligibility"), ("overall_eligibility", "overall_eligibility"),
        ("research_statuses", "research_status"), ("source_types", "source_type"),
    ]
    result = list(opps)
    for key, field in mapping:
        wanted = criteria.get(key) or []
        if wanted:
            result = [o for o in result if set(facet(o, field)) & set(wanted)]
    d_from, d_to = criteria.get("date_from") or "", criteria.get("date_to") or ""
    if d_from or d_to:
        keep_unknown = criteria.get("include_unknown_dates", True)

        def in_range(o: dict) -> bool:
            d = (o.get("deadline_date") or "")[:10]
            if not d:
                return keep_unknown
            return (not d_from or d >= d_from) and (not d_to or d <= d_to)

        result = [o for o in result if in_range(o)]
    lo, hi = criteria.get("min_score"), criteria.get("max_score")
    if lo is not None:
        result = [o for o in result if float(o.get("match_score", 0)) >= lo]
    if hi is not None:
        result = [o for o in result if float(o.get("match_score", 0)) <= hi]
    if criteria.get("needs_review_only"):
        result = [o for o in result if o.get("needs_review")]
    text = (criteria.get("text") or "").strip().lower()
    if text:
        def haystack(o: dict) -> str:
            rec = o.get("record", {})
            return " ".join(str(x) for x in (
                o.get("name"), o.get("provider"), o.get("country"), o.get("subjects"), o.get("programmes"),
                rec.get("summary"), o.get("notes"), o.get("official_url"))).lower()
        result = [o for o in result if text in haystack(o)]
    sort_by = criteria.get("sort_by") or "match_score"
    descending = criteria.get("descending", True)
    if sort_by == "deadline_date":
        known = sorted((o for o in result if o.get("deadline_date")), key=lambda o: o["deadline_date"], reverse=descending)
        return known + [o for o in result if not o.get("deadline_date")]
    return sorted(result, key=lambda o: (str(o.get(sort_by, "")).lower() if isinstance(o.get(sort_by), str) else o.get(sort_by, 0)), reverse=descending)


def summarize(opps: list, new_discoveries: int = 0) -> dict:
    """Compute the dashboard summary-card numbers.

    Args:
        opps: Enriched saved opportunities.
        new_discoveries: Count of unsaved, undismissed search leads.

    Returns:
        Counts for saved, new discoveries, fully funded, closing soon, verified,
        unclear eligibility and records needing review.
    """
    return {
        "saved": len(opps),
        "new_discoveries": new_discoveries,
        "fully_funded": sum(1 for o in opps if o.get("funding_type") == "fully_funded"),
        "deadlines_approaching": sum(1 for o in opps if o.get("deadline_status") == STATUS_CLOSING_SOON),
        "verified": sum(1 for o in opps if o.get("verified_at")),
        "unclear_eligibility": sum(1 for o in opps if o.get("overall_eligibility") == "UNCLEAR"),
        "needs_review": sum(1 for o in opps if o.get("needs_review")),
    }


def export_csv(opps: list) -> str:
    """Export opportunities as CSV text using :data:`EXPORT_COLUMNS`."""
    return rows_to_csv(opps, EXPORT_COLUMNS)
