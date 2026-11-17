"""Source classification: official vs third-party, recorded as source type and domain.

Classification is a *heuristic* based on the domain name. It is shown to the user
as "likely official" or "third-party" and can be overridden in Review & Verify.
It is never presented as verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .utils import domain_of

SOURCE_TYPES = [
    "government",
    "university",
    "scholarship_provider",
    "third_party",
    "unverified",
]

# Domains of well-known third-party aggregators/portals (NOT the scholarship owners).
THIRD_PARTY_DOMAINS = {
    "scholars4dev.com", "mastersportal.com", "scholarshipportal.com", "studyportals.com",
    "bachelorsportal.com", "opportunitiesforyouth.org", "scholarshipdb.net", "findamasters.com",
    "topuniversities.com", "youthop.com", "opportunitydesk.org", "scholarshipsads.com",
    "scholarship-positions.com", "afterschoolafrica.com", "scholarshiproar.com", "educations.com",
    "studyabroad.shiksha.com", "collegedunia.com", "scholarshipscorner.website", "iefa.org",
    "fastweb.com", "scholarships.com", "reddit.com", "quora.com", "facebook.com", "youtube.com",
    "linkedin.com", "wikipedia.org", "medium.com", "blogspot.com", "wordpress.com", "studyin.com",
}

# Well-known scholarship providers/official study portals (heuristic allow-list).
KNOWN_PROVIDER_DOMAINS = {
    "daad.de", "chevening.org", "fulbright.org", "fulbrightprogram.org", "campuschina.org",
    "csc.edu.cn", "studyinjapan.go.jp", "jasso.go.jp", "studyinkorea.go.kr", "niied.go.kr",
    "si.se", "studyinsweden.se", "studyinfinland.fi", "studyinnorway.no", "studyinholland.nl",
    "nuffic.nl", "erasmus-plus.ec.europa.eu", "eacea.ec.europa.eu", "campusfrance.org",
    "gatescambridge.org", "rhodeshouse.ox.ac.uk", "commonwealthscholarships.org", "cscuk.fcdo.gov.uk",
    "australiaawards.gov.au", "studyinaustralia.gov.au", "educationnewzealand.govt.nz",
    "studyinestonia.ee", "studyinhungary.hu", "stipendiumhungaricum.hu", "swissuniversities.ch",
    "scholarships.gov.sg", "isdb.org", "kaust.edu.sa", "mext.go.jp", "studyin.cz", "aauw.org",
}

_GOV_RE = re.compile(r"(\.gov$|\.gov\.[a-z]{2}$|\.govt\.[a-z]{2}$|\.gob\.[a-z]{2}$|\.go\.[a-z]{2}$|\.europa\.eu$)")
_UNI_RE = re.compile(r"(\.edu$|\.edu\.[a-z]{2}$|\.ac\.[a-z]{2}$|(^|\.)uni-[a-z0-9\-]+\.[a-z]{2,}$)")


@dataclass(frozen=True)
class SourceInfo:
    """Classification of one source URL.

    Attributes:
        domain: Host without ``www.``.
        source_type: One of :data:`SOURCE_TYPES`.
        is_third_party: True when the domain is a known aggregator/social/blog host.
        likely_official: True for government, university or known provider domains.
        note: Short explanation shown in the UI.
    """

    domain: str
    source_type: str
    is_third_party: bool
    likely_official: bool
    note: str


def _matches(domain: str, candidates: set) -> bool:
    """Return True if ``domain`` equals or is a subdomain of any candidate."""
    return any(domain == c or domain.endswith("." + c) for c in candidates)


def classify_source(url: str, override: Optional[str] = None) -> SourceInfo:
    """Classify a URL by domain.

    Args:
        url: The source URL.
        override: Optional user-chosen source type that replaces the heuristic.

    Returns:
        A :class:`SourceInfo`. Unrecognised domains are ``unverified`` (neither
        confirmed official nor flagged third-party) so the UI asks for review.
    """
    domain = domain_of(url)
    if override in SOURCE_TYPES:
        return SourceInfo(
            domain, override, override == "third_party",
            override in ("government", "university", "scholarship_provider"),
            "Source type set manually by the user.",
        )
    if not domain:
        return SourceInfo("", "unverified", False, False, "No valid domain.")
    if _matches(domain, THIRD_PARTY_DOMAINS):
        return SourceInfo(domain, "third_party", True, False,
                          "Third-party site. Treat as a lead only and confirm on the official page.")
    if _GOV_RE.search(domain):
        return SourceInfo(domain, "government", False, True, "Government domain (heuristic).")
    if _UNI_RE.search(domain):
        return SourceInfo(domain, "university", False, True, "University domain (heuristic).")
    if _matches(domain, KNOWN_PROVIDER_DOMAINS):
        return SourceInfo(domain, "scholarship_provider", False, True, "Known scholarship provider domain (heuristic).")
    return SourceInfo(domain, "unverified", False, False,
                      "Could not confirm this is an official site. Check who owns the domain.")
