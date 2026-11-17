"""Migrations (incl. legacy preservation), CRUD, duplicates, change tracking, filters, discovery. No live APIs."""

import sqlite3
from datetime import datetime, timezone

import pytest

from scholarship_agent import repository as repo
from scholarship_agent.changes import compare_records
from scholarship_agent.database import Database
from scholarship_agent.dedup import find_duplicates
from scholarship_agent.discovery import SearchUnavailable, build_queries, search_many, search_tavily
from scholarship_agent.filters import apply_filters, enrich, export_csv, facet_options, summarize
from scholarship_agent.migrations import Migration, run_migrations
from scholarship_agent.settings import DEFAULT_PROFILE, load_profile, load_weights, reset_to_defaults, save_profile, save_weights
from tests.conftest import FakeResponse, FakeSession, make_record

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)


def payload(record, url="https://uni.example.edu/s", **over):
    p = {"record": record, "eligibility": {"overall": "UNCLEAR", "criteria": []}, "match": {"total": 55.0, "components": []},
         "conflicts": [], "sources": [{"role": "primary", "content_hash": "h1"}], "official_url": url, "canonical_url": url,
         "source_type": "university", "source_domain": "uni.example.edu", "extraction_method": "ai", "extraction_model": "gpt-4o-mini",
         "ai_verified": True, "retrieved_at": "2026-11-17T00:00:00+00:00"}
    p.update(over)
    return p


# ---------- migrations ----------
def test_fresh_migration_creates_schema_and_is_idempotent(tmp_path):
    path = tmp_path / "a.db"
    assert [v for v, _ in run_migrations(path)] == [1, 2]
    assert run_migrations(path) == []
    tables = {r[0] for r in sqlite3.connect(path).execute("select name from sqlite_master where type='table'")}
    assert {"opportunities", "opportunity_snapshots", "change_log", "app_settings", "discoveries", "schema_migrations"} <= tables


def test_legacy_v2_rows_preserved_and_imported(tmp_path):
    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, title TEXT, university TEXT, country TEXT, url TEXT, deadline TEXT, notes TEXT)")
    conn.execute("INSERT INTO opportunities VALUES (7, 'Old Scholarship', 'Old Uni', 'Sweden', 'https://old.example.edu/x?utm_source=a', '1 Mar 2027', 'my note')")
    conn.commit(); conn.close()
    run_migrations(path)
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    legacy = c.execute("SELECT * FROM legacy_opportunities_v2").fetchall()
    assert len(legacy) == 1 and legacy[0]["title"] == "Old Scholarship"            # original table kept intact
    new = c.execute("SELECT * FROM opportunities").fetchall()
    assert len(new) == 1 and new[0]["name"] == "Old Scholarship" and new[0]["provider"] == "Old Uni"
    assert new[0]["research_status"] == "Imported (V2)" and new[0]["verified_at"] == "" and new[0]["ai_verified"] == 0
    assert "my note" in new[0]["notes"] and new[0]["normalized_url"] == "https://old.example.edu/x"
    assert list(tmp_path.glob("v2.db.bak-*"))                                       # backup made before changing


def test_failed_migration_rolls_back(tmp_path):
    path = tmp_path / "f.db"
    run_migrations(path)

    def bad(conn):
        conn.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        run_migrations(path, [Migration(99, "bad", bad)])
    tables = {r[0] for r in sqlite3.connect(path).execute("select name from sqlite_master")}
    assert "half_done" not in tables and 99 not in {r[0] for r in sqlite3.connect(path).execute("select version from schema_migrations")}


# ---------- CRUD ----------
def test_add_get_list_roundtrip_keeps_all_fields(db, good_record):
    oid = repo.add_opportunity(db, payload(good_record))
    o = repo.get_opportunity(db, oid)
    assert o["record"] == good_record and o["name"] == good_record["name"] and o["normalized_url"] == "https://uni.example.edu/s"
    assert o["ai_verified"] == 1 and o["match_score"] == 55.0 and o["extraction_model"] == "gpt-4o-mini" and o["created_at"] and o["updated_at"]
    assert len(repo.list_snapshots(db, oid)) == 1 and len(repo.list_opportunities(db)) == 1


