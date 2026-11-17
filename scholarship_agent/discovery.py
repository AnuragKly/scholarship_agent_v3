"""Scholarship discovery: query building and the Tavily search integration.

Search results are only *leads*. Each must be opened and read via the fetcher
before anything is extracted; snippets are never treated as facts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

from .sources import classify_source
from .utils import normalize_url, safe_error

logger = logging.getLogger("scholarship_agent.discovery")
TAVILY_URL = "https://api.tavily.com/search"

FUNDING_PHRASES = {
    "fully_funded": "fully funded",
    "partial": "partial scholarship",
    "any": "scholarship",
}


class SearchUnavailable(Exception):
    """Raised when search cannot run (missing key, network, provider error); message is user-safe."""


@dataclass
class SearchResult:
    """One search hit (a lead, not a verified record).

    Attributes:
        title: Result title from the search provider.
        url: Result URL.
        snippet: Provider snippet (untrusted, for display only).
        query: The query that produced this hit.
        normalized_url: Normalised URL for duplicate checks.
        domain: Host name.
        source_type: Heuristic source type (see ``sources.py``).
        likely_official: Heuristic flag.
        is_third_party: True for known aggregators.
        source_note: Short explanation of the classification.
    """

    title: str
    url: str
    snippet: str
    query: str
    normalized_url: str = ""
    domain: str = ""
    source_type: str = "unverified"
    likely_official: bool = False
    is_third_party: bool = False
    source_note: str = ""
    extra: dict = field(default_factory=dict)


def build_queries(
    subjects: list,
    degree: str = "Master's",
    countries: Optional[list] = None,
    funding: str = "fully_funded",
    cycle: str = "",
    nationality: str = "Nepali",
    max_queries: int = 6,
) -> list:
    """Build search queries that explicitly target Nepal-eligible scholarships.

    Args:
        subjects: Subject names (e.g. ``["Applied Mathematics", "Data Science"]``).
        degree: Degree level wording.
        countries: Optional destination countries (one query per country/subject pair).
        funding: ``fully_funded``, ``partial`` or ``any``.
        cycle: Application cycle/year such as ``2027`` (optional).
        nationality: Applicant nationality wording.
        max_queries: Upper bound on queries returned, to limit API cost.

    Returns:
        A de-duplicated list of query strings, each mentioning Nepal/Nepalese applicants.
    """
    funding_phrase = FUNDING_PHRASES.get(funding, "scholarship")
    cycle = (cycle or "").strip()
    nat = (nationality or "Nepali").strip()
    nepal_terms = "Nepalese students" if nat.lower() in ("nepali", "nepalese", "nepal") else f"{nat} students"
    subjects = [s.strip() for s in subjects if s and s.strip()] or ["Mathematics"]
    countries = [c.strip() for c in (countries or []) if c and c.strip()]
    queries: list = []
    if countries:
        for c in countries:
            for s in subjects:
                queries.append(f"{funding_phrase} {degree} {s} {c} for {nepal_terms} {cycle}".strip())
    else:
        for s in subjects:
            queries.append(f"{funding_phrase} {degree} {s} scholarship open to {nepal_terms} {cycle}".strip())
            queries.append(f"{degree} {s} {funding_phrase} Nepal eligible official application {cycle}".strip())
    seen, unique = set(), []
    for q in queries:
        q = " ".join(q.split())
        if q.lower() not in seen:
            seen.add(q.lower())
            unique.append(q)
    return unique[:max_queries]


def search_tavily(
    query: str,
    api_key: str,
    max_results: int = 8,
    timeout: float = 20.0,
    session: Optional[requests.Session] = None,
) -> list:
    """Run one Tavily search and return classified results.

    Args:
        query: Search query.
        api_key: Tavily API key (sent as a bearer token; never logged).
        max_results: Number of results requested (1-20).
        timeout: Request timeout in seconds.
        session: Optional ``requests.Session`` (injected in tests).

    Returns:
        A list of :class:`SearchResult` (possibly empty).

    Raises:
        SearchUnavailable: Missing key, network error, rejected key, rate limit or
            malformed response. The message is safe to show to the user.
    """
    if not (api_key or "").strip():
        raise SearchUnavailable("Search is not configured. Add TAVILY_API_KEY to your .env file, or paste an official URL instead.")
    sess = session or requests.Session()
    try:
        resp = sess.post(
            TAVILY_URL,
            json={"query": query, "max_results": max(1, min(int(max_results), 20)), "search_depth": "basic"},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.exceptions.Timeout as exc:
        raise SearchUnavailable("The search service timed out. Try again in a moment.") from exc
    except requests.exceptions.RequestException as exc:
        raise SearchUnavailable("Could not reach the search service. Check your internet connection.") from exc
    if resp.status_code in (401, 403):
        raise SearchUnavailable("The search service rejected the API key. Check TAVILY_API_KEY in your .env file.")
    if resp.status_code == 429:
        raise SearchUnavailable("Search rate limit or quota reached. Wait a while or check your Tavily plan.")
    if resp.status_code >= 400:
        raise SearchUnavailable(f"The search service returned an error (HTTP {resp.status_code}).")
    try:
        payload = resp.json()
        items = payload.get("results", [])
        if not isinstance(items, list):
            raise ValueError("results is not a list")
    except ValueError as exc:
        logger.warning("Malformed search response: %s", safe_error(exc))
        raise SearchUnavailable("The search service sent an unreadable response.") from exc

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        info = classify_source(url)
        results.append(
            SearchResult(
                title=str(item.get("title") or url)[:200],
                url=url,
                snippet=str(item.get("content") or "")[:500],
                query=query,
                normalized_url=normalize_url(url),
                domain=info.domain,
                source_type=info.source_type,
                likely_official=info.likely_official,
                is_third_party=info.is_third_party,
                source_note=info.note,
            )
        )
    return results


def search_many(queries: list, api_key: str, max_results: int = 8, session: Optional[requests.Session] = None) -> tuple:
    """Run several queries and merge results by normalised URL.

    Args:
        queries: Query strings.
        api_key: Tavily API key.
        max_results: Results per query.
        session: Optional ``requests.Session``.

    Returns:
        ``(results, errors)``: de-duplicated results (official-looking first) and a
        list of user-safe error messages for queries that failed. If every query
        fails, :class:`SearchUnavailable` is raised.
    """
    merged: dict = {}
    errors: list = []
    for q in queries:
        try:
            for r in search_tavily(q, api_key, max_results, session=session):
                merged.setdefault(r.normalized_url, r)
        except SearchUnavailable as exc:
            errors.append(str(exc))
            if "not configured" in str(exc) or "rejected the API key" in str(exc):
                raise
    if not merged and errors and len(errors) == len(queries):
        raise SearchUnavailable(errors[0])
    ordered = sorted(merged.values(), key=lambda r: (not r.likely_official, r.is_third_party))
    return ordered, sorted(set(errors))
