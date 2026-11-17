"""Duplicate detection: normalised official URLs first, then name/provider similarity."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from .utils import normalize_text_key, normalize_url

NAME_THRESHOLD = 0.85
NAME_ONLY_THRESHOLD = 0.92
PROVIDER_THRESHOLD = 0.6


@dataclass(frozen=True)
class DuplicateMatch:
    """A possible duplicate of a candidate opportunity.

    Attributes:
        opportunity_id: Id of the existing saved record.
        name: Existing record's name.
        provider: Existing record's provider.
        reason: ``same_url`` (certain) or ``similar_name`` (needs human judgement).
        similarity: 1.0 for same URL, otherwise the name similarity ratio.
    """

    opportunity_id: int
    name: str
    provider: str
    reason: str
    similarity: float


def _ratio(a: str, b: str) -> float:
    """Return the SequenceMatcher similarity of two normalised strings."""
    a, b = normalize_text_key(a), normalize_text_key(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_duplicates(candidate: dict, existing: list) -> list:
    """Find saved opportunities that may be the same as ``candidate``.

    Args:
        candidate: Dict with ``official_url``, optional ``canonical_url`` and ``urls``
            (extra source URLs), ``name`` and ``provider``.
        existing: Saved opportunities (dicts with ``id``, ``name``, ``provider``,
            ``normalized_url``, ``canonical_url``, ``official_url``).

    Returns:
        A list of :class:`DuplicateMatch` (same-URL matches first, then by similarity).
        Never modifies or overwrites anything.
    """
    cand_urls = {
        normalize_url(u)
        for u in [candidate.get("official_url", ""), candidate.get("canonical_url", ""), *candidate.get("urls", [])]
        if u
    } - {""}
    matches: dict = {}
    for row in existing:
        row_urls = {normalize_url(row.get(k, "")) for k in ("normalized_url", "official_url", "canonical_url")} - {""}
        row_urls |= {u for u in row.get("normalized_url", "").split() if u}
        if cand_urls & row_urls:
            matches[row["id"]] = DuplicateMatch(row["id"], row.get("name", ""), row.get("provider", ""), "same_url", 1.0)
            continue
        name_sim = _ratio(candidate.get("name", ""), row.get("name", ""))
        cand_provider, row_provider = candidate.get("provider", ""), row.get("provider", "")
        if cand_provider and row_provider:
            ok = name_sim >= NAME_THRESHOLD and _ratio(cand_provider, row_provider) >= PROVIDER_THRESHOLD
        else:
            ok = name_sim >= NAME_ONLY_THRESHOLD
        if ok:
            matches[row["id"]] = DuplicateMatch(row["id"], row.get("name", ""), row_provider, "similar_name", round(name_sim, 3))
    return sorted(matches.values(), key=lambda m: (m.reason != "same_url", -m.similarity))
