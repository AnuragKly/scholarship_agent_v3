"""Dashboard page: summary cards, upcoming deadlines, items needing attention."""

from __future__ import annotations

import streamlit as st

from .. import repository
from ..filters import enrich, summarize
from .components import note
from .state import AppContext


def new_discovery_count(ctx: AppContext, saved: list) -> int:
    """Count stored search leads that are still 'new' and not already saved."""
    saved_urls = {o["normalized_url"] for o in saved if o.get("normalized_url")}
    return sum(1 for d in repository.list_discoveries(ctx.db, "new") if d["normalized_url"] not in saved_urls)


def render(ctx: AppContext) -> None:
    """Render the Dashboard page.

    Args:
        ctx: Application context.
    """
    st.title("Dashboard")
    st.caption("Your personal Master's scholarship research overview. Deadlines are shown in Nepal time (Asia/Kathmandu).")
    for issue in ctx.config_issues:
        (st.error if issue.level == "error" else st.warning)(issue.message)

    saved = enrich(repository.list_opportunities(ctx.db), closing_soon_days=ctx.cfg.closing_soon_days)
    stats = summarize(saved, new_discovery_count(ctx, saved))
    cols = st.columns(3)
    cards = [
        ("Saved opportunities", stats["saved"]), ("New discoveries (not saved)", stats["new_discoveries"]),
        ("Fully funded", stats["fully_funded"]), ("Deadlines approaching", stats["deadlines_approaching"]),
        ("Verified by you", stats["verified"]), ("Eligibility unclear", stats["unclear_eligibility"]),
    ]
    for i, (label, value) in enumerate(cards):
        cols[i % 3].metric(label, value)

    if stats["needs_review"]:
        note(f"{stats['needs_review']} saved record(s) need your review (changes were detected or a verified record changed). Open 'Change History'.")

    if not saved:
        st.info("Nothing saved yet. Go to **Discover** to search for scholarships, or paste an official scholarship page address. "
                "Records are saved only when you click **Add Opportunity**.")
        st.caption("Search and AI extraction use paid third-party APIs (OpenAI, Tavily). Usage may incur separate charges on your own accounts.")
        return

    st.subheader("Upcoming deadlines")
    upcoming = [o for o in saved if o["deadline_status"] in ("Open", "Closing soon")]
    upcoming.sort(key=lambda o: o.get("deadline_date") or "9999")
    if upcoming:
        st.dataframe([{
            "ID": o["id"], "Scholarship": o["name"], "Country": o["country"] or "Unknown", "Status": o["deadline_status"],
            "Deadline (Nepal time)": o["deadline_local"], "Days left": o["days_left"], "Eligibility": o["overall_eligibility"],
            "Match": o["match_score"],
        } for o in upcoming[:10]], width="stretch", hide_index=True)
    else:
        st.caption("No saved opportunity currently has a known, open deadline.")

    unknown = [o for o in saved if o["deadline_status"] == "Unknown"]
    if unknown:
        st.subheader("Deadline unknown (NOT assumed open)")
        st.dataframe([{"ID": o["id"], "Scholarship": o["name"], "Country": o["country"] or "Unknown"} for o in unknown[:10]],
                     width="stretch", hide_index=True)

    st.subheader("Top matches")
    st.caption("Match score ranks relevance only. It never means you are eligible.")
    top = sorted(saved, key=lambda o: -float(o["match_score"]))[:5]
    st.dataframe([{
        "ID": o["id"], "Scholarship": o["name"], "Match": o["match_score"], "Eligibility": o["overall_eligibility"],
        "Deadline": o["deadline_status"],
    } for o in top], width="stretch", hide_index=True)
    st.caption("Search and AI extraction use paid third-party APIs (OpenAI, Tavily). Usage may incur separate charges on your own accounts.")
