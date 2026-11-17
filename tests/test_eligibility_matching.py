"""Eligibility decisions, deadlines, matching, settings validation."""

from datetime import datetime, timezone

import pytest

from scholarship_agent.deadlines import (
    STATUS_CLOSING_SOON, STATUS_OPEN, STATUS_PASSED, STATUS_UNKNOWN, compute_deadline, find_dates_in_text, parse_timezone,
)
from scholarship_agent.eligibility import CONFIRMED, NOT_APPLICABLE, NOT_MET, UNCLEAR, assess_eligibility
from scholarship_agent.matching import compute_match
from scholarship_agent.settings import validate_profile, validate_weights
from tests.conftest import make_record

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)


# ---------- deadlines ----------
def test_missing_deadline_is_unknown_never_open():
    info = compute_deadline(make_record(), NOW)
    assert info.status == STATUS_UNKNOWN and info.deadline_utc is None and any("NOT assumed to be open" in f for f in info.flags)


def test_text_without_exact_date_is_unknown():
    info = compute_deadline(make_record(application_deadline="early next year"), NOW)
    assert info.status == STATUS_UNKNOWN


def test_passed_deadline_never_open():
    assert compute_deadline(make_record(deadline_date="2026-10-01", deadline_timezone="UTC"), NOW).status == STATUS_PASSED


def test_open_and_closing_soon_boundaries():
    assert compute_deadline(make_record(deadline_date="2027-03-01", deadline_timezone="UTC"), NOW).status == STATUS_OPEN
    assert compute_deadline(make_record(deadline_date="2026-12-10", deadline_timezone="UTC"), NOW).status == STATUS_CLOSING_SOON
    assert compute_deadline(make_record(deadline_date="2026-12-10", deadline_timezone="UTC"), NOW, closing_soon_days=10).status == STATUS_OPEN


def test_nepal_time_display_and_original_wording_kept():
    info = compute_deadline(make_record(deadline_date="2027-01-15T23:59", deadline_timezone="UTC", application_deadline="15 Jan 2027, 11:59pm UTC",
                                        application_cycle="2027/28"), NOW)
    assert info.local_display.startswith("16 Jan 2027, 05:44") and "Nepal time" in info.local_display
    assert info.original_text == "15 Jan 2027, 11:59pm UTC" and info.cycle == "2027/28" and info.timezone_label == "UTC"


def test_missing_timezone_uses_earliest_interpretation_and_flags():
    info = compute_deadline(make_record(deadline_date="2026-11-17"), NOW)
    assert info.timezone_assumed and info.status == STATUS_CLOSING_SOON
    assert info.deadline_utc.isoformat() == "2026-11-17T09:59:59+00:00"   # 23:59:59 at UTC+14, the earliest possible reading
    assert any("timezone" in f.lower() for f in info.flags)


def test_conflicting_deadlines_flagged_and_earliest_used():
    info = compute_deadline(make_record(deadline_date="2027-02-01", deadline_timezone="UTC"), NOW, other_dates=["2027-01-10"])
    assert info.conflict and info.deadline_utc.date().isoformat() == "2027-01-10" and any("different deadlines" in f for f in info.flags)


def test_invalid_date_ignored_with_flag():
    info = compute_deadline(make_record(deadline_date="2027-13-45"), NOW)
    assert info.status == STATUS_UNKNOWN and any("not a valid date" in f for f in info.flags)


def test_timezone_parsing():
    assert parse_timezone("Europe/Berlin") is not None and parse_timezone("UTC+5:45") is not None and parse_timezone("CET") is not None
    assert parse_timezone("IST") is None and parse_timezone("") is None and parse_timezone("UTC+20") is None


def test_find_dates_ignores_ambiguous_numeric():
    assert [d for d, _ in find_dates_in_text("Deadline 03/04/2027")] == []
    assert [d for d, _ in find_dates_in_text("Deadline: 15 January 2027 and March 3, 2027 and 2027-05-06")] == ["2027-01-15", "2027-03-03", "2027-05-06"]


# ---------- eligibility ----------
def status_of(result, key):
    return result.get(key).status


def test_nepal_named_is_confirmed(good_record, profile):
    assert status_of(assess_eligibility(good_record, profile), "nepal_nationality") == CONFIRMED


def test_international_students_does_not_confirm_nepal(profile):
    rec = make_record(eligible_nationalities=["International students"], nepal_eligibility="unclear")
    c = assess_eligibility(rec, profile).get("nepal_nationality")
    assert c.status == UNCLEAR and "NOT confirm Nepal" in c.detail


