"""Review & Verify page: manual criterion decisions, verification, status, notes, deletion."""

from __future__ import annotations

import streamlit as st

from .. import repository
from ..eligibility import STATES
from ..filters import enrich
from ..pipeline import evaluate
from ..sources import SOURCE_TYPES
from ..utils import iso, utc_now
from .components import conflicts_panel, eligibility_table, evidence_list, link_button, note, show_badges, opportunity_badges
from .state import AppContext


def render(ctx: AppContext) -> None:
    """Render the Review & Verify page.

    Args:
        ctx: Application context.
    """
    st.title("Review & Verify")
    st.caption("Check each record against the official page yourself, record your own decisions, then mark it verified. "
               "Automatic results are never marked verified for you.")
    saved = enrich(repository.list_opportunities(ctx.db), closing_soon_days=ctx.cfg.closing_soon_days)
    if not saved:
        st.info("Nothing to review yet. Save an opportunity from the Discover page first.")
        return
    queue = sorted(saved, key=lambda o: (not o["needs_review"], bool(o["verified_at"]), -float(o["match_score"])))
    pick = st.selectbox("Record", [o["id"] for o in queue], key="rv_pick",
                        format_func=lambda i: (lambda o: f"#{i} {o['name'] or '(unnamed)'}"
                                               + (" — NEEDS REVIEW" if o["needs_review"] else (" — verified" if o["verified_at"] else "")))(next(o for o in queue if o["id"] == i)))
    opp = next(o for o in queue if o["id"] == pick)
    show_badges(opportunity_badges(opp))
    link_button("Open official page to check", opp["official_url"], key="rv_open")
    if opp["needs_review"]:
        note("Changes were detected for this record, or a verified record changed. Review them under 'Change History' before verifying again.")
    if not opp["ai_verified"]:
        note("This record is not AI-extracted. Check every field on the official page.")

    st.subheader("Eligibility decisions")
    eligibility_table(opp["eligibility"])
    conflicts_panel(opp["conflicts"])

    overrides = dict(opp.get("overrides", {}))
    with st.form(f"override_form_{pick}"):
        st.markdown("**Override a criterion after checking the official page** (kept separately and clearly marked as your decision)")
        labels = {c["key"]: c["label"] for c in opp["eligibility"].get("criteria", [])}
        key = st.selectbox("Criterion", list(labels), format_func=lambda k: labels[k])
        c1, c2 = st.columns([1, 3])
        status = c1.selectbox("Your decision", STATES)
        reason = c2.text_input("Why (quote or page section)", max_chars=300)
        c3, c4 = st.columns(2)
        apply_btn = c3.form_submit_button("Save decision", type="primary")
        clear_btn = c4.form_submit_button("Remove my decision for this criterion")
    if apply_btn or clear_btn:
        if apply_btn:
            overrides[key] = {"status": status, "note": reason.strip(), "at": iso(utc_now())}
        else:
            overrides.pop(key, None)
        elig, _, match = evaluate(opp["record"], ctx.profile, ctx.weights, ctx.cfg, overrides, bool(opp["ai_verified"]), opp["conflicts"])
        repository.save_review_state(ctx.db, pick, overrides, elig.to_dict(), match.to_dict())
        st.success("Saved.")
        st.rerun()

    st.subheader("Source type")
    cur = opp["source_type"] if opp["source_type"] in SOURCE_TYPES else "unverified"
    new_type = st.selectbox("Who owns the source page?", SOURCE_TYPES, index=SOURCE_TYPES.index(cur), key=f"rv_stype_{pick}")
    if new_type != cur and st.button("Update source type", key=f"rv_stype_btn_{pick}"):
        repository.save_review_state(ctx.db, pick, overrides, opp["eligibility"], opp["match"], source_type=new_type)
        st.rerun()

    st.subheader("Evidence to check")
    evidence_list(opp["record"])

    st.subheader("Verification and status")
    c1, c2 = st.columns(2)
    with c1:
        if opp["verified_at"]:
            st.success(f"Verified by you on {opp['verified_at'][:10]}")
            if st.button("Remove verification", key=f"rv_unv_{pick}"):
                repository.set_verified(ctx.db, pick, False)
                st.rerun()
        else:
            confirm = st.checkbox("I compared this record with the official page and it is correct", key=f"rv_conf_{pick}")
            if st.button("Mark as verified", type="primary", disabled=not confirm, key=f"rv_ver_{pick}"):
                repository.set_verified(ctx.db, pick, True)
                st.rerun()
    with c2:
        statuses = repository.RESEARCH_STATUSES
        idx = statuses.index(opp["research_status"]) if opp["research_status"] in statuses else 0
        new_status = st.selectbox("Research status", statuses, index=idx, key=f"rv_status_{pick}")
        if new_status != opp["research_status"] and st.button("Save status", key=f"rv_status_btn_{pick}"):
            repository.set_status(ctx.db, pick, new_status)
            st.rerun()

    st.subheader("Your notes")
    notes = st.text_area("Notes (stored only on this computer)", opp["notes"], height=120, max_chars=5000, key=f"rv_notes_{pick}")
    if st.button("Save notes", key=f"rv_notes_btn_{pick}"):
        repository.set_notes(ctx.db, pick, notes)
        st.success("Notes saved.")

    with st.expander("Delete this record"):
        st.warning("This permanently deletes the record, its snapshots and its change history.")
        if st.checkbox(f"Yes, delete #{pick}", key=f"rv_del_chk_{pick}") and st.button("Delete permanently", key=f"rv_del_{pick}"):
            repository.delete_opportunity(ctx.db, pick)
            st.rerun()
