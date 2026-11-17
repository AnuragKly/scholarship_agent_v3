"""Eligibility assessment, kept separate from match scoring.

Each criterion gets one of four states - CONFIRMED, NOT_MET, UNCLEAR,
NOT_APPLICABLE - plus the evidence behind it. Rules:

* Overall eligibility is never CONFIRMED while any *mandatory* criterion is UNCLEAR.
* Facts the applicant has not supplied (GPA, test score, age, experience) are never assumed.
* 'International students' never counts as Nepal eligibility.
* Subject compatibility is only "likely compatibility"; formal programme admission is a
  separate, always-unverified criterion.
* Results from the non-AI fallback can never reach overall CONFIRMED.
* Manual overrides by the user are allowed, recorded and clearly marked.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from .subjects import covers_any_subject, match_subjects
from .utils import iso, utc_now

CONFIRMED = "CONFIRMED"
NOT_MET = "NOT_MET"
UNCLEAR = "UNCLEAR"
NOT_APPLICABLE = "NOT_APPLICABLE"
STATES = [CONFIRMED, NOT_MET, UNCLEAR, NOT_APPLICABLE]

_NEPAL_TERMS = ("nepal", "nepali", "nepalese")
_GENERIC_NATIONALITY = ("international", "all nationalit", "any nationalit", "developing countr", "worldwide", "all countr", "non-eu", "outside")
_NOT_REQUIRED = re.compile(r"\b(not required|no (?:prior |previous |professional |work )?experience|none|not necessary|no minimum|not needed)\b", re.I)
_PREFERRED = re.compile(r"\b(preferred|desirable|an advantage|advantageous|optional|beneficial|favou?rably)\b", re.I)
_REQUIRED = re.compile(r"\b(required|must|minimum|at least|mandatory)\b", re.I)


@dataclass
class Criterion:
    """The assessment of one eligibility criterion.

    Attributes:
        key: Stable identifier (e.g. ``nepal_nationality``).
        label: Display name.
        status: One of :data:`STATES`.
        mandatory: True if an UNCLEAR/NOT_MET status blocks overall confirmation.
        detail: Plain-language explanation of how the status was reached.
        evidence: Source wording or the missing-data note supporting the status.
        source_url: Where the evidence came from ('' if none).
        manual: True when the user overrode the automatic result.
    """

    key: str
    label: str
    status: str
    mandatory: bool
    detail: str
    evidence: str = ""
    source_url: str = ""
    manual: bool = False


@dataclass
class EligibilityAssessment:
    """Full assessment: criteria plus the overall state.

    Attributes:
        criteria: List of :class:`Criterion`.
        overall: CONFIRMED, NOT_MET or UNCLEAR (never NOT_APPLICABLE).
        reasons: Short statements explaining the overall state.
        caveat: Standing reminder that formal admission is decided by the university.
        assessed_at: UTC ISO timestamp.
    """

    criteria: list = field(default_factory=list)
    overall: str = UNCLEAR
    reasons: list = field(default_factory=list)
    caveat: str = ("This is an automated reading of the source text, not an official decision. "
                   "Formal programme admission requirements are set by the university and are NOT verified here.")
    assessed_at: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary."""
        return asdict(self)

    def get(self, key: str) -> Optional[Criterion]:
        """Return the criterion with ``key`` or None."""
        return next((c for c in self.criteria if c.key == key), None)


def _evidence(record: dict, *keywords: str) -> tuple:
    """Find the first evidence quote whose field name contains a keyword.

    Returns:
        ``(quote, source_url)`` or ``("", "")``.
    """
    for item in record.get("evidence", []):
        name = (item.get("field") or "").lower()
        if any(k in name for k in keywords) and item.get("quote"):
            return item["quote"], item.get("source_url", "")
    return "", ""


def _first_number(text: str) -> Optional[float]:
    """Return the first number in ``text`` or None."""
    m = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(m.group()) if m else None


