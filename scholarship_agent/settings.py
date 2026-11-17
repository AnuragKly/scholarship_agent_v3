"""Applicant profile and matching-weight settings, stored in SQLite and validated.

Nothing about the applicant is stored in Python files. Optional fields (GPA, English
test/score, work experience, age) may stay empty and are then *never assumed*.
"""

from __future__ import annotations

import logging
from typing import Optional

from .database import Database
from .matching import DEFAULT_WEIGHTS
from .utils import dumps, iso, loads, utc_now

logger = logging.getLogger("scholarship_agent.settings")

PROFILE_KEY = "applicant_profile"
WEIGHTS_KEY = "matching_weights"
FUNDING_PREFERENCES = ["fully_funded", "partial_ok", "any"]
EDUCATION_STATUSES = ["in_progress", "completed"]
ENGLISH_TESTS = ["", "IELTS", "TOEFL", "Duolingo", "PTE", "Cambridge", "Other"]

DEFAULT_PROFILE = {
    "nationality": "Nepali",
    "current_education": "Bachelor's (undergraduate)",
    "education_status": "in_progress",
    "target_degree": "Master's",
    "target_subjects": ["Applied Mathematics", "Computational Mathematics", "Data Science"],
    "preferred_countries": [],
    "funding_preference": "fully_funded",
    "gpa": None,
    "gpa_scale": None,
    "english_test": "",
    "english_score": None,
    "work_experience_years": None,
    "age": None,
    "country_of_residence": "Nepal",
    "other_criteria": "",
}


def _opt_float(raw, name: str, lo: float, hi: float, errors: list) -> Optional[float]:
    """Validate an optional number in [lo, hi]; empty means None."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        errors.append(f"{name} must be a number.")
        return None
    if not (lo <= val <= hi):
        errors.append(f"{name} must be between {lo:g} and {hi:g}.")
        return None
    return val


def _text(raw, name: str, max_len: int, errors: list, required: bool = False) -> str:
    """Validate a text field (stripped, length-limited, no control characters)."""
    val = "" if raw is None else str(raw).strip()
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in val):
        errors.append(f"{name} contains invalid characters.")
        val = ""
    if len(val) > max_len:
        errors.append(f"{name} is too long (max {max_len} characters).")
        val = val[:max_len]
    if required and not val:
        errors.append(f"{name} is required.")
    return val


def _csv_list(raw, name: str, errors: list, max_items: int = 20) -> list:
    """Parse a list or comma/newline-separated string into unique, cleaned items."""
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else str(raw).replace("\n", ",").split(",")
    out: list = []
    for item in items:
        item = str(item).strip()
        if item and item.lower() not in [o.lower() for o in out]:
            if len(item) > 80:
                errors.append(f"An entry in {name} is too long.")
                continue
            out.append(item)
    if len(out) > max_items:
        errors.append(f"{name} has too many entries (max {max_items}).")
        out = out[:max_items]
    return out


def validate_profile(raw: dict) -> tuple:
    """Validate applicant details.

    Args:
        raw: Values from the Settings form (strings or typed values).

    Returns:
        ``(clean_profile, errors)``. ``errors`` is a list of user-readable messages; when
        non-empty the profile should not be saved. Optional fields may be empty.
    """
    errors: list = []
    p = dict(DEFAULT_PROFILE)
    p["nationality"] = _text(raw.get("nationality"), "Nationality", 60, errors, required=True)
    p["current_education"] = _text(raw.get("current_education"), "Current education", 120, errors)
    status = (raw.get("education_status") or "in_progress")
    if status not in EDUCATION_STATUSES:
        errors.append("Education status must be 'in progress' or 'completed'.")
        status = "in_progress"
    p["education_status"] = status
    p["target_degree"] = _text(raw.get("target_degree"), "Target degree", 60, errors, required=True)
    p["target_subjects"] = _csv_list(raw.get("target_subjects"), "Target subjects", errors)
    if not p["target_subjects"]:
        errors.append("Add at least one target subject.")
    p["preferred_countries"] = _csv_list(raw.get("preferred_countries"), "Preferred countries", errors)
    pref = raw.get("funding_preference") or "fully_funded"
    if pref not in FUNDING_PREFERENCES:
        errors.append("Funding preference is not valid.")
        pref = "fully_funded"
    p["funding_preference"] = pref
    gpa_errors: list = []
    p["gpa_scale"] = _opt_float(raw.get("gpa_scale"), "GPA scale", 1, 1000, gpa_errors)
    p["gpa"] = _opt_float(raw.get("gpa"), "GPA", 0, 1000, gpa_errors)
    if p["gpa"] is not None and p["gpa_scale"] is None:
        gpa_errors.append("Enter the GPA scale (for example 4, 10 or 100) together with your GPA.")
        p["gpa"] = None
    if p["gpa"] is not None and p["gpa_scale"] is not None and p["gpa"] > p["gpa_scale"]:
        gpa_errors.append("GPA cannot be higher than its scale.")
        p["gpa"] = None
    errors += gpa_errors
    test = (raw.get("english_test") or "").strip()
    if test not in ENGLISH_TESTS:
        errors.append("English test is not valid.")
        test = ""
    p["english_test"] = test
    p["english_score"] = _opt_float(raw.get("english_score"), "English score", 0, 200, errors)
    if p["english_score"] is not None and not test:
        errors.append("Choose the English test that your score belongs to.")
        p["english_score"] = None
    p["work_experience_years"] = _opt_float(raw.get("work_experience_years"), "Work experience (years)", 0, 60, errors)
    age = _opt_float(raw.get("age"), "Age", 14, 99, errors)
    p["age"] = int(age) if age is not None else None
    p["country_of_residence"] = _text(raw.get("country_of_residence"), "Country of residence", 60, errors)
    p["other_criteria"] = _text(raw.get("other_criteria"), "Other criteria", 500, errors)
    return p, errors


def validate_weights(raw: dict) -> tuple:
    """Validate matching weights.

    Args:
        raw: Mapping of component key to a number.

    Returns:
        ``(clean_weights, errors)``. Weights must be non-negative and not all zero; they are
        normalised when used, so they need not sum to 100.
    """
    errors: list = []
    w: dict = {}
    for key, default in DEFAULT_WEIGHTS.items():
        val = _opt_float(raw.get(key, default), f"Weight '{key}'", 0, 1000, errors)
        w[key] = default if val is None else val
    if sum(w.values()) <= 0:
        errors.append("At least one weight must be above zero.")
    return w, errors


def get_setting(db: Database, key: str, default=None):
    """Read one JSON setting, returning ``default`` if absent."""
    rows = db.query("SELECT value_json FROM app_settings WHERE key = ?", (key,))
    return loads(rows[0]["value_json"], default) if rows else default


def set_setting(db: Database, key: str, value) -> None:
    """Insert or replace one JSON setting in a transaction."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, dumps(value), iso(utc_now())),
        )


