"""Safe fetching: URL validation, redirects, errors, size limits, text extraction."""

import pytest
import requests

from scholarship_agent.fetcher import FetchError, build_pasted_result, fetch_page, validate_url
from tests.conftest import LONG_HTML, PUBLIC_RESOLVER, FakeResponse, FakeSession


@pytest.mark.parametrize("url", [
    "ftp://example.com/a", "file:///etc/passwd", "javascript:alert(1)", "https://user:pw@example.com/",
    "http://example.com:8080/", "https://example.com/setup.exe", "", "https://",
])
def test_rejects_unsafe_urls(url):
    with pytest.raises(FetchError):
        validate_url(url, PUBLIC_RESOLVER)


@pytest.mark.parametrize("url", [
    "http://localhost/", "http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data",
    "http://[::1]/", "http://printer.local/",
])
def test_rejects_local_and_private_destinations(url):
    with pytest.raises(FetchError) as err:
        validate_url(url, PUBLIC_RESOLVER)
    assert err.value.code in ("blocked_host", "invalid_url")


def test_rejects_hostname_resolving_to_private_ip():
    with pytest.raises(FetchError) as err:
        validate_url("https://evil.example.com/", lambda h: ["10.1.2.3"])
    assert err.value.code == "blocked_host"


def test_successful_fetch_extracts_text_title_canonical_links():
    s = FakeSession([FakeResponse(200, LONG_HTML)])
    r = fetch_page("https://uni.example.edu/s", session=s, resolver=PUBLIC_RESOLVER)
    assert r.ok and r.title == "Test Scholarship" and r.canonical_url == "https://uni.example.edu/s"
    assert "alert(1)" not in r.text and "Master's students" in r.text
    urls = [l["url"] for l in r.links]
    assert "https://uni.example.edu/eligibility" in urls          # same-site eligibility link
    assert not any("a.zip" in u or "other.com" in u for u in urls)  # executables and off-site excluded
    assert r.retrieved_at and r.content_hash


def test_redirect_to_private_address_is_blocked():
    s = FakeSession([FakeResponse(302, headers={"Location": "http://127.0.0.1/admin"})])
    r = fetch_page("https://uni.example.edu/s", session=s, resolver=lambda h: ["93.184.216.34"] if "example" in h else ["127.0.0.1"])
    assert not r.ok and r.error_code == "blocked_host"


def test_redirect_followed_and_recorded():
    s = FakeSession([FakeResponse(301, headers={"Location": "/new"}), FakeResponse(200, LONG_HTML)])
    r = fetch_page("https://uni.example.edu/old", session=s, resolver=PUBLIC_RESOLVER)
    assert r.ok and r.final_url == "https://uni.example.edu/new" and any("Redirected" in w for w in r.warnings)


def test_too_many_redirects():
    s = FakeSession([FakeResponse(302, headers={"Location": "/again"})] * 10)
    r = fetch_page("https://uni.example.edu/loop", session=s, resolver=PUBLIC_RESOLVER)
    assert not r.ok and r.error_code == "too_many_redirects"


@pytest.mark.parametrize("status,code", [(404, "http_404"), (403, "http_403"), (500, "http_500"), (429, "http_429")])
def test_http_errors_reported_with_no_text(status, code):
    r = fetch_page("https://uni.example.edu/s", session=FakeSession([FakeResponse(status)]), resolver=PUBLIC_RESOLVER)
    assert not r.ok and r.error_code == code and r.text == "" and r.error


@pytest.mark.parametrize("exc,code", [
    (requests.exceptions.Timeout(), "timeout"), (requests.exceptions.SSLError(), "ssl_error"),
    (requests.exceptions.ConnectionError(), "connection_error"),
])
def test_network_failures_are_graceful(exc, code):
    r = fetch_page("https://uni.example.edu/s", session=FakeSession([exc]), resolver=PUBLIC_RESOLVER)
    assert not r.ok and r.error_code == code


def test_javascript_only_page_reported_not_invented():
    html = b"<html><head><title>App</title></head><body><div id='root'></div><script>render()</script></body></html>"
    r = fetch_page("https://uni.example.edu/s", session=FakeSession([FakeResponse(200, html)]), resolver=PUBLIC_RESOLVER)
    assert not r.ok and r.error_code == "no_readable_text" and "JavaScript" in r.error


def test_pdf_and_binary_content_types_refused():
    r = fetch_page("https://uni.example.edu/s", session=FakeSession([FakeResponse(200, b"%PDF", {"Content-Type": "application/pdf"})]), resolver=PUBLIC_RESOLVER)
    assert not r.ok and r.error_code == "unsupported_type"


def test_size_limit_enforced():
    big = b"<html><body><p>" + b"word " * 500_000 + b"</p></body></html>"
    r = fetch_page("https://uni.example.edu/s", max_bytes=50_000, session=FakeSession([FakeResponse(200, big)]), resolver=PUBLIC_RESOLVER)
    assert r.ok and any("size limit" in w for w in r.warnings)


def test_pasted_text_requires_enough_text_and_valid_url():
    assert build_pasted_result("https://uni.example.edu", "short").error_code == "too_short"
    assert build_pasted_result("nonsense", "x" * 300).error_code == "invalid_url"
    ok = build_pasted_result("https://uni.example.edu", "Scholarship text. " * 30)
    assert ok.ok and any("pasted by you" in w for w in ok.warnings)