def _nepal(record: dict, profile: dict) -> Criterion:
    """Assess nationality eligibility without inferring from generic wording."""
    nat = (profile.get("nationality") or "Nepali").strip()
    nat_is_nepal = nat.lower() in _NEPAL_TERMS
    flag = record.get("nepal_eligibility", "not_stated")
    nationalities = record.get("eligible_nationalities", [])
    quote = record.get("nepal_eligibility_evidence") or _evidence(record, "nepal", "nationalit")[0]
    url = _evidence(record, "nepal", "nationalit")[1]
    listed = [n for n in nationalities if any(t in n.lower() for t in (_NEPAL_TERMS if nat_is_nepal else (nat.lower(),)))]
    generic = [n for n in nationalities if any(g in n.lower() for g in _GENERIC_NATIONALITY)]
    label = "Nepali nationality eligible" if nat_is_nepal else f"{nat} nationality eligible"
    if not nat_is_nepal:
        if listed:
            return Criterion("nepal_nationality", label, CONFIRMED, True, f"'{nat}' appears in the source's eligible nationalities.", "; ".join(listed))
        return Criterion("nepal_nationality", label, UNCLEAR, True, f"The source does not clearly name '{nat}' as eligible.", "; ".join(generic))
    if flag == "explicitly_excluded":
        return Criterion("nepal_nationality", label, NOT_MET, True, "The source names Nepal as not eligible.", quote, url)
    if flag == "explicitly_eligible":
        return Criterion("nepal_nationality", label, CONFIRMED, True, "The source explicitly names Nepal/Nepali applicants as eligible.", quote, url)
    if listed:
        return Criterion("nepal_nationality", label, UNCLEAR, True,
                         "Nepal appears in the extracted nationality list, but the extraction did not mark it explicit. Verify on the page.", "; ".join(listed), url)
    if generic:
        return Criterion("nepal_nationality", label, UNCLEAR, True,
                         "Only generic wording (e.g. 'international students') was found. This does NOT confirm Nepal.", "; ".join(generic), url)
    detail = ("Nationality rules are not stated in the source." if flag == "not_stated"
              else "Nationality wording is unclear and does not name Nepal.")
    return Criterion("nepal_nationality", label, UNCLEAR, True, detail, quote, url)


def _degree(record: dict) -> Criterion:
    """Assess whether the scholarship funds Master's-level study."""
    level = record.get("degree_level", "unknown")
    quote, url = _evidence(record, "degree")
    if level == "master":
        return Criterion("degree_level", "Master's degree level", CONFIRMED, True, "Source states Master's-level funding.", quote, url)
    if level == "unknown":
        return Criterion("degree_level", "Master's degree level", UNCLEAR, True, "Degree level is not clearly stated.", quote, url)
    return Criterion("degree_level", "Master's degree level", NOT_MET, True, f"Source funds '{level}' level, not Master's.", quote, url)


def _subject(record: dict, profile: dict) -> Criterion:
    """Assess likely subject compatibility (not formal admission)."""
    targets = profile.get("target_subjects", [])
    terms = record.get("subject_areas", []) + record.get("eligible_programmes", [])
    label = "Subject compatibility (likely)"
    if not targets:
        return Criterion("subject", label, UNCLEAR, True, "No target subjects set in Settings.")
    if not terms:
        return Criterion("subject", label, UNCLEAR, True, "The source does not list eligible subjects/programmes.")
    quote, url = _evidence(record, "subject", "programme")
    if covers_any_subject(terms):
        return Criterion("subject", label, CONFIRMED, True,
                         "Source says all subjects/fields are covered. Likely compatible; the specific programme's own admission rules still apply.", "; ".join(terms[:3]), url)
    matches = match_subjects(targets, terms)
    strong = [m for m in matches if m["strength"] == "strong"]
    if strong:
        names = "; ".join(sorted({f"{m['target']} ~ {m['term']}" for m in strong}))
        return Criterion("subject", label, CONFIRMED, True,
                         f"Likely compatible (listed subject matches target). This is not formal admission eligibility. Matched: {names}", quote or "; ".join(terms[:3]), url)
    if matches:
        names = "; ".join(sorted({f"{m['target']} ~ {m['term']}" for m in matches}))
        return Criterion("subject", label, UNCLEAR, True,
                         f"Only a partial word overlap with your targets ({names}). Check the programme list.", "; ".join(terms[:3]), url)
    return Criterion("subject", label, NOT_MET, True,
                     "None of the subjects/programmes listed in the source overlap with your target subjects (based only on what the source lists).", "; ".join(terms[:5]), url)


