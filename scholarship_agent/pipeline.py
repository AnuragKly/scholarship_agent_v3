"""Orchestration: fetch -> extract -> assess -> score -> save, plus source re-checks.

This is the only module that wires the others together, so the UI stays thin and each
step stays independently testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from . import repository
from .changes import Change, compare_records
from .config import Config
from .database import Database
from .deadlines import DeadlineInfo, compute_deadline
from .eligibility import EligibilityAssessment, assess_eligibility
from .extraction import (
    METHOD_AI, METHOD_FALLBACK, AIUnavailable, ExtractionError, ExtractionResult,
    extract_fallback, extract_with_ai, merge_records,
)
from .fetcher import FetchResult, build_pasted_result, fetch_page
from .matching import MatchResult, compute_match
from .sources import SourceInfo, classify_source
from .utils import iso, safe_error, utc_now

logger = logging.getLogger("scholarship_agent.pipeline")

IMPORTANT_FIELDS = {
    "name": "name", "provider": "provider/university", "country": "country", "funding_type": "funding level",
    "application_deadline": "deadline", "nepal_eligibility": "Nepal eligibility", "academic_requirements": "academic requirements",
    "language_requirements": "language requirements", "subject_areas": "subject areas", "eligible_programmes": "named programmes",
}


@dataclass
class PageResearch:
    """One fetched-and-extracted page within a research session.

    Attributes:
        role: ``primary``, ``linked`` or ``pasted``.
        fetch: The fetch result (``ok`` False means nothing was extracted).
        extraction: Extraction result, or None when the fetch failed.
        source: Source classification of the final URL.
    """

    role: str
    fetch: FetchResult
    extraction: Optional[ExtractionResult]
    source: SourceInfo


@dataclass
class ResearchResult:
    """Everything the UI needs to show after researching a page.

    Attributes:
        ok: True when at least the primary page was read and extracted.
        error: Actionable message when ``ok`` is False.
        pages: All pages researched so far (primary first).
        record: Merged structured record (conflicts preserved separately).
        conflicts: Fields on which sources disagree.
        eligibility: Eligibility assessment.
        match: Match score breakdown.
        deadline: Deadline status.
        ai_verified: True only if every extraction used was AI-based.
        warnings: Notes (fallback use, repairs, redirects).
        missing: Human labels of important fields the sources did not provide.
    """

    ok: bool = False
    error: str = ""
    pages: list = field(default_factory=list)
    record: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    eligibility: Optional[EligibilityAssessment] = None
    match: Optional[MatchResult] = None
    deadline: Optional[DeadlineInfo] = None
    ai_verified: bool = False
    warnings: list = field(default_factory=list)
    missing: list = field(default_factory=list)

    @property
    def primary(self) -> Optional[PageResearch]:
        """Return the first page that produced an extraction, or None."""
        return next((p for p in self.pages if p.extraction is not None), None)


def find_missing(record: dict) -> list:
    """List important facts the sources did not provide.

    Args:
        record: Structured record.

    Returns:
        Readable names of empty/unknown important fields.
    """
    missing = []
    for key, label in IMPORTANT_FIELDS.items():
        val = record.get(key)
        if val in ("", [], None, "unknown", "not_stated"):
            missing.append(label)
    if not record.get("deadline_date"):
        missing.append("exact deadline date") if "deadline" not in missing else None
    return missing


def evaluate(record: dict, profile: dict, weights: dict, cfg: Config, overrides: Optional[dict] = None,
             ai_verified: bool = True, conflicts: Optional[list] = None, now: Optional[datetime] = None) -> tuple:
    """Compute eligibility, deadline and match for a record.

    Args:
        record: Structured record.
        profile: Applicant profile.
        weights: Matching weights.
        cfg: App configuration (closing-soon threshold).
        overrides: User's manual criterion decisions.
        ai_verified: Whether the record is AI-extracted.
        conflicts: Cross-source conflicts (their deadline values feed the deadline check).
        now: Injectable clock for tests.

    Returns:
        ``(eligibility, deadline, match)``.
    """
    other_dates = []
    for c in conflicts or []:
        if c.get("field") == "deadline_date":
            other_dates += [v["value"] for v in c.get("values", [])]
    eligibility = assess_eligibility(record, profile, overrides, ai_verified)
    deadline = compute_deadline(record, now, cfg.closing_soon_days, other_dates)
    match = compute_match(record, profile, weights, eligibility, deadline)
    return eligibility, deadline, match


def _extract(page: FetchResult, cfg: Config, use_ai: bool, client: Any) -> ExtractionResult:
    """Extract with AI when possible, otherwise use the labelled fallback with a clear reason."""
    url = page.canonical_url or page.final_url or page.requested_url
    if use_ai:
        try:
            return extract_with_ai(page.text, url, page.title, cfg.openai_api_key, cfg.openai_model, client)
        except AIUnavailable as exc:
            reason = str(exc)
        except ExtractionError as exc:
            reason = f"AI extraction failed: {exc}"
        result = extract_fallback(page.text, url, page.title)
        result.warnings.insert(0, f"{reason} Showing the heuristic fallback instead (NOT AI-verified).")
        return result
    result = extract_fallback(page.text, url, page.title)
    result.warnings.insert(0, "AI extraction was switched off for this run. Showing the heuristic fallback (NOT AI-verified).")
    return result


def _page_from_fetch(role: str, fetch: FetchResult, cfg: Config, use_ai: bool, client: Any) -> PageResearch:
    """Classify the source and extract from a fetched page (no extraction if the fetch failed)."""
    source = classify_source(fetch.final_url or fetch.requested_url)
    extraction = _extract(fetch, cfg, use_ai, client) if fetch.ok else None
    return PageResearch(role, fetch, extraction, source)


def assemble(pages: list, profile: dict, weights: dict, cfg: Config, overrides: Optional[dict] = None,
             now: Optional[datetime] = None) -> ResearchResult:
    """Build a :class:`ResearchResult` from researched pages.

    The first successfully extracted page is the primary record; others are merged
    with disagreements preserved as conflicts.

    Args:
        pages: List of :class:`PageResearch`.
        profile: Applicant profile.
        weights: Matching weights.
        cfg: App configuration.
        overrides: Manual criterion overrides.
        now: Injectable clock for tests.

    Returns:
        The assembled result (``ok=False`` with the primary fetch error if nothing was read).
    """
    result = ResearchResult(pages=pages)
    good = [p for p in pages if p.extraction is not None]
    if not good:
        first = pages[0].fetch if pages else None
        result.error = first.error if first and first.error else "The page could not be read."
        return result
    primary = good[0]
    others = [(p.fetch.canonical_url or p.fetch.final_url, p.extraction.record) for p in good[1:]]
    record, conflicts = merge_records(primary.extraction.record, others) if others else (primary.extraction.record, [])
    result.ok, result.record, result.conflicts = True, record, conflicts
    result.ai_verified = all(p.extraction.ai_verified for p in good)
    for p in pages:
        result.warnings += p.fetch.warnings
        if p.extraction:
            result.warnings += p.extraction.warnings
    if any(p.source.is_third_party for p in good):
        result.warnings.append("At least one source is a third-party website. Confirm everything on the official page.")
    result.eligibility, result.deadline, result.match = evaluate(
        record, profile, weights, cfg, overrides, result.ai_verified, conflicts, now)
    result.missing = find_missing(record)
    return result


def research_url(url: str, cfg: Config, profile: dict, weights: dict, use_ai: bool = True, session: Any = None,
                 resolver: Any = None, client: Any = None, now: Optional[datetime] = None) -> ResearchResult:
    """Fetch an official page, extract it, and assess it.

    Args:
        url: Page URL (from a search result or typed by the user).
        cfg: App configuration.
        profile: Applicant profile.
        weights: Matching weights.
        use_ai: False forces the labelled heuristic fallback.
        session: Optional ``requests.Session`` (tests).
        resolver: Optional DNS resolver (tests).
        client: Optional OpenAI client (tests pass a mock).
        now: Injectable clock.

    Returns:
        A :class:`ResearchResult`; on fetch failure ``ok`` is False and nothing is invented.
    """
    fetch = fetch_page(url, cfg.fetch_timeout_seconds, cfg.max_response_bytes, cfg.max_page_chars, session, resolver)
    return assemble([_page_from_fetch("primary", fetch, cfg, use_ai, client)], profile, weights, cfg, None, now)


def research_pasted_text(url: str, text: str, cfg: Config, profile: dict, weights: dict, use_ai: bool = True,
                         client: Any = None, now: Optional[datetime] = None) -> ResearchResult:
    """Research text the user pasted from an official page that could not be fetched.

    Args:
        url: The official URL the text came from.
        text: Pasted page text.
        cfg: App configuration.
        profile: Applicant profile.
        weights: Matching weights.
        use_ai: False forces the heuristic fallback.
        client: Optional OpenAI client (tests).
        now: Injectable clock.

    Returns:
        A :class:`ResearchResult` whose source role is ``pasted``.
    """
    fetch = build_pasted_result(url, text, cfg.max_page_chars)
    return assemble([_page_from_fetch("pasted", fetch, cfg, use_ai, client)], profile, weights, cfg, None, now)


def add_linked_page(result: ResearchResult, url: str, cfg: Config, profile: dict, weights: dict, use_ai: bool = True,
                    session: Any = None, resolver: Any = None, client: Any = None,
                    now: Optional[datetime] = None) -> ResearchResult:
    """Research a linked official page (e.g. programme application page) as extra evidence.

    Args:
        result: The existing research result to extend.
        url: Linked page URL.
        cfg: App configuration.
        profile: Applicant profile.
        weights: Matching weights.
        use_ai: False forces the heuristic fallback.
        session: Optional ``requests.Session`` (tests).
        resolver: Optional DNS resolver (tests).
        client: Optional OpenAI client (tests).
        now: Injectable clock.

    Returns:
        A new :class:`ResearchResult` including the extra page. If the linked page cannot be
        read, the failure is kept in the page list and the earlier data is unchanged.
    """
    fetch = fetch_page(url, cfg.fetch_timeout_seconds, cfg.max_response_bytes, cfg.max_page_chars, session, resolver)
    pages = list(result.pages) + [_page_from_fetch("linked", fetch, cfg, use_ai, client)]
    new = assemble(pages, profile, weights, cfg, None, now)
    if not fetch.ok:
        new.warnings.append(f"Linked page could not be read: {fetch.error}")
    return new


def sources_list(result: ResearchResult) -> list:
    """Describe every researched page for storage (URLs, timestamps, hashes, classification)."""
    out = []
    for p in result.pages:
        out.append({
            "role": p.role, "requested_url": p.fetch.requested_url, "final_url": p.fetch.final_url,
            "canonical_url": p.fetch.canonical_url, "title": p.fetch.title, "retrieved_at": p.fetch.retrieved_at,
            "ok": p.fetch.ok, "error": p.fetch.error, "content_hash": p.fetch.content_hash,
            "source_type": p.source.source_type, "source_domain": p.source.domain,
            "is_third_party": p.source.is_third_party, "likely_official": p.source.likely_official,
            "extraction_method": p.extraction.method if p.extraction else "",
            "last_checked_at": p.fetch.retrieved_at,
        })
    return out


def build_payload(result: ResearchResult, notes: str = "") -> dict:
    """Turn a successful research result into a repository payload (complete structured record).

    Args:
        result: A result with ``ok=True``.
        notes: Optional user notes.

    Returns:
        Payload for :func:`repository.add_opportunity` / ``update_opportunity_from_research``.

    Raises:
        ValueError: If the result is not ok.
    """
    if not result.ok or result.primary is None:
        raise ValueError("Cannot save a result that has no extracted data.")
    prim = result.primary
    methods = {p.extraction.method for p in result.pages if p.extraction}
    return {
        "record": result.record,
        "eligibility": result.eligibility.to_dict(),
        "match": result.match.to_dict(),
        "conflicts": result.conflicts,
        "sources": sources_list(result),
        "official_url": prim.fetch.final_url or prim.fetch.requested_url,
        "canonical_url": prim.fetch.canonical_url,
        "source_type": prim.source.source_type,
        "source_domain": prim.source.domain,
        "extraction_method": METHOD_AI if methods == {METHOD_AI} else (METHOD_FALLBACK if methods == {METHOD_FALLBACK} else "mixed"),
        "extraction_model": prim.extraction.model,
        "ai_verified": result.ai_verified,
        "retrieved_at": prim.fetch.retrieved_at,
        "notes": notes,
    }


@dataclass
class CheckResult:
    """Outcome of re-checking one saved opportunity's sources.

    Attributes:
        opportunity_id: The checked record.
        checked_urls: URLs that were read.
        changes: Detected differences (primary page, field-level).
        error: Failure text ('' if fine).
        method: ``ai`` for field comparison or ``hash_only`` when AI was unavailable.
        snapshot_id: Snapshot written when changes were found.
    """

    opportunity_id: int
    checked_urls: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    error: str = ""
    method: str = "ai"
    snapshot_id: Optional[int] = None


def check_source(db: Database, opp: dict, cfg: Config, profile: dict, weights: dict, session: Any = None,
                 resolver: Any = None, client: Any = None) -> CheckResult:
    """Re-read a saved opportunity's source pages and record differences.

    The primary page is re-extracted with AI and compared field by field with the saved
    record. Linked pages are compared by content hash only. Nothing in the saved record
    is changed; differences are stored as unapplied changes for human review.

    Args:
        db: Database.
        opp: Saved opportunity (from the repository).
        cfg: App configuration.
        profile: Applicant profile.
        weights: Matching weights.
        session: Optional ``requests.Session`` (tests).
        resolver: Optional DNS resolver (tests).
        client: Optional OpenAI client (tests).

    Returns:
        A :class:`CheckResult`. Fetch problems are reported in ``error`` and nothing is recorded.
    """
    res = CheckResult(opportunity_id=opp["id"])
    sources = [dict(s) for s in opp.get("sources", [])] or [
        {"role": "primary", "final_url": opp["official_url"], "requested_url": opp["official_url"], "content_hash": ""}]
    primary_new_record, primary_elig, primary_hash, primary_url = None, {}, "", opp["official_url"]
    now_iso = iso(utc_now())
    for src in sources:
        if src.get("role") == "pasted" or src.get("ok") is False:
            continue
        url = src.get("final_url") or src.get("requested_url")
        fetch = fetch_page(url, cfg.fetch_timeout_seconds, cfg.max_response_bytes, cfg.max_page_chars, session, resolver)
        if not fetch.ok:
            res.error = f"Could not re-read {url}: {fetch.error}"
            continue
        res.checked_urls.append(url)
        old_hash = src.get("content_hash", "")
        src["last_checked_at"] = now_iso
        hash_changed = bool(old_hash) and old_hash != fetch.content_hash
        src["content_hash"] = fetch.content_hash
        if src.get("role") == "primary":
            primary_hash, primary_url = fetch.content_hash, url
            if cfg.has_openai or client is not None:
                try:
                    ext = extract_with_ai(fetch.text, fetch.canonical_url or url, fetch.title, cfg.openai_api_key, cfg.openai_model, client)
                    primary_new_record = ext.record
                except (AIUnavailable, ExtractionError) as exc:
                    res.error = f"AI comparison failed ({exc}); only page-content changes were checked."
                    res.method = "hash_only"
            else:
                res.method = "hash_only"
            if primary_new_record is None and hash_changed:
                res.changes.append(Change("page_content", "Page content", f"hash {old_hash[:10]}", f"hash {fetch.content_hash[:10]}", True))
        elif hash_changed:
            res.changes.append(Change(f"linked_page:{url}", "Linked page content", f"hash {old_hash[:10]}", f"hash {fetch.content_hash[:10]}", True))
    new_record = primary_new_record
    if new_record is not None:
        res.changes = compare_records(opp.get("record", {}), new_record) + res.changes
        primary_elig = assess_eligibility(new_record, profile, opp.get("overrides"), True).to_dict()
    if res.checked_urls:
        res.snapshot_id = repository.record_source_check(
            db, opp["id"], new_record or opp.get("record", {}), primary_elig or opp.get("eligibility", {}),
            res.changes, primary_url, primary_hash, sources)
    return res


def apply_snapshot(db: Database, opp_id: int, snapshot_id: int, cfg: Config, profile: dict, weights: dict) -> None:
    """Apply a stored source-check snapshot to the saved record (an explicit human action).

    The previous state stays in history. A verified record becomes 'Needs review' rather than
    silently keeping its verified decision.

    Args:
        db: Database.
        opp_id: Opportunity id.
        snapshot_id: Snapshot produced by a source check.
        cfg: App configuration.
        profile: Applicant profile.
        weights: Matching weights.

    Raises:
        KeyError: If the opportunity or snapshot does not exist.
    """
    opp = repository.get_opportunity(db, opp_id)
    snap = repository.get_snapshot(db, snapshot_id)
    if opp is None or snap is None or snap["opportunity_id"] != opp_id:
        raise KeyError("opportunity or snapshot not found")
    new_record = snap["record"]
    eligibility, _, match = evaluate(new_record, profile, weights, cfg, opp.get("overrides"), bool(opp["ai_verified"]), opp.get("conflicts"))
    payload = {
        "record": new_record, "eligibility": eligibility.to_dict(), "match": match.to_dict(),
        "conflicts": opp["conflicts"], "sources": opp["sources"], "official_url": opp["official_url"],
        "canonical_url": opp["canonical_url"], "source_type": opp["source_type"], "source_domain": opp["source_domain"],
        "extraction_method": opp["extraction_method"], "extraction_model": opp["extraction_model"],
        "ai_verified": bool(opp["ai_verified"]), "retrieved_at": snap["taken_at"],
    }
    repository.update_opportunity_from_research(db, opp_id, payload, [], reason=f"applied snapshot {snapshot_id}")
    repository.mark_changes_applied(db, snapshot_id)


def recompute_all(db: Database, cfg: Config, profile: dict, weights: dict) -> int:
    """Recompute eligibility and match for every saved record (after Settings changed).

    Returns:
        Number of records refreshed. Saved extracted data is not changed.
    """
    count = 0
    for opp in repository.list_opportunities(db):
        if not opp.get("record"):
            continue
        elig, _, match = evaluate(opp["record"], profile, weights, cfg, opp.get("overrides"), bool(opp["ai_verified"]), opp.get("conflicts"))
        repository.refresh_scores(db, opp["id"], elig.to_dict(), match.to_dict())
        count += 1
    return count
