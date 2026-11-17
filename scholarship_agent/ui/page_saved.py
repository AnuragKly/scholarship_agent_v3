"""Saved Opportunities page: combinable filters, sorting, table, CSV export and detail view."""

from __future__ import annotations

import streamlit as st

from .. import repository
from ..filters import SORT_OPTIONS, UNKNOWN, apply_filters, enrich, export_csv, facet_options
from ..deadlines import ALL_STATUSES
from .components import opportunity_detail
from .state import AppContext


def _multi(label: str, options: list, key: str) -> list:
    """Render a multiselect and return the chosen values."""
    return st.multiselect(label, options, key=key)


def render(ctx: AppContext) -> None:
    """Render the Saved Opportunities page.

    Args:
        ctx: Application context.
    """
    st.title("Saved Opportunities")
    saved = enrich(repository.list_opportunities(ctx.db), closing_soon_days=ctx.cfg.closing_soon_days)
    if not saved:
        st.info("No saved opportunities yet. Use **Discover** to research a scholarship page, then click **Add Opportunity**.")
        return

    st.caption("Filters combine (AND). Records with missing values appear under the 'Unknown' option instead of disappearing.")
    text = st.text_input("Search text", placeholder="name, provider, country, subject, notes…", key="sv_text")
    with st.expander("Filters", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            countries = _multi("Country", facet_options(saved, "country"), "sv_country")
            providers = _multi("University / provider", facet_options(saved, "provider"), "sv_provider")
            subjects = _multi("Subject", facet_options(saved, "subjects"), "sv_subject")
            degrees = _multi("Degree level", facet_options(saved, "degree_level"), "sv_degree")
        with c2:
            funding = _multi("Funding type", facet_options(saved, "funding_type"), "sv_funding")
            fclass = _multi("Full / partial funding", ["Full", "Partial", UNKNOWN], "sv_fclass")
            dstatus = _multi("Deadline status", ALL_STATUSES, "sv_dstatus")
            nepal = _multi("Nepal eligibility (as extracted)", facet_options(saved, "nepal_eligibility"), "sv_nepal")
        with c3:
            overall = _multi("Overall eligibility", ["CONFIRMED", "UNCLEAR", "NOT_MET"], "sv_overall")
            rstatus = _multi("Research status", facet_options(saved, "research_status"), "sv_rstatus")
            stype = _multi("Source type", facet_options(saved, "source_type"), "sv_stype")
            score = st.slider("Match score", 0, 100, (0, 100), key="sv_score")
        d1, d2, d3, d4 = st.columns(4)
        date_from = d1.date_input("Deadline from", value=None, key="sv_dfrom")
        date_to = d2.date_input("Deadline to", value=None, key="sv_dto")
        keep_unknown = d3.checkbox("Include unknown deadlines", value=True, key="sv_keepunk")
        review_only = d4.checkbox("Needs review only", key="sv_review")

    s1, s2 = st.columns([3, 1])
    sort_label = s1.selectbox("Sort by", list(SORT_OPTIONS), key="sv_sort")
    descending = s2.checkbox("Descending", value=sort_label != "Name", key="sv_desc")

    criteria = {
        "text": text, "countries": countries, "providers": providers, "subjects": subjects, "degree_levels": degrees,
        "funding_types": funding, "funding_classes": fclass, "deadline_statuses": dstatus, "nepal_eligibility": nepal,
        "overall_eligibility": overall, "research_statuses": rstatus, "source_types": stype,
        "min_score": score[0] if score[0] > 0 else None, "max_score": score[1] if score[1] < 100 else None,
        "date_from": date_from.isoformat() if date_from else "", "date_to": date_to.isoformat() if date_to else "",
        "include_unknown_dates": keep_unknown, "needs_review_only": review_only,
        "sort_by": SORT_OPTIONS[sort_label], "descending": descending,
    }
    rows = apply_filters(saved, criteria)
    st.write(f"**{len(rows)}** of {len(saved)} opportunities")
    if not rows:
        st.info("No opportunity matches these filters. Clear some filters to see more.")
        return

    st.dataframe([{
        "ID": o["id"], "Scholarship": o["name"] or "(unnamed)", "Provider": o["provider"] or UNKNOWN, "Country": o["country"] or UNKNOWN,
        "Funding": o["funding_type"].replace("_", " "), "Deadline": o["deadline_status"], "Deadline (Nepal time)": o["deadline_local"],
        "Nepal (extracted)": o["nepal_eligibility"].replace("_", " "), "Eligibility": o["overall_eligibility"], "Match": o["match_score"],
        "Status": o["research_status"], "Source": o["source_type"].replace("_", " "), "Review": "yes" if o["needs_review"] else "",
    } for o in rows], width="stretch", hide_index=True)
    st.download_button("Download CSV", export_csv(rows), "scholarships_export.csv", "text/csv", key="sv_csv")

    st.subheader("Details")
    ids = [o["id"] for o in rows]
    chosen = st.selectbox("Open record", ids, format_func=lambda i: f"#{i} " + next(o["name"] or "(unnamed)" for o in rows if o["id"] == i), key="sv_pick")
    opp = next(o for o in rows if o["id"] == chosen)
    opportunity_detail(opp, "sv")