def test_duplicate_url_rejected_by_database(db, good_record):
    repo.add_opportunity(db, payload(good_record))
    with pytest.raises(repo.DuplicateOpportunityError):
        repo.add_opportunity(db, payload(good_record, url="http://www.uni.example.edu/s/?utm_campaign=z"))
    assert len(repo.list_opportunities(db)) == 1


def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO app_settings (key, value_json, updated_at) VALUES ('k','1','t')")
            raise RuntimeError("x")
    assert db.query("SELECT * FROM app_settings WHERE key='k'") == []


def test_sql_injection_text_is_stored_literally(db):
    rec = make_record(name="x'); DROP TABLE opportunities;--")
    oid = repo.add_opportunity(db, payload(rec))
    assert repo.get_opportunity(db, oid)["name"] == "x'); DROP TABLE opportunities;--" and len(repo.list_opportunities(db)) == 1


def test_delete_cascades(db, good_record):
    oid = repo.add_opportunity(db, payload(good_record))
    repo.delete_opportunity(db, oid)
    assert repo.get_opportunity(db, oid) is None and db.query("SELECT * FROM opportunity_snapshots") == []


def test_status_notes_verification(db, good_record):
    oid = repo.add_opportunity(db, payload(good_record))
    repo.set_verified(db, oid, True)
    o = repo.get_opportunity(db, oid)
    assert o["verified_at"] and o["research_status"] == "Verified"
    repo.set_notes(db, oid, "hello"); repo.set_status(db, oid, "Applying")
    assert repo.get_opportunity(db, oid)["notes"] == "hello" and repo.get_opportunity(db, oid)["research_status"] == "Applying"
    with pytest.raises(ValueError):
        repo.set_status(db, oid, "bogus")


# ---------- duplicates ----------
def test_dedup_same_url_and_similar_name(good_record):
    existing = [{"id": 1, "name": "DAAD EPOS Scholarship", "provider": "DAAD", "normalized_url": "https://daad.de/epos", "official_url": "https://daad.de/epos", "canonical_url": ""}]
    same = find_duplicates({"official_url": "http://www.daad.de/epos/?utm_source=x", "name": "other"}, existing)
    assert same[0].reason == "same_url" and same[0].opportunity_id == 1
    similar = find_duplicates({"official_url": "https://new.example/x", "name": "DAAD EPOS Scholarships", "provider": "D.A.A.D."}, existing)
    assert similar and similar[0].reason == "similar_name"
    assert find_duplicates({"official_url": "https://new.example/x", "name": "Chevening Award", "provider": "FCDO"}, existing) == []


def test_dedup_via_canonical_and_extra_urls():
    existing = [{"id": 2, "name": "A", "provider": "P", "normalized_url": "https://a.edu/x", "official_url": "https://a.edu/x", "canonical_url": ""}]
    assert find_duplicates({"official_url": "https://b.edu/y", "canonical_url": "https://a.edu/x", "name": "Z"}, existing)[0].reason == "same_url"
    assert find_duplicates({"official_url": "https://b.edu/y", "urls": ["https://www.a.edu/x/"], "name": "Z"}, existing)


# ---------- change tracking ----------
def test_compare_records_ignores_cosmetics_detects_material(good_record):
    new = dict(good_record, eligible_nationalities=["India", "nepal"], application_deadline="  15 JANUARY 2027 ")
    assert compare_records(good_record, new) == []
    new2 = dict(good_record, deadline_date="2027-02-15", eligible_nationalities=["India"], funding_type="partially_funded")
    fields = {c.field for c in compare_records(good_record, new2)}
    assert fields == {"deadline_date", "eligible_nationalities", "funding_type"}


