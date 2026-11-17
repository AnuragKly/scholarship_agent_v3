"""The structured scholarship record: field list, enums, strict JSON schema and coercion.

One definition is shared by AI extraction (the strict schema sent to OpenAI),
the heuristic fallback, the database, and the UI, so they cannot drift apart.
"""

from __future__ import annotations

import copy
from typing import Any

DEGREE_LEVELS = ["master", "bachelor", "phd", "other", "unknown"]
FUNDING_TYPES = ["fully_funded", "partially_funded", "tuition_only", "stipend_only", "unknown"]
NEPAL_ELIGIBILITY_VALUES = ["explicitly_eligible", "explicitly_excluded", "not_stated", "unclear"]

STRING_FIELDS = [
    "name", "country", "provider", "tuition_coverage", "stipend", "accommodation", "travel",
    "insurance", "other_benefits", "nepal_eligibility_evidence", "academic_requirements",
    "min_gpa", "work_experience", "age_limit", "residency_restrictions", "application_deadline",
    "deadline_date", "deadline_timezone", "deadline_applies_to", "application_cycle",
    "application_url", "application_instructions", "summary",
]
LIST_FIELDS = [
    "eligible_programmes", "subject_areas", "eligible_nationalities", "other_restrictions",
    "interpretations", "uncertainties",
]
ENUM_FIELDS = {
    "degree_level": (DEGREE_LEVELS, "unknown"),
    "funding_type": (FUNDING_TYPES, "unknown"),
    "nepal_eligibility": (NEPAL_ELIGIBILITY_VALUES, "not_stated"),
}
OBJECT_LIST_FIELDS = {
    "language_requirements": ["test", "minimum_score", "notes"],
    "evidence": ["field", "quote"],
}

ALL_FIELDS = (
    STRING_FIELDS + LIST_FIELDS + list(ENUM_FIELDS) + list(OBJECT_LIST_FIELDS)
)

FIELD_DESCRIPTIONS = {
    "name": "Official scholarship or programme name exactly as the source states it.",
    "country": "Country where the study takes place.",
    "provider": "University, government agency or foundation that provides/awards it.",
    "degree_level": "Degree level the scholarship funds.",
    "eligible_programmes": "Specific Master's programmes the source names (empty list if none).",
    "subject_areas": "Subject areas/disciplines the source says are eligible (empty list if not stated).",
    "funding_type": "fully_funded only if the source states tuition AND living costs are covered; otherwise partially_funded, tuition_only, stipend_only or unknown.",
    "tuition_coverage": "What the source says about tuition fees ('' if not stated).",
    "stipend": "Monthly/annual living allowance wording, with currency ('' if not stated).",
    "accommodation": "Accommodation benefit wording ('' if not stated).",
    "travel": "Travel/airfare benefit wording ('' if not stated).",
    "insurance": "Health insurance benefit wording ('' if not stated).",
    "other_benefits": "Any other benefits the source lists ('' if none).",
    "eligible_nationalities": "Nationalities/countries the source explicitly names as eligible. Do NOT put 'international students' here unless the source uses exactly that phrase; never add Nepal unless the source names it.",
    "nepal_eligibility": "explicitly_eligible ONLY if the source names Nepal/Nepali/Nepalese (or a list/definition that clearly includes Nepal); explicitly_excluded if it names Nepal as excluded; unclear if the wording is generic (e.g. 'international students', 'developing countries') without naming Nepal; not_stated if nationality is not discussed.",
    "nepal_eligibility_evidence": "Verbatim sentence from the source supporting nepal_eligibility ('' if none).",
    "academic_requirements": "Academic requirements wording (required prior degree, etc.).",
    "min_gpa": "Minimum GPA/grade exactly as written, including scale ('' if not stated; 'none' only if the source explicitly says there is no minimum).",
    "language_requirements": "Each language test requirement the source states, with minimum score.",
    "work_experience": "Work experience requirement wording ('' if not stated; 'not required' only if explicitly stated).",
    "age_limit": "Age limit wording ('' if not stated).",
    "residency_restrictions": "Residency/country-of-residence restrictions wording ('' if not stated).",
    "other_restrictions": "Other eligibility conditions the source states.",
    "application_deadline": "Deadline wording exactly as the source writes it ('' if not stated).",
    "deadline_date": "ISO date (YYYY-MM-DD, or YYYY-MM-DDTHH:MM if a time is stated) ONLY if the source gives an unambiguous full date; otherwise ''.",
    "deadline_timezone": "Timezone stated for the deadline (e.g. 'CET', 'UTC+1', 'Europe/Berlin'); '' if not stated.",
    "deadline_applies_to": "Which programme/intake/cohort the deadline applies to, if stated.",
    "application_cycle": "Application cycle or intake year, e.g. '2027/28' ('' if not stated).",
    "application_url": "URL where applications are made, if given in the source ('' otherwise).",
    "application_instructions": "Short summary of how to apply, as stated.",
    "summary": "2-4 sentence neutral summary of what the source states. No eligibility conclusions.",
    "interpretations": "Your inferences or readings that are NOT directly stated by the source. Label each clearly.",
    "uncertainties": "Things that are missing, ambiguous or contradictory in the source.",
    "language_requirements.test": "Test name (IELTS, TOEFL, etc.) or language requirement.",
    "evidence": "Verbatim supporting quotations (<=300 chars each) for important claims: nepal eligibility, funding, deadline, GPA, language.",
}