def _undergrad(record: dict, profile: dict) -> Criterion:
    """Assess the undergraduate-qualification requirement against the applicant's education status."""
    req = record.get("academic_requirements", "")
    quote, url = _evidence(record, "academic", "qualification", "bachelor")
    label = "Undergraduate qualification"
    if not req:
        return Criterion("undergraduate", label, UNCLEAR, True, "Academic requirements are not stated in the source.")
    if not re.search(r"bachelor|undergraduate|first degree|first-cycle|four-year|4-year|equivalent", req, re.I):
        return Criterion("undergraduate", label, UNCLEAR, True, "Requirements are stated but do not clearly mention a bachelor's degree.", req[:200], url)
    status = (profile.get("education_status") or "in_progress").lower()
    if status == "completed":
        return Criterion("undergraduate", label, CONFIRMED, True, "Source requires a bachelor's degree; you report having completed one. Level/field equivalence is not checked.", quote or req[:200], url)
    return Criterion("undergraduate", label, UNCLEAR, True,
                     "Source requires a bachelor's degree; yours is in progress. Check whether you can apply before graduating and the graduation-date rule.", quote or req[:200], url)


def _gpa(record: dict, profile: dict) -> Criterion:
    """Compare stated minimum GPA to the applicant's GPA only when both are known and on stated scales."""
    req = (record.get("min_gpa") or "").strip()
    label = "Minimum GPA / grade"
    quote, url = _evidence(record, "gpa", "grade")
    if not req:
        return Criterion("gpa", label, UNCLEAR, False, "The source does not state a minimum GPA. A programme may still have one; check admission pages.")
    if re.search(r"\b(none|no minimum|not required)\b", req, re.I):
        return Criterion("gpa", label, NOT_APPLICABLE, False, "Source states no minimum GPA.", req, url)
    ratio = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|out of|of)\s*(\d+(?:\.\d+)?)", req)
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", req)
    if ratio:
        required = float(ratio.group(1)) / float(ratio.group(2))
    elif pct:
        required = float(pct.group(1)) / 100
    else:
        return Criterion("gpa", label, UNCLEAR, True, f"A minimum is stated ('{req}') but its scale is unclear, so it cannot be compared.", quote or req, url)
    gpa, scale = profile.get("gpa"), profile.get("gpa_scale")
    if gpa is None or not scale:
        return Criterion("gpa", label, UNCLEAR, True, "Your GPA and its scale are not set in Settings, so this is not assumed.", quote or req, url)
    if float(gpa) / float(scale) >= required:
        return Criterion("gpa", label, CONFIRMED, True, f"Your {gpa}/{scale} meets the stated minimum ({req}). Grade scales differ between institutions; confirm conversion.", quote or req, url)
    return Criterion("gpa", label, NOT_MET, True, f"Your {gpa}/{scale} is below the stated minimum ({req}). Grade scales differ; confirm conversion.", quote or req, url)


