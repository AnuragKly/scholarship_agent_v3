"""Change History page: re-check saved sources, review detected differences, browse snapshots."""

from __future__ import annotations

import streamlit as st

from .. import repository
from ..pipeline import apply_snapshot, check_source
from ..utils import safe_error
from .components import note
from .state import AppContext


def render(ctx: AppContext) -> None:
    """Render the Change History page.

    Args:
        ctx: Application context.
    """
    st.title("Change History")
    st.caption("Re-read the official pages of saved opportunities and see what changed. "
               "Detected changes are stored for your review; saved records and verified decisions are never changed automatically.")
    opps = repository.list_opportunities(ctx.db)
    if not opps:
        st.info("No saved opportunities yet, so there is nothing to check.")
        return

    st.subheader("Check sources")
    if not ctx.cfg.has_openai:
        st.caption("No OpenAI key: only page-content changes (by fingerprint) can be detected, not field-level changes.")
    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("Opportunity", [o["id"] for o in opps], key="ch_pick",
                        format_func=lambda i: f"#{i} " + next(o["name"] or "(unnamed)" for o in opps if o["id"] == i))
    run_one = c2.button("Check this source", type="primary", key="ch_one")
    run_all = st.button("Check all saved sources", key="ch_all", help="Reads every saved page; AI comparison may incur charges.")
    targets = [o for o in opps if o["id"] == pick] if run_one else (opps if run_all else [])
    if targets:
        progress = st.progress(0.0)
        for i, o in enumerate(targets):
            with st.spinner(f"Checking #{o['id']} {o['name']}…"):
                try:
                    res = check_source(ctx.db, o, ctx.cfg, ctx.profile, ctx.weights)
                except Exception as exc:
                    st.error(f"#{o['id']}: check failed. {safe_error(exc)}")
                    continue
            if res.error and not res.checked_urls:
                st.error(f"#{o['id']} {o['name']}: {res.error}")
            elif res.changes:
                st.warning(f"#{o['id']} {o['name']}: {len(res.changes)} change(s) detected and stored for review.")
            else:
                st.success(f"#{o['id']} {o['name']}: no changes detected.")
            if res.error and res.checked_urls:
                st.caption(res.error)
            progress.progress((i + 1) / len(targets))

    st.subheader("Detected changes")
    show_all = st.checkbox("Include changes I already reviewed", key="ch_showall")
    changes = repository.list_changes(ctx.db, only_unreviewed=not show_all)
    if not changes:
        st.info("No changes to show. Run a source check above.")
    for ch in changes:
        with st.container(border=True):
            status = "applied to the saved record" if ch["applied"] else "NOT applied (saved record unchanged)"
            st.markdown(f"**#{ch['opportunity_id']} {ch['opportunity_name']}** — {ch['field']}  \n"
                        f"Detected {ch['detected_at'][:16]} UTC · {status}")
            st.dataframe([{"Previously": ch["old_value"] or "(empty)", "Now": ch["new_value"] or "(empty)", "Source": ch["source_url"]}],
                         width="stretch", hide_index=True)
            if ch["needs_review"] and not ch["reviewed_at"]:
                if st.button("Mark reviewed", key=f"ch_rev_{ch['id']}"):
                    repository.review_change(ctx.db, ch["id"], "reviewed by user")
                    st.rerun()
            elif ch["reviewed_at"]:
                st.caption(f"Reviewed {ch['reviewed_at'][:16]} UTC")

    st.subheader("Snapshot history")
    snap_pick = st.selectbox("Show history for", [o["id"] for o in opps], key="ch_snap_pick",
                             format_func=lambda i: f"#{i} " + next(o["name"] or "(unnamed)" for o in opps if o["id"] == i))
    snaps = repository.list_snapshots(ctx.db, snap_pick)
    if not snaps:
        st.caption("No snapshots yet.")
    for s in snaps:
        with st.expander(f"Snapshot {s['id']} · {s['taken_at'][:16]} UTC · {s['reason']}"):
            st.caption(f"Source: {s['source_url']}")
            rec = s["record"]
            st.write({k: rec.get(k) for k in ("application_deadline", "deadline_date", "funding_type", "eligible_nationalities",
                                              "nepal_eligibility", "eligible_programmes", "min_gpa") if k in rec})
            if "not yet applied" in s["reason"]:
                note("This snapshot was found by a source check and has NOT been applied to the saved record.")
                if st.button("Apply this snapshot to the saved record", key=f"ch_apply_{s['id']}"):
                    try:
                        apply_snapshot(ctx.db, snap_pick, s["id"], ctx.cfg, ctx.profile, ctx.weights)
                        st.success("Applied. The previous state is kept in history; verified records are now flagged 'Needs review'.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not apply. {safe_error(exc)}")
