"""Streamlit entry point: page configuration and navigation only. All logic lives in the package."""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Scholarship Research Agent", page_icon="🎓", layout="wide")

from scholarship_agent.ui import components, page_changes, page_dashboard, page_discover, page_review, page_saved, page_settings
from scholarship_agent.ui.state import get_context

PAGES = {
    "Dashboard": page_dashboard,
    "Discover": page_discover,
    "Saved Opportunities": page_saved,
    "Review & Verify": page_review,
    "Change History": page_changes,
    "Settings": page_settings,
}


def main() -> None:
    """Draw the sidebar navigation and render the selected page."""
    components.inject_css()
    try:
        ctx = get_context()
    except Exception as exc:  # startup problem: show an actionable message, never a raw trace
        from scholarship_agent.utils import safe_error

        st.error("The app could not start. Check that the 'data' folder is writable and the database file is not corrupted.")
        st.caption(safe_error(exc))
        return
    st.sidebar.title("🎓 Scholarship Agent v3")
    choice = st.sidebar.radio("Navigate", list(PAGES), key="nav", label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption("Official sources first. Nothing is claimed without evidence. Dates shown in Nepal time.")
    st.sidebar.caption(f"AI: {'on' if ctx.cfg.has_openai else 'off (fallback)'} · Search: {'on' if ctx.cfg.has_search else 'off'}")
    PAGES[choice].render(ctx)


main()
