"""Safe webpage fetching and readable-text extraction.

Safety measures: only HTTP/HTTPS on ports 80/443, no embedded credentials,
hostnames resolved and rejected when they point to loopback/private/link-local
ranges (re-checked on every redirect hop), response-size and time limits,
executable/binary file types refused. Failures are reported, never papered over.

Known limitation: pages that need JavaScript to render their content cannot be
read; they are reported as failures so nothing is invented.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .utils import collapse_whitespace, domain_of, normalize_url, safe_error, utc_now, iso

logger = logging.getLogger("scholarship_agent.fetcher")

USER_AGENT = "ScholarshipResearchAgent/3.0 (personal research tool; respects size/time limits)"
MAX_REDIRECTS = 5
MIN_READABLE_CHARS = 200
BLOCKED_EXTENSIONS = {
    ".exe", ".msi", ".dmg", ".pkg", ".apk", ".bat", ".cmd", ".sh", ".jar", ".zip", ".rar", ".7z",
    ".tar", ".gz", ".iso", ".bin", ".dll", ".scr", ".ps1", ".js", ".docm", ".xlsm",
}
UNSUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".gif", ".mp4"}
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_LINK_KEYWORDS = {
    "eligib": 5, "how-to-apply": 5, "how to apply": 5, "apply": 4, "application": 4, "admission": 4,
    "requirement": 4, "deadline": 4, "funding": 3, "scholarship": 3, "fees": 2, "master": 2,
}
_SECOND_LEVEL = {"co", "ac", "edu", "gov", "com", "org", "net", "go", "govt"}

Resolver = Callable[[str], list]


class FetchError(Exception):
    """A fetch failure with a machine-readable code and a user-friendly message.

    Attributes:
        code: Short identifier (``invalid_url``, ``blocked_host``, ``timeout``, ``http_404`` ...).
        message: Actionable explanation for the UI.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class FetchResult:
    """Outcome of fetching one page.

    Attributes:
        ok: True only when readable text was obtained.
        requested_url: URL as requested by the user/search.
        final_url: URL after redirects.
        canonical_url: ``<link rel=canonical>`` if present and valid, else ``final_url``.
        title: Page title ('' if none).
        text: Readable page text ('' on failure).
        retrieved_at: UTC ISO timestamp of retrieval.
        status_code: HTTP status code (0 if no response).
        error_code: Failure code ('' on success).
        error: Actionable failure message ('' on success).
        links: Candidate eligibility/application links found on the page.
        content_hash: SHA-256 of the text, used for cheap change detection.
        warnings: Non-fatal notes (truncation, redirects).
    """

    ok: bool
    requested_url: str
    final_url: str = ""
    canonical_url: str = ""
    title: str = ""
    text: str = ""
    retrieved_at: str = ""
    status_code: int = 0
    error_code: str = ""
    error: str = ""
    links: list = field(default_factory=list)
    content_hash: str = ""
    warnings: list = field(default_factory=list)