def test_update_keeps_history_and_flags_verified(db, good_record):
    oid = repo.add_opportunity(db, payload(good_record))
    repo.set_verified(db, oid, True)
    new = dict(good_record, deadline_date="2027-03-01")
    changes = compare_records(good_record, new)
    repo.update_opportunity_from_research(db, oid, payload(new), changes)
    o = repo.get_opportunity(db, oid)
    assert o["record"]["deadline_date"] == "2027-03-01" and o["needs_review"] == 1 and o["research_status"] == "Needs review"
    assert o["verified_at"]                                                       # verification not silently removed
    log = repo.list_changes(db, oid)
    assert log[0]["field"] == "deadline_date" and log[0]["old_value"] == "2027-01-15" and log[0]["new_value"] == "2027-03-01" and log[0]["source_url"]
    snaps = repo.list_snapshots(db, oid)
    assert len(snaps) == 2 and snaps[-1]["record"]["deadline_date"] == "2027-01-15"   # old state retained


def test_source_check_does_not_overwrite_saved_record(db, good_record):
    oid = repo.add_opportunity(db, payload(good_record))
    repo.set_verified(db, oid, True)
    new = dict(good_record, nepal_eligibility="explicitly_excluded")
    snap = repo.record_source_check(db, oid, new, {"overall": "NOT_MET"}, compare_records(good_record, new), "https://uni.example.edu/s", "h2", [{"role": "primary", "content_hash": "h2"}])
    o = repo.get_opportunity(db, oid)
    assert o["record"]["nepal_eligibility"] == "explicitly_eligible" and o["verified_at"] and o["needs_review"] == 1
    ch = repo.list_changes(db, oid)[0]
    assert ch["applied"] == 0 and ch["needs_review"] == 1 and snap
    repo.review_change(db, ch["id"], "ok")
    assert repo.get_opportunity(db, oid)["needs_review"] == 0


# ---------- discoveries ----------
def test_discoveries_are_leads_not_saved_opportunities(db):
    from scholarship_agent.discovery import SearchResult
    r = SearchResult("T", "https://x.edu/a", "snip", "q", normalized_url="https://x.edu/a", domain="x.edu")
    assert repo.upsert_discoveries(db, [r]) == 1 and repo.upsert_discoveries(db, [r]) == 0
    assert len(repo.list_discoveries(db, "new")) == 1 and repo.list_opportunities(db) == []


# ---------- search ----------
def test_queries_target_nepal_and_filters():
    qs = build_queries(["Applied Mathematics", "Data Science"], "Master's", ["Germany"], "fully_funded", "2027")
    assert qs and all("Nepalese" in q for q in qs) and all("Germany" in q and "2027" in q and "fully funded" in q for q in qs)
    assert len(build_queries(["A"] * 1, countries=["x", "y", "z", "w", "v", "u", "t", "s"])) <= 6


def test_search_success_classifies_sources():
    body = {"results": [{"title": "DAAD", "url": "https://www.daad.de/en/x", "content": "c"}, {"title": "Blog", "url": "https://scholars4dev.com/y", "content": "c"},
                        {"title": "bad", "url": "ftp://x"}, "junk"]}
    res = search_tavily("q", "tvly-test-key", session=FakeSession([FakeResponse(200, json_data=body)]))
    assert [r.source_type for r in res] == ["scholarship_provider", "third_party"] and res[1].is_third_party


@pytest.mark.parametrize("resp,fragment", [
    (FakeResponse(401), "API key"), (FakeResponse(429), "rate limit"), (FakeResponse(500), "error"), (FakeResponse(200, json_data=None), "unreadable"),
])
def test_search_failures_are_user_friendly(resp, fragment):
    with pytest.raises(SearchUnavailable) as err:
        search_tavily("q", "k-123456", session=FakeSession([resp]))
    assert fragment in str(err.value) and "k-123456" not in str(err.value)


def test_search_without_key():
    with pytest.raises(SearchUnavailable):
        search_tavily("q", "")


def test_search_many_merges_duplicates():
    body = {"results": [{"title": "A", "url": "https://a.edu/x", "content": ""}]}
    res, errs = search_many(["q1", "q2"], "key-123456", session=FakeSession([FakeResponse(200, json_data=body), FakeResponse(200, json_data=body)]))
    assert len(res) == 1 and errs == []


