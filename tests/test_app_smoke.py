"""Startup smoke test: every page renders with and without keys, on an empty and a populated database."""

import pytest
from streamlit.testing.v1 import AppTest

from scholarship_agent import repository as repo
from scholarship_agent.database import Database
from tests.conftest import make_record
from tests.test_database import payload

import streamlit as st


@pytest.fixture(autouse=True)
def fresh_app_cache():
    """The app caches its database handle per process; clear it so each test uses its own database."""
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


PAGES = ["Dashboard", "Discover", "Saved Opportunities", "Review & Verify", "Change History", "Settings"]


def run_all_pages(monkeypatch, tmp_path, populate=False, key=""):
    db_path = tmp_path / "smoke.db"
    monkeypatch.setenv("SCHOLARSHIP_DB_PATH", str(db_path))
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("TAVILY_API_KEY", key)
    if populate:
        db = Database(db_path)
        rec = make_record(name="Smoke Scholarship", provider="P", country="Germany", funding_type="fully_funded", deadline_date="2027-05-01",
                          subject_areas=["Data Science"], evidence=[{"field": "x", "quote": "q", "source_url": "https://uni.example.edu/s", "quote_found": True}])
        repo.add_opportunity(db, payload(rec))
        repo.add_opportunity(db, payload(make_record(name="Mostly Unknown"), url="https://b.edu/2"))
    at = AppTest.from_file(str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    for page in PAGES:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, [e.value for e in at.exception])
    return at


@pytest.mark.parametrize("populate", [False, True])
def test_all_pages_render_without_keys(monkeypatch, tmp_path, populate):
    run_all_pages(monkeypatch, tmp_path, populate)


def test_all_pages_render_with_keys_configured(monkeypatch, tmp_path):
    run_all_pages(monkeypatch, tmp_path, True, key="sk-test-key-not-real-0000")


def test_secrets_never_displayed(monkeypatch, tmp_path):
    at = run_all_pages(monkeypatch, tmp_path, True, key="sk-test-key-not-real-0000")
    everything = " ".join(str(getattr(el, "value", "")) for el in at.main) + " ".join(str(getattr(el, "value", "")) for el in at.sidebar)
    assert "sk-test-key-not-real-0000" not in everything


def test_add_opportunity_click_saves_record_and_shows_id(monkeypatch, tmp_path):
    """Drive the real Discover page: a researched result is shown, Add Opportunity saves it and reports the database ID."""
    import json
    from dataclasses import replace
    from pathlib import Path
    from types import SimpleNamespace

    from scholarship_agent import pipeline
    from scholarship_agent.config import load_config
    from scholarship_agent.record import empty_record
    from scholarship_agent.settings import DEFAULT_PROFILE
    from tests.conftest import PUBLIC_RESOLVER, FakeResponse, FakeSession
    from tests.test_pipeline import GOOD, PAGE_HTML, ai_client

    db_path = tmp_path / "click.db"
    monkeypatch.setenv("SCHOLARSHIP_DB_PATH", str(db_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    cfg = replace(load_config({"SCHOLARSHIP_DB_PATH": str(db_path)}), openai_api_key="sk-test")
    result = pipeline.research_url("https://uni.example.edu/s", cfg, dict(DEFAULT_PROFILE), {"subject": 30, "funding": 20, "nationality": 20, "academic": 10, "deadline": 10, "programme": 10},
                                   True, FakeSession([FakeResponse(200, PAGE_HTML)]), PUBLIC_RESOLVER, ai_client(**GOOD))
    assert result.ok
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=60).run()
    at.sidebar.radio[0].set_value("Discover").run()
    at.session_state["research"] = result
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    add = [b for b in at.button if b.label == "Add Opportunity"]
    assert add, [b.label for b in at.button]
    assert Database(db_path).query("SELECT COUNT(*) AS n FROM opportunities")[0]["n"] == 0      # nothing saved before the click
    add[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    rows = Database(db_path).query("SELECT id, name, ai_verified, extraction_method FROM opportunities")
    assert len(rows) == 1 and rows[0]["name"] == "Example Scholarship" and rows[0]["extraction_method"] == "ai"
    assert any(f"#{rows[0]['id']}" in s.value for s in at.success)
    # researching the same page again now offers update-existing rather than a second copy
    at.session_state["saved_id"] = None
    at.run()
    assert not [b for b in at.button if b.label == "Add Opportunity"]
    assert [b for b in at.button if b.label.startswith("Update existing record")]