def default_resolver(host: str) -> list:
    """Resolve a hostname to a list of IP address strings (all A/AAAA records).

    Raises:
        FetchError: With code ``dns_failed`` if the name cannot be resolved.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError("dns_failed", f"Could not find the website '{host}'. Check the address for typos.") from exc
    return sorted({info[4][0] for info in infos})


def _is_public_ip(ip_text: str) -> bool:
    """Return True only for globally routable addresses."""
    try:
        ip = ipaddress.ip_address(ip_text.split("%")[0])
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def validate_url(url: str, resolver: Optional[Resolver] = None) -> str:
    """Validate that a URL is safe to fetch.

    Args:
        url: Candidate URL.
        resolver: Function mapping hostname to IP strings (injectable for tests).

    Returns:
        The stripped URL.

    Raises:
        FetchError: ``invalid_url`` for bad scheme/format/credentials/port/extension,
            ``blocked_host`` for localhost or private/reserved addresses.
    """
    resolver = resolver or default_resolver
    url = (url or "").strip()
    if not url:
        raise FetchError("invalid_url", "Please enter a web address.")
    if len(url) > 2000:
        raise FetchError("invalid_url", "That web address is too long.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError("invalid_url", "That does not look like a valid web address.") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise FetchError("invalid_url", "Only http:// and https:// addresses are allowed.")
    host = (parts.hostname or "").lower()
    if not host:
        raise FetchError("invalid_url", "The web address has no website name.")
    if parts.username or parts.password:
        raise FetchError("invalid_url", "Addresses containing a username or password are not allowed.")
    if port not in (None, 80, 443):
        raise FetchError("invalid_url", "Only the standard web ports (80/443) are allowed.")
    path = parts.path.lower()
    for ext in BLOCKED_EXTENSIONS:
        if path.endswith(ext):
            raise FetchError("blocked_file", f"Files of type '{ext}' are not downloaded for safety.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home")):
        raise FetchError("blocked_host", "Local or internal addresses are not allowed.")
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        ips = resolver(host)
    if not ips:
        raise FetchError("dns_failed", f"Could not find the website '{host}'.")
    for ip in ips:
        if not _is_public_ip(ip):
            raise FetchError("blocked_host", "This address points to a private or reserved network and was blocked.")
    return url


def _site_root(host: str) -> str:
    """Return an approximate registrable domain (e.g. ``ox.ac.uk``) for same-site checks."""
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def find_candidate_links(soup: BeautifulSoup, base_url: str, limit: int = 8) -> list:
    """Find links on the page likely to hold eligibility/application details.

    Only same-site HTTP(S) links to non-document pages are returned.

    Args:
        soup: Parsed page.
        base_url: Final URL of the page, for resolving relative links.
        limit: Maximum number of links.

    Returns:
        A list of ``{"url", "text"}`` dicts ordered by relevance.
    """
    base_root = _site_root(domain_of(base_url))
    base_norm = normalize_url(base_url)
    scored: dict = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        ext_path = parts.path.lower()
        if any(ext_path.endswith(e) for e in BLOCKED_EXTENSIONS | UNSUPPORTED_EXTENSIONS):
            continue
        if _site_root(domain_of(absolute)) != base_root:
            continue
        norm = normalize_url(absolute)
        if not norm or norm == base_norm:
            continue
        label = collapse_whitespace(a.get_text(" "))[:120]
        haystack = f"{label} {parts.path}".lower()
        score = max((w for k, w in _LINK_KEYWORDS.items() if k in haystack), default=0)
        if score and (norm not in scored or scored[norm][0] < score):
            scored[norm] = (score, absolute, label)
    ranked = sorted(scored.values(), key=lambda x: -x[0])[:limit]
    return [{"url": u, "text": t or u} for _, u, t in ranked]


def extract_readable(html_bytes: bytes, base_url: str, max_chars: int) -> tuple:
    """Extract title, canonical URL, readable text and candidate links from HTML.

    Args:
        html_bytes: Raw page bytes.
        base_url: Final page URL.
        max_chars: Maximum text length kept.

    Returns:
        ``(title, canonical_url, text, links, truncated, script_count)``.
    """
    soup = BeautifulSoup(html_bytes, "html.parser")
    title = collapse_whitespace(soup.title.get_text(" ")) if soup.title else ""
    canonical = ""
    link = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    if link and link.get("href"):
        cand = urljoin(base_url, link["href"].strip())
        if urlsplit(cand).scheme in ("http", "https"):
            canonical = cand
    links = find_candidate_links(soup, base_url)
    script_count = len(soup.find_all("script"))
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "aside", "template"]):
        tag.decompose()
    lines = [collapse_whitespace(line) for line in soup.get_text("\n").splitlines()]
    text = "\n".join(line for line in lines if line)
    truncated = len(text) > max_chars
    return title, canonical, text[:max_chars], links, truncated, script_count


def fetch_page(
    url: str,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
    max_chars: int = 60_000,
    session: Optional[requests.Session] = None,
    resolver: Optional[Resolver] = None,
) -> FetchResult:
    """Fetch one webpage safely and return readable text.

    Args:
        url: Page URL (must pass :func:`validate_url`).
        timeout: Read timeout in seconds.
        max_bytes: Maximum response bytes read.
        max_chars: Maximum characters of text kept.
        session: Optional ``requests.Session`` (injected in tests).
        resolver: Optional DNS resolver (injected in tests).

    Returns:
        A :class:`FetchResult`. This function never raises for network or content
        problems: it returns ``ok=False`` with ``error_code`` and ``error`` instead.
    """
    result = FetchResult(ok=False, requested_url=url, retrieved_at=iso(utc_now()))
    sess = session or requests.Session()
    current = url
    try:
        for hop in range(MAX_REDIRECTS + 1):
            current = validate_url(current, resolver)
            resp = sess.get(
                current, stream=True, timeout=(5, timeout), allow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"},
            )
            try:
                result.status_code = resp.status_code
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise FetchError("bad_redirect", "The website redirected without saying where.")
                    current = urljoin(current, location)
                    result.warnings.append(f"Redirected to {current}")
                    continue
                if resp.status_code in (401, 403, 429):
                    raise FetchError(
                        f"http_{resp.status_code}",
                        f"The website refused automated access (HTTP {resp.status_code}). "
                        "Open the page in your browser and paste its text using 'Paste page text'.",
                    )
                if resp.status_code >= 400:
                    raise FetchError(f"http_{resp.status_code}", f"The page returned an error (HTTP {resp.status_code}). Check the address.")
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype and not ctype.startswith(ALLOWED_CONTENT_TYPES):
                    hint = " PDF files are not supported yet; open the PDF and paste its text." if "pdf" in ctype else ""
                    raise FetchError("unsupported_type", f"This is not a web page (content type '{ctype}').{hint}")
                declared = resp.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > max_bytes * 5:
                    raise FetchError("too_large", "The page is too large to read safely.")
                chunks, size = [], 0
                for chunk in resp.iter_content(8192):
                    size += len(chunk)
                    chunks.append(chunk)
                    if size > max_bytes:
                        result.warnings.append("The page was larger than the size limit; only the first part was read.")
                        break
                raw = b"".join(chunks)[: max_bytes]
            finally:
                resp.close()
            break
        else:
            raise FetchError("too_many_redirects", "The website redirected too many times.")

        title, canonical, text, links, truncated, script_count = extract_readable(raw, current, max_chars)
        if truncated:
            result.warnings.append("Page text was long and was cut to the configured limit.")
        result.final_url = current
        result.title = title
        result.canonical_url = canonical or current
        result.links = links
        if len(text) < MIN_READABLE_CHARS:
            raise FetchError(
                "no_readable_text",
                "Very little readable text was found. The page may need JavaScript to display its content "
                "(not supported) or may be empty. Open it in your browser and use 'Paste page text'.",
            )
        result.text = text
        result.content_hash = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        result.ok = True
        return result
    except FetchError as exc:
        result.error_code, result.error = exc.code, exc.message
    except requests.exceptions.Timeout:
        result.error_code, result.error = "timeout", "The website took too long to respond. Try again later."
    except requests.exceptions.SSLError:
        result.error_code, result.error = "ssl_error", "The website's security certificate could not be verified."
    except requests.exceptions.ConnectionError:
        result.error_code, result.error = "connection_error", "Could not connect to the website. Check your internet connection and the address."
    except requests.exceptions.RequestException as exc:
        result.error_code, result.error = "request_error", f"The request failed ({exc.__class__.__name__})."
    except Exception as exc:  # last-resort guard so the UI never crashes
        logger.exception("Unexpected fetch failure")
        result.error_code, result.error = "unexpected", f"Unexpected problem while reading the page: {safe_error(exc)}"
    result.final_url = result.final_url or current
    logger.info("Fetch failed (%s) for %s", result.error_code, domain_of(url))
    return result


def build_pasted_result(url: str, text: str, max_chars: int = 60_000) -> FetchResult:
    """Wrap user-pasted page text as a :class:`FetchResult`.

    Used when a page cannot be fetched (blocked, JavaScript-only). The result is
    marked with a warning so the record is known to rest on user-supplied text.

    Args:
        url: The official URL the text came from (validated only for scheme/format).
        text: Pasted page text.
        max_chars: Maximum characters kept.

    Returns:
        A ``FetchResult`` (``ok=False`` with ``too_short`` if the text is under 200 characters).
    """
    result = FetchResult(ok=False, requested_url=url, final_url=url, canonical_url=url, retrieved_at=iso(utc_now()))
    try:
        parts = urlsplit((url or "").strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError
    except ValueError:
        result.error_code, result.error = "invalid_url", "Enter the official page address that the pasted text came from."
        return result
    text = "\n".join(collapse_whitespace(l) for l in (text or "").splitlines() if l.strip())[:max_chars]
    if len(text) < MIN_READABLE_CHARS:
        result.error_code, result.error = "too_short", "The pasted text is too short to analyse (need at least 200 characters)."
        return result
    result.ok, result.text, result.title = True, text, "(text pasted by user)"
    result.content_hash = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    result.warnings.append("This text was pasted by you and was not fetched by the app; verify it is from the official page.")
    return result
