"""Transparent 0-100 match score, deliberately separate from eligibility.

Each component yields 0.0-1.0 (or None when the source gives no data). **Missing data
earns zero points** and is labelled 'no data' - it is never treated as a match. The
total can be high while eligibility is UNCLEAR or NOT_MET; the UI says so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from .deadlines import STATUS_CLOSING_SOON, STATUS_OPEN, STATUS_PASSED, DeadlineInfo
from .eligibility import CONFIRMED, NOT_APPLICABLE, NOT_MET, EligibilityAssessment
from .subjects import covers_any_subject, match_subjects

DEFAULT_WEIGHTS = {
    "subject": 30.0,
    "funding": 20.0,
    "nationality": 20.0,
    "academic": 10.0,
    "deadline": 10.0,
    "programme": 10.0,
}
COMPONENT_LABELS = {
    "subject": "Subject relevance",
    "funding": "Funding preference",
    "nationality": "Nationality eligibility",
    "academic": "Academic fit",
    "deadline": "Deadline feasibility",
    "programme": "Programme compatibility",
}
_FUNDING_STRICT = {"fully_funded": 1.0, "partially_funded": 0.5, "tuition_only": 0.35, "stipend_only": 0.25}
_FUNDING_FLEX = {"fully_funded": 1.0, "partially_funded": 0.8, "tuition_only": 0.6, "stipend_only": 0.5}


@dataclass
class MatchComponent:
    """One scored component.

    Attributes:
        key: Component identifier.
        label: Display name.
        weight: Normalised weight (percent of total).
        score: 0.0-1.0, or None when there was no data.
        points: ``weight * score`` (0 when no data).
        explanation: Why this score was given.
    """

    key: str
    label: str
    weight: float
    score: Optional[float]
    points: float
    explanation: str


@dataclass
class MatchResult:
    """Total score with its breakdown.

    Attributes:
        total: 0-100 score, one decimal.
        components: Per-component breakdown.
        disclaimer: Reminder that the score is not eligibility.
    """

    total: float = 0.0
    components: list = field(default_factory=list)
    disclaimer: str = "Match score ranks relevance only. A high score does NOT mean you are eligible."

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary."""
        return asdict(self)


def _subject_score(record: dict, profile: dict) -> tuple:
    """Score subject relevance as (score_or_None, explanation)."""
    targets = profile.get("target_subjects", [])
    terms = record.get("subject_areas", []) + record.get("eligible_programmes", [])
    if not targets:
        return None, "No target subjects in Settings."
    if not terms:
        return None, "No data: the source lists no subjects."
    if covers_any_subject(terms):
        return 0.5, "Source covers all subjects; relevant but not specific to your targets."
    matches = match_subjects(targets, terms)
    strong_targets = {m["target"] for m in matches if m["strength"] == "strong"}
    if strong_targets:
        score = min(1.0, 0.8 + 0.1 * (len(strong_targets) - 1))
        return score, f"Strong match for: {', '.join(sorted(strong_targets))}."
    if matches:
        return 0.4, "Only partial keyword overlap with your targets."
    return 0.0, "Listed subjects do not overlap with your targets."


def _funding_score(record: dict, profile: dict) -> tuple:
    """Score funding against the applicant's preference."""
    ftype = record.get("funding_type", "unknown")
    if ftype == "unknown":
        return None, "No data: funding level is not clearly stated."
    pref = profile.get("funding_preference", "fully_funded")
    table = _FUNDING_STRICT if pref == "fully_funded" else _FUNDING_FLEX
    return table.get(ftype, 0.0), f"Funding '{ftype.replace('_', ' ')}' versus your preference '{pref.replace('_', ' ')}'."


def _nationality_score(elig: EligibilityAssessment) -> tuple:
    """Score nationality from the eligibility assessment."""
    c = elig.get("nepal_nationality")
    if c is None:
        return None, "No data."
    if c.status == CONFIRMED:
        return 1.0, "Nationality eligibility is explicitly confirmed by the source."
    if c.status == NOT_MET:
        return 0.0, "The source excludes your nationality."
    return None, "No data: the source does not clearly confirm your nationality (not assumed)."


