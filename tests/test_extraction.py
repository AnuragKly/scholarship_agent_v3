"""AI extraction mapping, schema strictness, guard rails, fallback labelling, merge conflicts. OpenAI is mocked."""

import json
from types import SimpleNamespace

import pytest

from scholarship_agent.extraction import (
    AIUnavailable, ExtractionError, SYSTEM_PROMPT, apply_safeguards, extract_fallback, extract_with_ai, merge_records,
)
from scholarship_agent.record import ALL_FIELDS, build_json_schema, coerce_record, empty_record
from tests.conftest import make_record

PAGE = ("The Example Scholarship funds Master's study. Applicants from Nepal are eligible. "
        "Application deadline: 15 January 2027. International students may apply to other programmes. " * 3)


class FakeResponses:
    """Mock of ``client.responses`` that returns a canned output_text and records the call."""

    def __init__(self, output_text="", status="completed", exc=None):
        self.output_text, self.status, self.exc, self.kwargs = output_text, status, exc, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return SimpleNamespace(output_text=self.output_text, status=self.status)


def client_returning(payload, **kw):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(responses=FakeResponses(text, **kw))


def full_payload(**over):
    rec = empty_record()
    rec.update(over)
    return rec


def test_schema_is_strict_and_complete():
    schema = build_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == set(ALL_FIELDS)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)
    for needed in ["name", "country", "provider", "degree_level", "funding_type", "min_gpa", "language_requirements",
                   "nepal_eligibility", "application_deadline", "deadline_timezone", "application_url", "uncertainties", "evidence"]:
        assert needed in schema["properties"]


def test_uses_responses_api_with_strict_structured_output():
    client = client_returning(full_payload(name="X", nepal_eligibility="explicitly_eligible",
                                           nepal_eligibility_evidence="Applicants from Nepal are eligible."))
    res = extract_with_ai(PAGE, "https://uni.example.edu/s", "T", model="gpt-4o-mini", client=client)
    kw = client.responses.kwargs
    assert kw["model"] == "gpt-4o-mini"
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["schema"]["additionalProperties"] is False
    assert res.method == "ai" and res.ai_verified and res.model == "gpt-4o-mini"
    user_msg = kw["input"][1]["content"]
    assert "<<<PAGE_TEXT_START>>>" in user_msg and "UNTRUSTED" in kw["input"][0]["content"]


def test_prompt_declares_injection_and_nepal_rules():
    assert "UNTRUSTED" in SYSTEM_PROMPT and "international students" in SYSTEM_PROMPT and "Never use outside knowledge" in SYSTEM_PROMPT


def test_no_key_raises_unavailable():
    with pytest.raises(AIUnavailable):
        extract_with_ai(PAGE, "https://x.edu", api_key="")


def test_malformed_json_raises_extraction_error():
    with pytest.raises(ExtractionError):
        extract_with_ai(PAGE, "https://x.edu", client=client_returning("{not json"))


def test_non_object_json_raises_extraction_error():
    with pytest.raises(ExtractionError):
        extract_with_ai(PAGE, "https://x.edu", client=client_returning("[1,2,3]"))


def test_empty_output_and_incomplete_status_raise():
    with pytest.raises(ExtractionError):
        extract_with_ai(PAGE, "https://x.edu", client=client_returning(""))
    with pytest.raises(ExtractionError):
        extract_with_ai(PAGE, "https://x.edu", client=client_returning({}, status="incomplete"))


def test_api_exception_becomes_friendly_secret_free_error():
    class AuthenticationError(Exception):
        pass
    client = SimpleNamespace(responses=FakeResponses(exc=AuthenticationError("bad key sk-abcdefgh12345678")))
    with pytest.raises(ExtractionError) as err:
        extract_with_ai(PAGE, "https://x.edu", client=client)
    assert "sk-abcdefgh" not in str(err.value) and "OPENAI_API_KEY" in str(err.value)


