"""Reusable Streamlit building blocks: styling, badges, tables and detail renderers.

All text coming from the web or the AI is HTML-escaped before it is placed in custom
HTML, and rendered with plain ``st.write``/``st.markdown`` (HTML disabled) otherwise.
"""

from __future__ import annotations

import html
from typing import Optional
from urllib.parse import urlsplit

import streamlit as st

from ..changes import display_value

CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
.sa-badge {display:inline-block; padding:2px 10px; margin:0 6px 4px 0; border-radius:12px;
           font-size:0.78rem; font-weight:600; border:1px solid transparent;}
.sa-green {background:#e6f4ea; color:#137333; border-color:#b7e1c1;}
.sa-red {background:#fce8e6; color:#c5221f; border-color:#f4b9b4;}
.sa-amber {background:#fef7e0; color:#b06000; border-color:#f5dc8a;}
.sa-blue {background:#e8f0fe; color:#1a56c4; border-color:#b9d0f8;}
.sa-grey {background:#f1f3f4; color:#5f6368; border-color:#dadce0;}
.sa-note {background:#fef7e0; border-left:4px solid #f9ab00; padding:8px 12px; border-radius:4px; margin:6px 0;}
@media (prefers-color-scheme: dark) {
  .sa-green {background:#0f2e1a; color:#81c995; border-color:#1e5631;}
  .sa-red {background:#3a1513; color:#f28b82; border-color:#6e2a26;}
  .sa-amber {background:#3a2e0b; color:#fdd663; border-color:#6b5415;}
  .sa-blue {background:#12254a; color:#8ab4f8; border-color:#274a8c;}
  .sa-grey {background:#2a2c2f; color:#bdc1c6; border-color:#454749;}
  .sa-note {background:#3a2e0b; border-left-color:#fdd663;}
}
@media (max-width: 700px) {.block-container {padding-left:0.8rem; padding-right:0.8rem;}}
</style>
"""

STATUS_EMOJI = {"CONFIRMED": "✅", "NOT_MET": "❌", "UNCLEAR": "❓", "NOT_APPLICABLE": "➖"}
DEADLINE_KIND = {"Open": "green", "Closing soon": "amber", "Passed": "red", "Unknown": "grey"}
ELIG_KIND = {"CONFIRMED": "green", "NOT_MET": "red", "UNCLEAR": "amber"}
FUNDING_LABEL = {
    "fully_funded": ("Fully funded", "green"), "partially_funded": ("Partial funding", "blue"),
    "tuition_only": ("Tuition only", "blue"), "stipend_only": ("Stipend only", "blue"), "unknown": ("Funding unknown", "grey"),
}
NEPAL_LABEL = {
    "explicitly_eligible": ("Nepal: named eligible", "green"), "explicitly_excluded": ("Nepal: excluded", "red"),
    "unclear": ("Nepal: unclear", "amber"), "not_stated": ("Nepal: not stated", "grey"),
}
SOURCE_LABEL = {
    "government": ("Government site (likely official)", "green"), "university": ("University site (likely official)", "green"),
    "scholarship_provider": ("Provider site (likely official)", "green"), "third_party": ("THIRD-PARTY source", "red"),
    "unverified": ("Source not verified", "amber"),
}


def inject_css() -> None:
    """Inject the shared stylesheet once per rerun."""
    st.markdown(CSS, unsafe_allow_html=True)


def badge(text: str, kind: str = "grey") -> str:
    """Return HTML for a small coloured badge (text is escaped)."""
    return f'<span class="sa-badge sa-{kind}">{html.escape(str(text))}</span>'


def show_badges(items: list) -> None:
    """Render a row of ``(text, kind)`` badges."""
    st.markdown("".join(badge(t, k) for t, k in items), unsafe_allow_html=True)


def note(text: str) -> None:
    """Render an amber callout (text is escaped)."""
    st.markdown(f'<div class="sa-note">{html.escape(text)}</div>', unsafe_allow_html=True)


def safe_link(url: str) -> Optional[str]:
    """Return ``url`` only if it is an http(s) URL, otherwise None (guards link buttons)."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    return url.strip() if parts.scheme in ("http", "https") and parts.hostname else None


def link_button(label: str, url: str, key: Optional[str] = None) -> None:
    """Show a link button to ``url`` when it is a safe http(s) address."""
    safe = safe_link(url)
    if safe:
        st.link_button(label, safe, key=key)
    else:
        st.caption("No valid link available.")


def opportunity_badges(opp: dict) -> list:
    """Return the badge list for an enriched opportunity."""
    f_text, f_kind = FUNDING_LABEL.get(opp.get("funding_type", "unknown"), FUNDING_LABEL["unknown"])
    n_text, n_kind = NEPAL_LABEL.get(opp.get("nepal_eligibility", "not_stated"), NEPAL_LABEL["not_stated"])
    s_text, s_kind = SOURCE_LABEL.get(opp.get("source_type", "unverified"), SOURCE_LABEL["unverified"])
    deadline = opp.get("deadline_status", "Unknown")
    items = [
        (f"Deadline: {deadline}", DEADLINE_KIND.get(deadline, "grey")), (f_text, f_kind), (n_text, n_kind),
        (f"Eligibility: {opp.get('overall_eligibility', 'UNCLEAR')}", ELIG_KIND.get(opp.get("overall_eligibility", "UNCLEAR"), "grey")),
        (s_text, s_kind),
    ]
    if opp.get("verified_at"):
        items.append(("Verified by you", "green"))
    if opp.get("needs_review"):
        items.append(("Needs review", "red"))
    if not opp.get("ai_verified"):
        items.append(("Not AI-verified", "amber"))
    return items


def eligibility_table(assessment: dict) -> None:
    """Show the eligibility matrix (criterion, state, evidence, details) with the overall state."""
    if not assessment:
        st.info("No eligibility assessment stored for this record.")
        return
    overall = assessment.get("overall", "UNCLEAR")
    show_badges([(f"Overall eligibility: {overall}", ELIG_KIND.get(overall, "grey"))])
    for reason in assessment.get("reasons", []):
        st.caption(reason)
    rows = [{
        "Criterion": c["label"], "State": f"{STATUS_EMOJI.get(c['status'], '')} {c['status']}",
        "Required?": "mandatory" if c["mandatory"] else "if stated", "How it was decided": c["detail"],
        "Evidence": c.get("evidence", ""), "Source": c.get("source_url", ""), "Set by you": "yes" if c.get("manual") else "",
    } for c in assessment.get("criteria", [])]
    st.dataframe(rows, width="stretch", hide_index=True)
    st.caption(assessment.get("caveat", ""))


def match_breakdown(match: dict) -> None:
    """Show the 0-100 match score with its component breakdown and the 'not eligibility' reminder."""
    if not match:
        return
    total = float(match.get("total", 0))
    st.metric("Match score (0-100)", f"{total:.0f}")
    st.progress(min(1.0, total / 100))
    rows = [{
        "Component": c["label"], "Weight %": c["weight"],
        "Score": "no data" if c["score"] is None else f"{c['score']:.2f}", "Points": c["points"], "Why": c["explanation"],
    } for c in match.get("components", [])]
    st.dataframe(rows, width="stretch", hide_index=True)
    st.caption(match.get("disclaimer", ""))


def funding_breakdown(record: dict) -> None:
    """Show funding details exactly as extracted ('not stated' where empty)."""
    rows = [{"Item": label, "As stated in the source": record.get(key) or "not stated"} for key, label in [
        ("tuition_coverage", "Tuition"), ("stipend", "Stipend / living allowance"), ("accommodation", "Accommodation"),
        ("travel", "Travel"), ("insurance", "Insurance"), ("other_benefits", "Other benefits")]]
    st.dataframe(rows, width="stretch", hide_index=True)


def evidence_list(record: dict) -> None:
    """List evidence quotations with their sources and whether each was found verbatim."""
    items = record.get("evidence", [])
    if not items:
        st.caption("No evidence quotations were extracted.")
        return
    for item in items:
        found = item.get("quote_found")
        mark = "✅ found in page" if found else ("⚠️ NOT found verbatim in page" if found is False else "")
        st.markdown(f"**{item.get('field', '')}** {mark}")
        st.text(item.get("quote", ""))
        if item.get("source_url"):
            st.caption(item["source_url"])


def sources_table(sources: list) -> None:
    """Show every source URL with type, domain, retrieval time and any failure."""
    rows = [{
        "Role": s.get("role", ""), "URL": s.get("final_url") or s.get("requested_url", ""),
        "Type": SOURCE_LABEL.get(s.get("source_type", "unverified"), ("",))[0], "Domain": s.get("source_domain", ""),
        "Retrieved (UTC)": s.get("retrieved_at", ""), "Last checked (UTC)": s.get("last_checked_at", ""),
        "Read OK": "yes" if s.get("ok", True) else f"no: {s.get('error', '')}",
    } for s in sources]
    st.dataframe(rows, width="stretch", hide_index=True)


def conflicts_panel(conflicts: list) -> None:
    """Show conflicting values from different sources side by side."""
    if not conflicts:
        return
    st.error("Sources disagree. Both values are kept; check which one applies to your programme.")
    for c in conflicts:
        st.markdown(f"**{c['field']}**")
        st.dataframe([{"Value": v["value"], "Source": v["source_url"]} for v in c["values"]], width="stretch", hide_index=True)


def record_overview(record: dict) -> None:
    """Show the main extracted facts in two columns (unknowns shown as 'not stated')."""
    def val(key: str) -> str:
        v = record.get(key)
        return display_value(v) if v not in ("", None, [], "unknown", "not_stated") else "not stated"

    left, right = st.columns(2)
    with left:
        st.markdown(f"**Provider / university:** {val('provider')}")
        st.markdown(f"**Country:** {val('country')}")
        st.markdown(f"**Degree level:** {val('degree_level')}")
        st.markdown(f"**Eligible programmes:** {val('eligible_programmes')}")
        st.markdown(f"**Subject areas:** {val('subject_areas')}")
        st.markdown(f"**Eligible nationalities (as written):** {val('eligible_nationalities')}")
        st.markdown(f"**Nepal eligibility (extracted):** {val('nepal_eligibility')}")
    with right:
        st.markdown(f"**Academic requirements:** {val('academic_requirements')}")
        st.markdown(f"**Minimum GPA:** {val('min_gpa')}")
        st.markdown(f"**Language requirements:** {val('language_requirements')}")
        st.markdown(f"**Work experience:** {val('work_experience')}")
        st.markdown(f"**Age limit:** {val('age_limit')}")
        st.markdown(f"**Residency restrictions:** {val('residency_restrictions')}")
        st.markdown(f"**Other restrictions:** {val('other_restrictions')}")


def deadline_block(opp_or_result_deadline: dict) -> None:
    """Show deadline status, Nepal-time display, original wording, timezone, cycle and flags.

    Args:
        opp_or_result_deadline: Dict with ``status``, ``local``, ``original``, ``timezone``,
            ``cycle``, ``days_left``, ``flags`` and ``applies_to`` keys.
    """
    d = opp_or_result_deadline
    show_badges([(f"Deadline: {d['status']}", DEADLINE_KIND.get(d["status"], "grey"))])
    st.markdown(f"**Nepal time:** {d['local'] or 'unknown'}"
                + (f"  ·  **{d['days_left']} days left**" if d.get("days_left") is not None and d["status"] in ("Open", "Closing soon") else ""))
    st.markdown(f"**Original wording:** {d['original'] or 'not stated'}  ·  **Timezone as stated:** {d['timezone'] or 'not stated'}")
    st.markdown(f"**Application cycle:** {d['cycle'] or 'not stated'}  ·  **Applies to:** {d.get('applies_to') or 'not stated'}")
    for flag in d.get("flags", []):
        st.warning(flag)


def opportunity_detail(opp: dict, key_prefix: str = "detail") -> None:
    """Render the complete detail view of one saved opportunity (every extracted field, no website visit needed).

    Args:
        opp: Enriched opportunity from :func:`filters.enrich`.
        key_prefix: Prefix for widget keys so several details can coexist.
    """
    rec = opp.get("record", {})
    st.subheader(opp.get("name") or "(unnamed)")
    show_badges(opportunity_badges(opp))
    link_button("Open official page", opp.get("official_url", ""), key=f"{key_prefix}_link_{opp['id']}")
    st.caption(f"Saved record #{opp['id']} · retrieved {opp.get('retrieved_at', '')[:16]} UTC · "
               f"verified {opp.get('verified_at', '')[:16] or 'not yet'} · method: {opp.get('extraction_method', '')} {opp.get('extraction_model', '')}")
    if not opp.get("ai_verified"):
        note("This record was NOT extracted by AI (heuristic fallback or imported). Treat every field as unverified.")
    tabs = st.tabs(["Summary", "Funding", "Requirements", "Deadline & apply", "Eligibility", "Match score", "Evidence & sources"])
    with tabs[0]:
        st.write(rec.get("summary") or "No summary available.")
        record_overview(rec)
        if rec.get("uncertainties"):
            st.markdown("**Uncertainties / missing information**")
            for u in rec["uncertainties"]:
                st.markdown(f"- {u}")
        if rec.get("interpretations"):
            st.markdown("**Interpretations (not stated by the source)**")
            for u in rec["interpretations"]:
                st.markdown(f"- {u}")
    with tabs[1]:
        show_badges([FUNDING_LABEL.get(opp.get("funding_type", "unknown"), FUNDING_LABEL["unknown"])])
        funding_breakdown(rec)
    with tabs[2]:
        record_overview(rec)
        if rec.get("language_requirements"):
            st.dataframe(rec["language_requirements"], width="stretch", hide_index=True)
    with tabs[3]:
        deadline_block({
            "status": opp.get("deadline_status", "Unknown"), "local": opp.get("deadline_local", ""),
            "original": rec.get("application_deadline", ""), "timezone": rec.get("deadline_timezone", ""),
            "cycle": rec.get("application_cycle", ""), "days_left": opp.get("days_left"),
            "flags": opp.get("deadline_flags", []), "applies_to": rec.get("deadline_applies_to", ""),
        })
        st.markdown("**How to apply (as stated):** " + (rec.get("application_instructions") or "not stated"))
        app_url = rec.get("application_url") or ""
        if app_url:
            link_button("Open application link", app_url, key=f"{key_prefix}_apply_{opp['id']}")
    with tabs[4]:
        eligibility_table(opp.get("eligibility", {}))
    with tabs[5]:
        match_breakdown(opp.get("match", {}))
    with tabs[6]:
        conflicts_panel(opp.get("conflicts", []))
        evidence_list(rec)
        st.markdown("**Sources**")
        sources_table(opp.get("sources", []))