def test_even_if_model_wrongly_says_eligible_but_only_generic_is_listed_stays_consistent(profile):
    rec = make_record(eligible_nationalities=["all international students"], nepal_eligibility="not_stated")
    assert status_of(assess_eligibility(rec, profile), "nepal_nationality") == UNCLEAR


def test_nepal_excluded_is_not_met(profile):
    rec = make_record(nepal_eligibility="explicitly_excluded", nepal_eligibility_evidence="Nepal is not eligible")
    r = assess_eligibility(rec, profile)
    assert status_of(r, "nepal_nationality") == NOT_MET and r.overall == NOT_MET


def test_unclear_nepal_status_blocks_overall_confirmed(good_record, profile):
    good_record.update(nepal_eligibility="unclear", nepal_eligibility_evidence="")
    r = assess_eligibility(good_record, profile)
    assert r.overall == UNCLEAR and r.overall != CONFIRMED


def test_overall_never_confirmed_while_any_mandatory_unclear(good_record, profile):
    # default profile has no GPA/English supplied -> GPA and language mandatory criteria are UNCLEAR
    r = assess_eligibility(good_record, profile)
    assert status_of(r, "gpa") == UNCLEAR and status_of(r, "language") == UNCLEAR
    assert r.overall == UNCLEAR


def test_user_data_is_never_assumed(good_record, profile):
    r = assess_eligibility(good_record, profile)
    assert "not set in Settings" in r.get("gpa").detail and "not set in Settings" in r.get("language").detail


def test_fully_supplied_profile_can_reach_confirmed(good_record, profile):
    profile.update(gpa=3.5, gpa_scale=4, english_test="IELTS", english_score=7.0, education_status="completed")
    good_record["other_restrictions"] = []
    r = assess_eligibility(good_record, profile)
    assert r.overall == CONFIRMED, [(c.key, c.status) for c in r.criteria if c.mandatory]
    assert r.get("programme_admission").status == UNCLEAR  # formal admission is never claimed
    assert "NOT verified" in r.caveat


def test_gpa_below_minimum_is_not_met(good_record, profile):
    profile.update(gpa=2.5, gpa_scale=4)
    assert status_of(assess_eligibility(good_record, profile), "gpa") == NOT_MET


def test_gpa_scale_difference_handled(good_record, profile):
    good_record["min_gpa"] = "3.0/4.0"
    profile.update(gpa=8.5, gpa_scale=10)
    assert status_of(assess_eligibility(good_record, profile), "gpa") == CONFIRMED


def test_unparseable_gpa_requirement_is_unclear(good_record, profile):
    good_record["min_gpa"] = "second class upper"
    profile.update(gpa=3.9, gpa_scale=4)
    assert status_of(assess_eligibility(good_record, profile), "gpa") == UNCLEAR


def test_language_below_min_not_met_and_other_test_unclear(good_record, profile):
    profile.update(english_test="IELTS", english_score=5.5)
    assert status_of(assess_eligibility(good_record, profile), "language") == NOT_MET
    profile.update(english_test="TOEFL", english_score=100)
    assert status_of(assess_eligibility(good_record, profile), "language") == UNCLEAR


def test_work_experience_rules(good_record, profile):
    assert status_of(assess_eligibility(good_record, profile), "work_experience") == NOT_APPLICABLE
    good_record["work_experience"] = "at least 2 years of professional experience is required"
    assert status_of(assess_eligibility(good_record, profile), "work_experience") == UNCLEAR
    profile["work_experience_years"] = 1
    assert status_of(assess_eligibility(good_record, profile), "work_experience") == NOT_MET
    profile["work_experience_years"] = 3
    assert status_of(assess_eligibility(good_record, profile), "work_experience") == CONFIRMED
    good_record["work_experience"] = "work experience is preferred"
    assert status_of(assess_eligibility(good_record, profile), "work_experience") == UNCLEAR


def test_age_limit(good_record, profile):
    good_record["age_limit"] = "must be under 35 years old"
    assert status_of(assess_eligibility(good_record, profile), "age") == UNCLEAR
    profile["age"] = 22
    assert status_of(assess_eligibility(good_record, profile), "age") == CONFIRMED
    profile["age"] = 40
    assert status_of(assess_eligibility(good_record, profile), "age") == NOT_MET