def _academic_score(elig: EligibilityAssessment) -> tuple:
    """Score academic fit from degree, undergraduate, GPA and language criteria."""
    items = [elig.get(k) for k in ("degree_level", "undergraduate", "gpa", "language")]
    items = [c for c in items if c is not None and c.status != NOT_APPLICABLE]
    if not items:
        return None, "No data."
    if any(c.status == NOT_MET for c in items):
        return 0.0, "At least one academic criterion is not met."
    confirmed = sum(1 for c in items if c.status == CONFIRMED)
    if confirmed == 0:
        return None, "No data: none of the academic criteria could be confirmed."
    return confirmed / len(items), f"{confirmed} of {len(items)} academic criteria confirmed (unclear ones earn nothing)."


def _deadline_score(info: DeadlineInfo) -> tuple:
    """Score deadline feasibility from its status."""
    if info.status == STATUS_OPEN:
        return 1.0, "Deadline is open with comfortable time."
    if info.status == STATUS_CLOSING_SOON:
        return 0.6, "Deadline is close; feasible only if you can prepare quickly."
    if info.status == STATUS_PASSED:
        return 0.0, "Deadline has passed."
    return None, "No data: deadline unknown (not assumed open)."


def _programme_score(record: dict, profile: dict) -> tuple:
    """Score programme compatibility: named programmes and preferred countries."""
    targets = profile.get("target_subjects", [])
    programmes = record.get("eligible_programmes", [])
    prog = None
    if programmes and targets:
        matches = match_subjects(targets, programmes)
        prog = 1.0 if any(m["strength"] == "strong" for m in matches) else (0.5 if matches else 0.0)
    preferred = [c.lower() for c in profile.get("preferred_countries", [])]
    if not preferred:
        return prog, ("Named programmes compared with your targets." if prog is not None else "No data: no programmes named.")
    country = (record.get("country") or "").strip().lower()
    country_part = 1.0 if country and country in preferred else 0.0
    if prog is None and not country:
        return None, "No data: no programmes or country stated."
    score = 0.5 * (prog or 0.0) + 0.5 * country_part
    return score, (f"Programmes: {'no data' if prog is None else f'{prog:.1f}'}; "
                   f"country {'is' if country_part else 'is not'} in your preferred list.")


def compute_match(
    record: dict,
    profile: dict,
    weights: dict,
    eligibility: EligibilityAssessment,
    deadline: DeadlineInfo,
) -> MatchResult:
    """Compute the transparent match score.

    Args:
        record: Structured record.
        profile: Applicant profile.
        weights: Component weights (non-negative; normalised internally).
        eligibility: Result of :func:`eligibility.assess_eligibility`.
        deadline: Result of :func:`deadlines.compute_deadline`.

    Returns:
        A :class:`MatchResult` whose ``total`` is 0-100 and whose components explain it.
    """
    w = {k: max(0.0, float(weights.get(k, DEFAULT_WEIGHTS[k]))) for k in DEFAULT_WEIGHTS}
    total_w = sum(w.values()) or 1.0
    raw = {
        "subject": _subject_score(record, profile),
        "funding": _funding_score(record, profile),
        "nationality": _nationality_score(eligibility),
        "academic": _academic_score(eligibility),
        "deadline": _deadline_score(deadline),
        "programme": _programme_score(record, profile),
    }
    components = []
    total = 0.0
    for key in DEFAULT_WEIGHTS:
        score, why = raw[key]
        weight_pct = w[key] / total_w * 100
        points = weight_pct * (score or 0.0)
        total += points
        components.append(MatchComponent(key, COMPONENT_LABELS[key], round(weight_pct, 1), score, round(points, 1), why))
    return MatchResult(total=round(min(100.0, total), 1), components=components)
