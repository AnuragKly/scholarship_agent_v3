"""Shared fixtures. No test makes a live network or paid API call."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scholarship_agent.config import Config  # noqa: E402
from scholarship_agent.database import Database  # noqa: E402
from scholarship_agent.matching import DEFAULT_WEIGHTS  # noqa: E402
from scholarship_agent.record import empty_record  # noqa: E402
from scholarship_agent.settings import DEFAULT_PROFILE  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    """A fresh, migrated database in a temporary folder."""
    return Database(tmp_path / "test.db")


@pytest.fixture()
def cfg(tmp_path):
    """A Config with no API keys."""
    return Config(db_path=tmp_path / "test.db")


@pytest.fixture()
def profile():
    """The default applicant profile (no GPA/English/experience supplied)."""
    return dict(DEFAULT_PROFILE)


@pytest.fixture()
def weights():
    """Default matching weights."""
    return dict(DEFAULT_WEIGHTS)


def make_record(**over) -> dict:
    """Build a full record with the given overrides."""
    rec = empty_record()
    rec.update(over)
    return rec


@pytest.fixture()
def good_record():
    """A well-populated Master's record that explicitly names Nepal."""
    return make_record(
        name="Example Applied Maths Scholarship", provider="Example University", country="Germany",
        degree_level="master", funding_type="fully_funded", subject_areas=["Applied Mathematics", "Data Science"],
        eligible_programmes=["MSc Applied Mathematics"], nepal_eligibility="explicitly_eligible",
        nepal_eligibility_evidence="Applicants from Nepal are eligible.", eligible_nationalities=["Nepal", "India"],
        academic_requirements="A bachelor's degree in mathematics or a related field.",
        min_gpa="3.0/4.0", language_requirements=[{"test": "IELTS", "minimum_score": "6.5", "notes": ""}],
        work_experience="not required", application_deadline="15 January 2027", deadline_date="2027-01-15",
        deadline_timezone="CET", application_cycle="2027/28", summary="A summary.",
        evidence=[{"field": "nepal_eligibility", "quote": "Applicants from Nepal are eligible.", "source_url": "https://uni.example.edu/s"}],
    )


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status=200, body=b"", headers=None, json_data=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self._json = json_data

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def close(self):
        pass


class FakeSession:
    """Session that returns queued responses (or raises queued exceptions) and records requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def _next(self, url):
        self.requests.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kw):
        return self._next(url)

    def post(self, url, **kw):
        return self._next(url)


PUBLIC_RESOLVER = lambda host: ["93.184.216.34"]  # noqa: E731

LONG_HTML = ("<html><head><title>Test Scholarship</title><link rel='canonical' href='https://uni.example.edu/s'></head><body>"
             "<h1>Scholarship</h1><p>" + "This scholarship supports Master's students in mathematics. " * 12 +
             "</p><a href='/eligibility'>Eligibility</a><a href='/files/a.zip'>zip</a><a href='https://other.com/x'>apply</a>"
             "<script>alert(1)</script></body></html>").encode()