def _language(record: dict, profile: dict) -> Criterion:
    """Compare stated language-test requirements with the applicant's supplied test and score."""
    reqs = record.get("language_requirements", [])
    label = "Language / English test"
    quote, url = _evidence(record, "language", "english", "ielts", "toefl")
    if not reqs:
        return Criterion("language", label, UNCLEAR, False, "The source does not state a language requirement.")
    shown = "; ".join(f"{r.get('test', '')} {r.get('minimum_score', '')}".strip() for r in reqs)
    test, score = (profile.get("english_test") or "").strip().lower(), profile.get("english_score")
    if not test or score is None:
        return Criterion("language", label, UNCLEAR, True, "Your English test and score are not set in Settings, so this is not assumed.", quote or shown, url)
    results = []
    for r in reqs:
        if test and test in (r.get("test") or "").lower():
            need = _first_number(r.get("minimum_score", ""))
            if need is None:
                results.append(UNCLEAR)
            else:
                results.append(CONFIRMED if float(score) >= need else NOT_MET)
    if CONFIRMED in results:
        return Criterion("language", label, CONFIRMED, True, f"Your {profile.get('english_test')} {score} meets a stated requirement ({shown}).", quote or shown, url)
    if results and all(x == NOT_MET for x in results) and len(results) == len(reqs):
        return Criterion("language", label, NOT_MET, True, f"Your {profile.get('english_test')} {score} is below the stated minimum ({shown}).", quote or shown, url)
    if results:
        return Criterion("language", label, UNCLEAR, True, f"Your score could not be compared cleanly with: {shown}. Other accepted tests may exist.", quote or shown, url)
    return Criterion("language", label, UNCLEAR, True, f"Your test ({profile.get('english_test')}) is not among the stated tests: {shown}.", quote or shown, url)


def _experience(record: dict, profile: dict) -> Criterion:
    """Compare stated work-experience requirement with the applicant's optional experience."""
    req = (record.get("work_experience") or "").strip()
    label = "Work experience"
    quote, url = _evidence(record, "work", "experience")
    if not req:
        return Criterion("work_experience", label, UNCLEAR, False, "The source does not state a work-experience requirement.")
    if _NOT_REQUIRED.search(req):
        return Criterion("work_experience", label, NOT_APPLICABLE, False, "Source states work experience is not required.", req, url)
    if _PREFERRED.search(req) and not _REQUIRED.search(req):
        return Criterion("work_experience", label, UNCLEAR, False, "Experience is described as preferred/desirable rather than required.", req, url)
    years_match = re.search(r"(\d+)\s*\+?\s*(?:years?|yrs?)", req, re.I)
    need = float(years_match.group(1)) if years_match else None
    have = profile.get("work_experience_years")
    if need is None:
        return Criterion("work_experience", label, UNCLEAR, True, "An experience requirement is stated but not as a number of years.", quote or req, url)
    if have is None:
        return Criterion("work_experience", label, UNCLEAR, True, "Your work experience is not set in Settings, so this is not assumed.", quote or req, url)
    if float(have) >= need:
        return Criterion("work_experience", label, CONFIRMED, True, f"Your {have} years meet the stated {need:g} years.", quote or req, url)
    return Criterion("work_experience", label, NOT_MET, True, f"Your {have} years are below the stated {need:g} years.", quote or req, url)


def _age(record: dict, profile: dict) -> Criterion:
    """Compare a stated maximum age with the applicant's optional age."""
    req = (record.get("age_limit") or "").strip()
    label = "Age limit"
    quote, url = _evidence(record, "age")
    if not req:
        return Criterion("age", label, UNCLEAR, False, "The source does not state an age limit.")
    m = re.search(r"(?:under|below|younger than|not (?:older|more) than|maximum(?: age)?(?: of)?|max\.?|up to|<)\s*(\d{2})", req, re.I) \
        or re.search(r"(\d{2})\s*(?:years?)?\s*(?:or younger|or under|at most)", req, re.I)
    age = profile.get("age")
    if not m:
        return Criterion("age", label, UNCLEAR, True, f"An age rule is stated ('{req}') but could not be compared automatically.", quote or req, url)
    if age is None:
        return Criterion("age", label, UNCLEAR, True, "Your age is not set in Settings (optional), so this is not assumed.", quote or req, url)
    if int(age) <= int(m.group(1)):
        return Criterion("age", label, CONFIRMED, True, f"Your age ({age}) is within the stated limit ({req}). Check the reference date the source uses.", quote or req, url)
    return Criterion("age", label, NOT_MET, True, f"Your age ({age}) is above the stated limit ({req}). Check the reference date.", quote or req, url)