def load_profile(db: Database) -> dict:
    """Load the applicant profile, filling any missing keys with defaults."""
    stored = get_setting(db, PROFILE_KEY, {}) or {}
    profile = dict(DEFAULT_PROFILE)
    profile.update({k: v for k, v in stored.items() if k in DEFAULT_PROFILE})
    return profile


def load_weights(db: Database) -> dict:
    """Load matching weights, filling any missing keys with defaults."""
    stored = get_setting(db, WEIGHTS_KEY, {}) or {}
    weights = dict(DEFAULT_WEIGHTS)
    weights.update({k: float(v) for k, v in stored.items() if k in DEFAULT_WEIGHTS})
    return weights


def save_profile(db: Database, raw: dict) -> list:
    """Validate and save the applicant profile.

    Returns:
        A list of error messages; an empty list means the profile was saved.
    """
    clean, errors = validate_profile(raw)
    if not errors:
        set_setting(db, PROFILE_KEY, clean)
    return errors


def save_weights(db: Database, raw: dict) -> list:
    """Validate and save matching weights.

    Returns:
        A list of error messages; an empty list means the weights were saved.
    """
    clean, errors = validate_weights(raw)
    if not errors:
        set_setting(db, WEIGHTS_KEY, clean)
    return errors


def reset_to_defaults(db: Database) -> None:
    """Replace stored profile and weights with the defaults (UI asks for confirmation first)."""
    set_setting(db, PROFILE_KEY, dict(DEFAULT_PROFILE))
    set_setting(db, WEIGHTS_KEY, dict(DEFAULT_WEIGHTS))
