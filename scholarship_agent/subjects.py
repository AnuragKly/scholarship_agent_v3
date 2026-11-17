"""Subject matching helpers shared by eligibility assessment and match scoring.

Subject matching is deliberately labelled *likely compatibility*: a keyword
overlap between a scholarship's listed subjects and the applicant's target
subjects. It says nothing about formal admission to a specific programme.
"""

from __future__ import annotations

import re

from .utils import normalize_text_key

_STOP = {"and", "of", "in", "the", "for", "to", "a", "an", "applied", "science", "sciences", "studies", "study", "master", "masters", "msc", "ma", "degree", "program", "programme"}
_ANY_SUBJECT_PHRASES = (
    "all subjects", "any subject", "all fields", "any field", "all disciplines", "any discipline",
    "all study programmes", "all programmes", "all programs", "all courses", "any course", "all areas",
)


def _tokens(text: str) -> set:
    """Return significant, lightly stemmed word tokens of ``text``."""
    out = set()
    for tok in normalize_text_key(text).split():
        if tok in _STOP or len(tok) < 3:
            continue
        if tok.startswith("mathemat") or tok in ("math", "maths"):
            tok = "mathematics"
        elif tok.startswith("computation"):
            tok = "computational"
        elif tok in ("statistic", "statistical"):
            tok = "statistics"
        out.add(tok)
    return out


def covers_any_subject(terms: list) -> bool:
    """Return True if the listed terms say the scholarship covers all subjects."""
    joined = " ".join(normalize_text_key(t) for t in terms)
    return any(p in joined for p in _ANY_SUBJECT_PHRASES)


def match_subjects(targets: list, record_terms: list) -> list:
    """Match the applicant's target subjects to a scholarship's listed subjects.

    Args:
        targets: Applicant's target subjects (e.g. ``["Applied Mathematics"]``).
        record_terms: Subject areas and programme names extracted from the source.

    Returns:
        A list of ``{"target", "term", "strength"}`` dicts where ``strength`` is
        ``strong`` (one phrase contains the other) or ``partial`` (shared key word).
    """
    matches = []
    for target in targets:
        t_key = normalize_text_key(target)
        t_tokens = _tokens(target)
        for term in record_terms:
            r_key = normalize_text_key(term)
            if not t_key or not r_key:
                continue
            if t_key == r_key or re.search(rf"\b{re.escape(t_key)}\b", r_key) or re.search(rf"\b{re.escape(r_key)}\b", t_key):
                matches.append({"target": target, "term": term, "strength": "strong"})
            elif t_tokens & _tokens(term):
                matches.append({"target": target, "term": term, "strength": "partial"})
    return matches
