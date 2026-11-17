"""Settings page: applicant profile, matching weights, reset, and a status panel (no secrets shown)."""

from __future__ import annotations

import streamlit as st

from .. import settings as app_settings
from ..matching import COMPONENT_LABELS, DEFAULT_WEIGHTS
from ..pipeline import recompute_all
from .state import AppContext


def _opt_text(value) -> str:
    """Show an optional number as text ('' when unset)."""
    return "" if value is None else f"{value:g}"


def render(ctx: AppContext) -> None:
    """Render the Settings page.

    Args:
        ctx: Application context.
    """
    st.title("Settings")
    st.caption("Everything about you is stored in this computer's SQLite database. Optional fields can stay empty; "
               "empty means 'unknown', and the app never assumes a value for them.")
    p = ctx.profile
    tab_profile, tab_weights, tab_system = st.tabs(["Applicant profile", "Matching weights", "System status"])

    with tab_profile:
        with st.form("profile_form"):
            c1, c2 = st.columns(2)
            nationality = c1.text_input("Nationality", p["nationality"])
            residence = c2.text_input("Country of residence", p["country_of_residence"])
            education = c1.text_input("Current education", p["current_education"])
            status = c2.selectbox("Undergraduate degree status", app_settings.EDUCATION_STATUSES,
                                  index=app_settings.EDUCATION_STATUSES.index(p["education_status"]),
                                  format_func=lambda s: "In progress" if s == "in_progress" else "Completed")
            degree = c1.text_input("Target degree", p["target_degree"])
            funding = c2.selectbox("Funding preference", app_settings.FUNDING_PREFERENCES,
                                   index=app_settings.FUNDING_PREFERENCES.index(p["funding_preference"]),
                                   format_func=lambda s: {"fully_funded": "Fully funded only", "partial_ok": "Prefer full, accept exceptional partial",
                                                          "any": "Any funding"}[s])
            subjects = st.text_input("Target subjects (comma-separated)", ", ".join(p["target_subjects"]))
            countries = st.text_input("Preferred countries (comma-separated, optional)", ", ".join(p["preferred_countries"]))
            st.markdown("**Optional details** (leave empty if unknown or you prefer not to say)")
            o1, o2, o3 = st.columns(3)
            gpa = o1.text_input("GPA", _opt_text(p["gpa"]))
            gpa_scale = o2.text_input("GPA scale (e.g. 4, 10, 100)", _opt_text(p["gpa_scale"]))
            age = o3.text_input("Age", _opt_text(p["age"]))
            t1, t2, t3 = st.columns(3)
            test = t1.selectbox("English test", app_settings.ENGLISH_TESTS,
                                index=app_settings.ENGLISH_TESTS.index(p["english_test"]) if p["english_test"] in app_settings.ENGLISH_TESTS else 0,
                                format_func=lambda s: s or "(none / not taken)")
            score = t2.text_input("English score", _opt_text(p["english_score"]))
            years = t3.text_input("Work experience (years)", _opt_text(p["work_experience_years"]))
            other = st.text_area("Other applicant-specific criteria or notes", p["other_criteria"], height=80, max_chars=500)
            saved = st.form_submit_button("Save profile", type="primary")
        if saved:
            errors = app_settings.save_profile(ctx.db, {
                "nationality": nationality, "country_of_residence": residence, "current_education": education,
                "education_status": status, "target_degree": degree, "funding_preference": funding,
                "target_subjects": subjects, "preferred_countries": countries, "gpa": gpa, "gpa_scale": gpa_scale, "age": age,
                "english_test": test, "english_score": score, "work_experience_years": years, "other_criteria": other})
            if errors:
                for e in errors:
                    st.error(e)
            else:
                st.success("Profile saved.")
                st.session_state["profile_changed"] = True
                st.rerun()
        if st.session_state.pop("profile_changed", False) or st.session_state.get("need_recompute"):
            st.session_state["need_recompute"] = True
        if st.session_state.get("need_recompute"):
            st.info("Saved opportunities still show scores based on your old settings.")
            if st.button("Recalculate eligibility and match for all saved opportunities", key="recompute"):
                n = recompute_all(ctx.db, ctx.cfg, ctx.profile, ctx.weights)
                st.session_state["need_recompute"] = False
                st.success(f"Recalculated {n} record(s). Extracted data was not changed.")

    with tab_weights:
        st.write("Weights say how much each component counts. They are normalised, so they do not have to add up to 100.")
        with st.form("weights_form"):
            vals = {}
            cols = st.columns(2)
            for i, key in enumerate(DEFAULT_WEIGHTS):
                vals[key] = cols[i % 2].number_input(COMPONENT_LABELS[key], 0.0, 1000.0, float(ctx.weights[key]), 5.0, key=f"w_{key}")
            w_saved = st.form_submit_button("Save weights", type="primary")
        if w_saved:
            errors = app_settings.save_weights(ctx.db, vals)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                st.success("Weights saved.")
                st.session_state["need_recompute"] = True
                st.rerun()
        st.caption("The match score is a relevance ranking. It is separate from eligibility and never confirms it.")

    with tab_system:
        st.write(f"**AI extraction:** {'configured (model: ' + ctx.cfg.openai_model + ')' if ctx.cfg.has_openai else 'NOT configured (heuristic fallback only)'}")
        st.write(f"**Search (Tavily):** {'configured' if ctx.cfg.has_search else 'NOT configured'}")
        st.write(f"**Database file:** `{ctx.cfg.db_path.name}`")
        st.caption("API keys are read from your .env file and are never shown here. Usage of OpenAI and Tavily may incur separate charges on your accounts.")
        for issue in ctx.config_issues:
            (st.error if issue.level == "error" else st.warning)(issue.message)

    st.divider()
    with st.expander("Reset to defaults"):
        st.warning("This resets your applicant profile and matching weights. Saved opportunities are not deleted.")
        if st.checkbox("Yes, reset my settings", key="reset_confirm") and st.button("Reset settings", key="reset_btn"):
            app_settings.reset_to_defaults(ctx.db)
            st.session_state["need_recompute"] = True
            st.session_state["reset_confirm"] = False
            st.rerun()