def _residency(record: dict, profile: dict) -> Criterion:
    """Flag residency restrictions for manual checking."""
    req = (record.get("residency_restrictions") or "").strip()
    label = "Residency restrictions"
    quote, url = _evidence(record, "residen")
    if not req:
        return Criterion("residency", label, UNCLEAR, False, "The source does not state residency restrictions.")
    return Criterion("residency", label, UNCLEAR, True,
                     f"Residency rule stated; it must be checked manually against your situation (Settings: residing in {profile.get('country_of_residence') or 'not set'}).", quote or req, url)


def _other(record: dict, profile: dict) -> Criterion:
    """Flag other stated conditions for manual checking."""
    items = record.get("other_restrictions", [])
    label = "Other conditions"
    if not items:
        return Criterion("other", label, UNCLEAR, False, "No other restrictions were extracted. This does not mean none exist.")
    note = f" Your own criteria note: {profile['other_criteria']}" if profile.get("other_criteria") else ""
    return Criterion("other", label, UNCLEAR, True, "Other conditions are stated and need a manual check." + note, "; ".join(items[:5]))


def _admission() -> Criterion:
    """Return the standing, always-unverified formal-admission criterion."""
    return Criterion("programme_admission", "Formal programme admission", UNCLEAR, False,
                     "Not assessed. The university decides admission using its own requirements, which may differ from the scholarship's. Read the programme admission page.")


def assess_eligibility(
    record: dict,
    profile: dict,
    overrides: Optional[dict] = None,
    ai_verified: bool = True,
) -> EligibilityAssessment:
    """Assess all eligibility criteria for one record and compute the overall state.

    Args:
        record: Structured record.
        profile: Applicant profile from Settings.
        overrides: Optional ``{criterion_key: {"status", "note", "at"}}`` from the user.
        ai_verified: False for heuristic-fallback records; caps overall at UNCLEAR
            unless every mandatory criterion was manually confirmed.

    Returns:
        An :class:`EligibilityAssessment`.
    """
    criteria = [
        _nepal(record, profile), _degree(record), _subject(record, profile), _undergrad(record, profile),
        _gpa(record, profile), _language(record, profile), _experience(record, profile),
        _age(record, profile), _residency(record, profile), _other(record, profile), _admission(),
    ]
    for c in criteria:
        ov = (overrides or {}).get(c.key)
        if ov and ov.get("status") in STATES:
            c.detail = f"Manual decision by you ({ov.get('at', '')[:10]}): {ov.get('note') or 'no note'}. Automatic result was {c.status}. " + c.detail
            c.status, c.manual = ov["status"], True
    result = EligibilityAssessment(criteria=criteria, assessed_at=iso(utc_now()))
    mandatory = [c for c in criteria if c.mandatory]
    not_met = [c for c in mandatory if c.status == NOT_MET]
    unclear = [c for c in mandatory if c.status == UNCLEAR]
    if not_met:
        result.overall = NOT_MET
        result.reasons = [f"Not met: {c.label}" for c in not_met]
    elif unclear:
        result.overall = UNCLEAR
        result.reasons = [f"Unclear: {c.label}" for c in unclear]
    elif not ai_verified and any(not c.manual for c in mandatory):
        result.overall = UNCLEAR
        result.reasons = ["Extraction was not AI-verified, so it cannot support a confirmed result."]
    else:
        result.overall = CONFIRMED
        result.reasons = ["All mandatory criteria stated by the source are confirmed."]
    open_items = [c.label for c in criteria if not c.mandatory and c.status == UNCLEAR and c.key != "programme_admission"]
    if open_items and result.overall == CONFIRMED:
        result.reasons.append("Not stated by the source (check yourself): " + ", ".join(open_items))
    return result