def test_degree_level_and_subject(good_record, profile):
    assert status_of(assess_eligibility(good_record, profile), "degree_level") == CONFIRMED
    assert status_of(assess_eligibility(make_record(degree_level="phd"), profile), "degree_level") == NOT_MET
    assert status_of(assess_eligibility(make_record(), profile), "degree_level") == UNCLEAR
    assert status_of(assess_eligibility(good_record, profile), "subject") == CONFIRMED
    assert "not formal admission" in assess_eligibility(good_record, profile).get("subject").detail
    assert status_of(assess_eligibility(make_record(subject_areas=["Medicine", "Law"]), profile), "subject") == NOT_MET
    assert status_of(assess_eligibility(make_record(), profile), "subject") == UNCLEAR


def test_undergraduate_in_progress_is_unclear(good_record, profile):
    assert status_of(assess_eligibility(good_record, profile), "undergraduate") == UNCLEAR


def test_fallback_extraction_cannot_confirm_overall(good_record, profile):
    profile.update(gpa=3.5, gpa_scale=4, english_test="IELTS", english_score=7.0, education_status="completed")
    good_record["other_restrictions"] = []
    assert assess_eligibility(good_record, profile, ai_verified=False).overall == UNCLEAR


def test_manual_override_is_marked_and_changes_result(good_record, profile):
    ov = {"gpa": {"status": CONFIRMED, "note": "checked page", "at": "2026-11-17T00:00:00+00:00"}}
    c = assess_eligibility(good_record, profile, ov).get("gpa")
    assert c.status == CONFIRMED and c.manual and "Manual decision" in c.detail


# ---------- matching ----------
def match_for(record, profile, weights):
    elig = assess_eligibility(record, profile)
    dl = compute_deadline(record, NOW)
    return compute_match(record, profile, weights, elig, dl), elig


def test_score_is_bounded_and_explained(good_record, profile, weights):
    m, _ = match_for(good_record, profile, weights)
    assert 0 <= m.total <= 100 and len(m.components) == 6 and all(c.explanation for c in m.components)
    assert abs(sum(c.weight for c in m.components) - 100) < 0.5


def test_missing_data_earns_zero_not_a_match(profile, weights):
    m, _ = match_for(make_record(), profile, weights)
    assert m.total == 0 and all(c.score is None or c.score == 0 for c in m.components)
    assert any("no data" in c.explanation.lower() or "No data" in c.explanation for c in m.components)


def test_high_score_does_not_imply_eligibility(good_record, profile, weights):
    m, elig = match_for(good_record, profile, weights)
    assert m.total >= 60 and elig.overall != CONFIRMED and "NOT mean you are eligible" in m.disclaimer


def test_weights_change_the_score(good_record, profile, weights):
    base, _ = match_for(good_record, profile, weights)
    heavy = dict(weights, subject=0, funding=0, nationality=100, academic=0, deadline=0, programme=0)
    other, _ = match_for(good_record, profile, heavy)
    assert base.total != other.total and other.total == 100.0


def test_passed_deadline_scores_zero_for_that_component(good_record, profile, weights):
    good_record["deadline_date"] = "2026-01-01"
    m, _ = match_for(good_record, profile, weights)
    assert next(c for c in m.components if c.key == "deadline").score == 0.0


# ---------- settings validation ----------
def valid_raw(**over):
    raw = {"nationality": "Nepali", "target_degree": "Master's", "target_subjects": "Applied Mathematics, Data Science", "education_status": "in_progress",
           "funding_preference": "fully_funded"}
    raw.update(over)
    return raw


def test_optional_fields_may_be_empty():
    p, errors = validate_profile(valid_raw(gpa="", gpa_scale="", english_test="", english_score="", work_experience_years="", age=""))
    assert not errors and p["gpa"] is None and p["english_score"] is None and p["target_subjects"] == ["Applied Mathematics", "Data Science"]


@pytest.mark.parametrize("over,fragment", [
    ({"nationality": ""}, "Nationality is required"), ({"target_subjects": ""}, "at least one target subject"),
    ({"gpa": "abc", "gpa_scale": "4"}, "GPA must be a number"), ({"gpa": "3.5"}, "GPA scale"), ({"gpa": "5", "gpa_scale": "4"}, "higher than its scale"),
    ({"english_score": "7"}, "English test"), ({"work_experience_years": "-1"}, "between"), ({"funding_preference": "x"}, "Funding preference"),
])
def test_profile_validation_errors(over, fragment):
    _, errors = validate_profile(valid_raw(**over))
    assert any(fragment in e for e in errors), errors


def test_weights_validation():
    _, errors = validate_weights({k: 0 for k in ["subject", "funding", "nationality", "academic", "deadline", "programme"]})
    assert any("above zero" in e for e in errors)
    _, errors = validate_weights({"subject": "abc"})
    assert errors