# ---------- filters ----------
@pytest.fixture()
def saved(db, good_record):
    a = good_record
    b = make_record(name="Unknown Thing")                                            # almost everything unknown
    c = dict(good_record, name="Swedish Tuition Award", country="Sweden", funding_type="partially_funded", deadline_date="2026-01-01", subject_areas=["Statistics"])
    for rec, url, score, elig in [(a, "https://a.edu/1", 80, "UNCLEAR"), (b, "https://b.edu/2", 10, "UNCLEAR"), (c, "https://c.edu/3", 50, "CONFIRMED")]:
        repo.add_opportunity(db, payload(rec, url=url, match={"total": score, "components": []}, eligibility={"overall": elig, "criteria": []}))
    return enrich(repo.list_opportunities(db), NOW)


def test_unknown_values_get_own_category(saved):
    assert "Unknown" in facet_options(saved, "country")
    unknown = apply_filters(saved, {"countries": ["Unknown"]})
    assert [o["name"] for o in unknown] == ["Unknown Thing"]


def test_filters_combine(saved):
    assert [o["name"] for o in apply_filters(saved, {"countries": ["Germany"], "funding_classes": ["Full"]})] == ["Example Applied Maths Scholarship"]
    assert apply_filters(saved, {"countries": ["Germany"], "funding_classes": ["Partial"]}) == []
    assert len(apply_filters(saved, {"min_score": 40})) == 2 and len(apply_filters(saved, {"min_score": 40, "overall_eligibility": ["CONFIRMED"]})) == 1


def test_deadline_status_and_range_filters(saved):
    assert [o["name"] for o in apply_filters(saved, {"deadline_statuses": ["Passed"]})] == ["Swedish Tuition Award"]
    assert [o["name"] for o in apply_filters(saved, {"deadline_statuses": ["Unknown"]})] == ["Unknown Thing"]
    r = apply_filters(saved, {"date_from": "2027-01-01", "date_to": "2027-12-31", "include_unknown_dates": False})
    assert [o["name"] for o in r] == ["Example Applied Maths Scholarship"]
    assert len(apply_filters(saved, {"date_from": "2027-01-01", "include_unknown_dates": True})) == 2


def test_text_search_and_sorting(saved):
    assert [o["name"] for o in apply_filters(saved, {"text": "swedish"})] == ["Swedish Tuition Award"]
    assert [o["match_score"] for o in apply_filters(saved, {"sort_by": "match_score", "descending": True})] == [80, 50, 10]
    d = apply_filters(saved, {"sort_by": "deadline_date", "descending": False})
    assert d[-1]["name"] == "Unknown Thing"                                           # unknown deadlines sort last


def test_passed_deadline_never_open_in_enrichment(saved):
    assert next(o for o in saved if o["name"] == "Swedish Tuition Award")["deadline_status"] == "Passed"
    assert next(o for o in saved if o["name"] == "Unknown Thing")["deadline_status"] == "Unknown"


def test_summary_cards_and_csv(saved):
    s = summarize(saved, new_discoveries=4)
    assert s["saved"] == 3 and s["new_discoveries"] == 4 and s["fully_funded"] == 1 and s["unclear_eligibility"] == 2
    csv_text = export_csv(saved)
    assert csv_text.splitlines()[0].startswith("id,name") and "Swedish Tuition Award" in csv_text


# ---------- settings persistence ----------
def test_settings_persist_and_reset(db):
    assert load_profile(db) == DEFAULT_PROFILE
    assert save_profile(db, {"nationality": "Nepali", "target_degree": "Master's", "target_subjects": "Data Science", "gpa": "3.4", "gpa_scale": "4"}) == []
    p = load_profile(db)
    assert p["gpa"] == 3.4 and p["target_subjects"] == ["Data Science"]
    assert save_profile(db, {"nationality": ""}) != [] and load_profile(db)["gpa"] == 3.4          # invalid input not saved
    assert save_weights(db, {"subject": 50, "funding": 10, "nationality": 10, "academic": 10, "deadline": 10, "programme": 10}) == []
    assert load_weights(db)["subject"] == 50
    reset_to_defaults(db)
    assert load_profile(db)["gpa"] is None and load_weights(db)["subject"] == 30