def test_coerce_repairs_bad_types_and_invalid_enums_without_inventing():
    rec, warnings = coerce_record({"name": 5, "subject_areas": "Math", "funding_type": "super_funded", "nepal_eligibility": "yes",
                                   "evidence": [{"field": "a", "quote": "q"}, "junk"], "unknown_key": 1})
    assert rec["name"] == "5" and rec["subject_areas"] == ["Math"]
    assert rec["funding_type"] == "unknown" and rec["nepal_eligibility"] == "not_stated"
    assert rec["evidence"] == [{"field": "a", "quote": "q"}] and "unknown_key" not in rec
    assert warnings and set(rec) == set(ALL_FIELDS)


def test_nepal_claim_without_nepal_in_page_is_downgraded():
    rec = make_record(nepal_eligibility="explicitly_eligible", nepal_eligibility_evidence="Nepal is eligible")
    apply_safeguards(rec, "Open to international students from all countries.", "https://x.edu")
    assert rec["nepal_eligibility"] == "unclear" and rec["uncertainties"]


def test_nepal_claim_needs_supporting_quote_present_in_page_and_naming_nepal():
    page = "Applicants from Nepal are eligible. Other text here."
    ok = make_record(nepal_eligibility="explicitly_eligible", nepal_eligibility_evidence="Applicants from Nepal are eligible.")
    apply_safeguards(ok, page, "https://x.edu")
    assert ok["nepal_eligibility"] == "explicitly_eligible"
    for quote in ["", "Quote that is not on the page but mentions Nepal.", "Other text here."]:
        bad = make_record(nepal_eligibility="explicitly_eligible", nepal_eligibility_evidence=quote)
        apply_safeguards(bad, page, "https://x.edu")
        assert bad["nepal_eligibility"] == "unclear", quote


def test_evidence_quotes_checked_against_page():
    rec = make_record(evidence=[{"field": "a", "quote": "Applicants from Nepal are eligible."}, {"field": "b", "quote": "made up sentence"}])
    warnings = apply_safeguards(rec, PAGE, "https://x.edu/p")
    found = {e["field"]: e["quote_found"] for e in rec["evidence"]}
    assert found == {"a": True, "b": False} and all(e["source_url"] == "https://x.edu/p" for e in rec["evidence"]) and warnings


def test_deadline_not_in_page_is_flagged():
    rec = make_record(deadline_date="2027-02-02")
    apply_safeguards(rec, PAGE, "https://x.edu")
    assert any("not found" in u for u in rec["uncertainties"])
    rec2 = make_record(deadline_date="2027-01-15")
    apply_safeguards(rec2, PAGE, "https://x.edu")
    assert not rec2["uncertainties"]


def test_fallback_is_labelled_and_never_confirms_nepal():
    res = extract_fallback(PAGE, "https://x.edu/s", "Example Scholarship")
    assert res.method == "heuristic_fallback" and res.ai_verified is False
    assert res.record["nepal_eligibility"] == "unclear"
    assert any("NOT AI verification" in w for w in res.warnings)
    assert res.record["deadline_date"] == "2027-01-15"


def test_fallback_without_deadline_or_nepal():
    res = extract_fallback("Plain text about something. " * 20, "https://x.edu", "T")
    assert res.record["nepal_eligibility"] == "not_stated" and res.record["deadline_date"] == ""


def test_fallback_multiple_deadlines_picks_none():
    text = "Deadline: 1 March 2027. Final deadline: 5 April 2027. " * 3
    res = extract_fallback(text, "https://x.edu", "T")
    assert res.record["deadline_date"] == ""
    assert any("Several possible deadlines" in u for u in res.record["uncertainties"])


def test_merge_preserves_conflicting_values():
    a = make_record(deadline_date="2027-01-15", funding_type="fully_funded", evidence=[{"field": "x", "quote": "q", "source_url": "https://a.edu"}])
    b = make_record(deadline_date="2027-02-01", funding_type="unknown", country="Germany", subject_areas=["Data Science"])
    merged, conflicts = merge_records(a, [("https://a.edu/apply", b)])
    assert [c["field"] for c in conflicts] == ["deadline_date"]
    assert {v["value"] for v in conflicts[0]["values"]} == {"2027-01-15", "2027-02-01"}
    assert merged["deadline_date"] == "2027-01-15" and merged["country"] == "Germany" and merged["subject_areas"] == ["Data Science"]
    assert any("disagree" in u for u in merged["uncertainties"])