def empty_record() -> dict:
    """Return a record with every field present and set to its 'unknown/empty' value."""
    rec: dict = {f: "" for f in STRING_FIELDS}
    rec.update({f: [] for f in LIST_FIELDS})
    for f, (_, default) in ENUM_FIELDS.items():
        rec[f] = default
    for f in OBJECT_LIST_FIELDS:
        rec[f] = []
    return rec


def _string_schema(desc: str) -> dict:
    """Build a JSON-schema fragment for a string field."""
    return {"type": "string", "description": desc}


def build_json_schema() -> dict:
    """Build the strict JSON schema used with OpenAI Structured Outputs.

    All properties are listed in ``required`` and ``additionalProperties`` is
    false at every object level, as strict mode demands.

    Returns:
        A JSON-schema dictionary describing one scholarship record.
    """
    props: dict = {}
    for f in STRING_FIELDS:
        props[f] = _string_schema(FIELD_DESCRIPTIONS.get(f, f))
    for f in LIST_FIELDS:
        props[f] = {"type": "array", "items": {"type": "string"}, "description": FIELD_DESCRIPTIONS.get(f, f)}
    for f, (values, _) in ENUM_FIELDS.items():
        props[f] = {"type": "string", "enum": values, "description": FIELD_DESCRIPTIONS.get(f, f)}
    for f, keys in OBJECT_LIST_FIELDS.items():
        props[f] = {
            "type": "array",
            "description": FIELD_DESCRIPTIONS.get(f, f),
            "items": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in keys},
                "required": list(keys),
                "additionalProperties": False,
            },
        }
    return {
        "type": "object",
        "properties": props,
        "required": list(props.keys()),
        "additionalProperties": False,
    }


def coerce_record(raw: Any) -> tuple:
    """Validate and normalise a raw (AI or fallback) dictionary into a full record.

    Unknown keys are dropped, wrong types are repaired or emptied, invalid enum
    values become the safe 'unknown' value. Nothing is invented.

    Args:
        raw: The parsed JSON object from the model (or a heuristic dict).

    Returns:
        ``(record, warnings)`` where ``record`` always contains every field and
        ``warnings`` lists each repair that was made.

    Raises:
        ValueError: If ``raw`` is not a dictionary.
    """
    if not isinstance(raw, dict):
        raise ValueError("Extraction result is not a JSON object.")
    rec = empty_record()
    warnings: list = []
    for f in STRING_FIELDS:
        if f in raw and raw[f] is not None:
            if isinstance(raw[f], (str, int, float)):
                rec[f] = str(raw[f]).strip()
            else:
                warnings.append(f"Field '{f}' had an unexpected type and was ignored.")
        elif f not in raw:
            warnings.append(f"Field '{f}' was missing from the extraction.")
    for f in LIST_FIELDS:
        val = raw.get(f)
        if isinstance(val, list):
            rec[f] = [str(v).strip() for v in val if isinstance(v, (str, int, float)) and str(v).strip()]
        elif isinstance(val, str) and val.strip():
            rec[f] = [val.strip()]
            warnings.append(f"Field '{f}' was a string, converted to a list.")
        elif f not in raw:
            warnings.append(f"Field '{f}' was missing from the extraction.")
    for f, (values, default) in ENUM_FIELDS.items():
        val = raw.get(f)
        if isinstance(val, str) and val.strip().lower() in values:
            rec[f] = val.strip().lower()
        else:
            rec[f] = default
            warnings.append(f"Field '{f}' had an invalid value; set to '{default}'.")
    for f, keys in OBJECT_LIST_FIELDS.items():
        val = raw.get(f)
        items: list = []
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    entry = {k: str(item.get(k, "") or "").strip() for k in keys}
                    if any(entry.values()):
                        items.append(entry)
                else:
                    warnings.append(f"An entry in '{f}' was malformed and was dropped.")
        elif f not in raw:
            warnings.append(f"Field '{f}' was missing from the extraction.")
        rec[f] = items
    return rec, warnings


def clone(record: dict) -> dict:
    """Return a deep copy of a record."""
    return copy.deepcopy(record)
