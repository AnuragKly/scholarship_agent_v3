"""Change detection between two extracted records of the same opportunity.

Only material fields are compared (deadlines, funding, nationalities, programmes,
eligibility requirements). Comparison ignores case, whitespace and list order so
cosmetic differences do not create false alarms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TRACKED_FIELDS = {
    "application_deadline": "Deadline wording",
    "deadline_date": "Deadline date",
    "deadline_timezone": "Deadline timezone",
    "application_cycle": "Application cycle",
    "funding_type": "Funding type",
    "tuition_coverage": "Tuition coverage",
    "stipend": "Stipend",
    "accommodation": "Accommodation",
    "travel": "Travel",
    "insurance": "Insurance",
    "other_benefits": "Other benefits",
    "eligible_nationalities": "Eligible nationalities",
    "nepal_eligibility": "Nepal eligibility",
    "eligible_programmes": "Eligible programmes",
    "subject_areas": "Subject areas",
    "degree_level": "Degree level",
    "academic_requirements": "Academic requirements",
    "min_gpa": "Minimum GPA",
    "language_requirements": "Language requirements",
    "work_experience": "Work experience",
    "age_limit": "Age limit",
    "residency_restrictions": "Residency restrictions",
    "other_restrictions": "Other restrictions",
}
ELIGIBILITY_FIELDS = {
    "nepal_eligibility", "eligible_nationalities", "degree_level", "academic_requirements", "min_gpa",
    "language_requirements", "work_experience", "age_limit", "residency_restrictions", "other_restrictions",
    "subject_areas", "eligible_programmes",
}


@dataclass(frozen=True)
class Change:
    """One detected difference.

    Attributes:
        field: Record field key.
        label: Display label.
        old: Previous value as readable text.
        new: New value as readable text.
        affects_eligibility: True if the field feeds the eligibility assessment.
    """

    field: str
    label: str
    old: str
    new: str
    affects_eligibility: bool


def display_value(value) -> str:
    """Render a record value as short readable text (lists joined with '; ')."""
    if isinstance(value, list):
        parts = []
        for v in value:
            if isinstance(v, dict):
                parts.append(" ".join(str(x) for x in v.values() if x))
            else:
                parts.append(str(v))
        return "; ".join(p for p in parts if p)
    return "" if value is None else str(value)


def _norm(value) -> str:
    """Normalise a value for comparison (case, whitespace, list order)."""
    if isinstance(value, list):
        items = sorted(re.sub(r"\s+", " ", display_value([v])).strip().lower() for v in value)
        return "|".join(i for i in items if i)
    return re.sub(r"\s+", " ", display_value(value)).strip().lower()


def compare_records(old: dict, new: dict) -> list:
    """Compare two records and list material differences.

    Args:
        old: Previously stored record.
        new: Newly extracted record.

    Returns:
        A list of :class:`Change`, in :data:`TRACKED_FIELDS` order. Empty if identical
        in all tracked fields.
    """
    changes = []
    for key, label in TRACKED_FIELDS.items():
        a, b = old.get(key, "" if not isinstance(new.get(key), list) else []), new.get(key, "")
        if _norm(a) != _norm(b):
            changes.append(Change(key, label, display_value(a), display_value(b), key in ELIGIBILITY_FIELDS))
    return changes
