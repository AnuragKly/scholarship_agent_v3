"""Discover page: search, research a page, review the structured result, Add Opportunity."""

from __future__ import annotations

import streamlit as st

from .. import repository
from ..changes import compare_records
from ..dedup import find_duplicates
from ..discovery import SearchUnavailable, build_queries, search_many
from ..pipeline import (
    ResearchResult, add_linked_page, build_payload, research_pasted_text, research_url,
)
from ..utils import normalize_url, safe_error
from .components import (
    SOURCE_LABEL, conflicts_panel, deadline_block, eligibility_table, evidence_list, funding_breakdown,
    link_button, match_breakdown, note, record_overview, show_badges, sources_table,
)
from .state import AppContext

FUNDING_CHOICES = {"Fully funded": "fully_funded", "Partial scholarships too": "partial", "Any funding": "any"}


def _saved_urls(ctx: AppContext) -> set:
    """Return normalised URLs (official and canonical) of all saved opportunities."""
    urls = set()
    for o in repository.list_opportunities(ctx.db):
        urls |= {o["normalized_url"], normalize_url(o.get("canonical_url", ""))}
    return urls - {""}


def _run_research(ctx: AppContext, url: str, use_ai: bool) -> None:
    """Research ``url`` and keep the result in session state."""
    with st.spinner("Opening the page and reading it… (AI extraction can take 10-40 seconds)"):
        st.session_state["research"] = research_url(url, ctx.cfg, ctx.profile, ctx.weights, use_ai)
    st.session_state["pending_add"] = False


def _mark_discovery(ctx: AppContext, url: str, status: str) -> None:
    """Set the status of the stored lead matching ``url`` (no-op if none)."""
    norm = normalize_url(url)
    for d in repository.list_discoveries(ctx.db):
        if d["normalized_url"] == norm:
            repository.set_discovery_status(ctx.db, d["id"], status)


def _search_section(ctx: AppContext) -> None:
    """Render the search form and run searches."""
    st.subheader("1. Search for scholarships")
    if not ctx.cfg.has_search:
        st.warning("Search is off because TAVILY_API_KEY is not set. You can still paste an official page address below.")
    with st.form("search_form"):
        subjects = st.text_input("Subjects (comma-separated)", ", ".join(ctx.profile["target_subjects"]))
        c1, c2, c3 = st.columns(3)
        countries = c1.text_input("Countries (optional)", ", ".join(ctx.profile["preferred_countries"]))
        funding_label = c2.selectbox("Funding", list(FUNDING_CHOICES),
                                     index=list(FUNDING_CHOICES.values()).index({"fully_funded": "fully_funded", "partial_ok": "partial"}.get(ctx.profile["funding_preference"], "any")))
        cycle = c3.text_input("Application year (optional)", "2027")
        st.caption(f"Queries always target scholarships open to {ctx.profile['nationality']} applicants. "
                   "Each search uses your Tavily quota; up to 6 queries are sent.")
        go = st.form_submit_button("Search", type="primary", disabled=not ctx.cfg.has_search)
    if go:
        queries = build_queries([s.strip() for s in subjects.split(",")], ctx.profile["target_degree"],
                                [c.strip() for c in countries.split(",")], FUNDING_CHOICES[funding_label], cycle.strip(),
                                ctx.profile["nationality"])
        with st.expander("Queries sent", expanded=False):
            for q in queries:
                st.write(q)
        try:
            with st.spinner("Searching…"):
                results, errors = search_many(queries, ctx.cfg.tavily_api_key)
            added = repository.upsert_discoveries(ctx.db, results)
            st.session_state["last_search_results"] = results
            st.success(f"Found {len(results)} result(s); {added} new lead(s) stored. Results are leads only: open each page to read the official details.")
            for e in errors:
                st.warning(e)
        except SearchUnavailable as exc:
            st.error(str(exc))
        except Exception as exc:  # keep the app alive on unexpected errors
            st.error(f"Search failed unexpectedly. {safe_error(exc)}")


def _lead_card(ctx: AppContext, lead: dict, saved: bool) -> None:
    """Render one search lead with its actions."""
    with st.container(border=True):
        st.markdown(f"**{lead['title']}**")
        text, kind = SOURCE_LABEL.get(lead["source_type"], SOURCE_LABEL["unverified"])
        show_badges([(text, kind), (lead["source_domain"], "grey"), ("Already saved" if saved else "New discovery", "blue" if saved else "green")])
        st.caption(lead["url"])
        if lead.get("snippet"):
            st.write(lead["snippet"][:300] + ("…" if len(lead["snippet"]) > 300 else ""))
        c1, c2, c3 = st.columns([1, 1, 3])
        if not saved:
            if c1.button("Research This Page", key=f"res_{lead['id']}", type="primary"):
                _run_research(ctx, lead["url"], ctx.cfg.has_openai)
                st.rerun()
            if c2.button("Dismiss", key=f"dis_{lead['id']}"):
                repository.set_discovery_status(ctx.db, lead["id"], "dismissed")
                st.rerun()
        with c3:
            link_button("Open in browser", lead["url"], key=f"open_{lead['id']}")


def _leads_section(ctx: AppContext) -> None:
    """Render stored leads, separating new discoveries from already-saved ones."""
    saved_urls = _saved_urls(ctx)
    leads = [d for d in repository.list_discoveries(ctx.db) if d["status"] != "dismissed"]
    new = [d for d in leads if d["normalized_url"] not in saved_urls]
    already = [d for d in leads if d["normalized_url"] in saved_urls]
    tab_new, tab_saved = st.tabs([f"New discoveries ({len(new)})", f"Already saved ({len(already)})"])
    with tab_new:
        if not new:
            st.info("No new leads. Run a search above, or paste an official page address.")
        for d in new[:30]:
            _lead_card(ctx, d, False)
        if len(new) > 30:
            st.caption(f"Showing 30 of {len(new)} leads.")
    with tab_saved:
        if not already:
            st.caption("None of your search results are saved yet.")
        for d in already:
            _lead_card(ctx, d, True)


def _manual_section(ctx: AppContext) -> None:
    """Render manual official-URL research and the paste-text fallback."""
    st.subheader("2. Or research an official page directly")
    url = st.text_input("Official page address", placeholder="https://…", key="manual_url")
    use_ai = st.checkbox("Use AI extraction", value=ctx.cfg.has_openai, disabled=not ctx.cfg.has_openai,
                         help="Needs OPENAI_API_KEY. Without it a clearly labelled, limited heuristic fallback is used.")
    if not ctx.cfg.has_openai:
        st.caption("No OpenAI key: results will come from the limited heuristic fallback and are labelled NOT AI-verified.")
    if st.button("Research This Page", key="manual_go", type="primary", disabled=not url.strip()):
        _run_research(ctx, url.strip(), use_ai)
        st.rerun()
    with st.expander("Paste page text (use this if the page is blocked or blank)"):
        pasted_url = st.text_input("Official page address the text came from", key="paste_url")
        text = st.text_area("Page text (copy from your browser)", height=160, key="paste_text")
        if st.button("Research pasted text", key="paste_go"):
            with st.spinner("Reading the text…"):
                st.session_state["research"] = research_pasted_text(pasted_url, text, ctx.cfg, ctx.profile, ctx.weights, use_ai)
            st.session_state["pending_add"] = False
            st.rerun()


def _result_section(ctx: AppContext, result: ResearchResult) -> None:
    """Render the structured research result and the Add Opportunity workflow."""
    st.divider()
    st.subheader("3. Research result")
    primary_fetch = result.pages[0].fetch if result.pages else None
    if not result.ok:
        st.error(f"This page could not be researched. {result.error}")
        st.caption("Nothing was extracted and nothing has been invented. Try another address, or paste the page text.")
        return
    prim = result.primary
    rec = result.record
    st.markdown(f"### {rec.get('name') or '(name not found)'}")
    if result.ai_verified:
        show_badges([("AI extraction (verify important facts)", "blue")])
    else:
        st.error("NOT AI-verified: this is a limited heuristic fallback. Most fields are unknown and nothing here confirms eligibility.")
    text, kind = SOURCE_LABEL.get(prim.source.source_type, SOURCE_LABEL["unverified"])
    show_badges([(text, kind), (prim.source.domain, "grey")])
    if prim.source.is_third_party:
        st.warning("This is a third-party website, not the scholarship owner. Find and research the official page before relying on it.")
    elif not prim.source.likely_official:
        st.info(prim.source.note)
    for w in dict.fromkeys(result.warnings):
        st.warning(w)
    if prim.fetch.title:
        st.caption(f"Page title: {prim.fetch.title} · retrieved {prim.fetch.retrieved_at[:19]} UTC")
    link_button("Open official page", prim.fetch.final_url or prim.fetch.requested_url, key="res_open")

    st.write(rec.get("summary") or "No summary.")
    t1, t2, t3, t4, t5 = st.tabs(["Facts", "Eligibility", "Match score", "Evidence", "Missing information"])
    with t1:
        record_overview(rec)
        funding_breakdown(rec)
        d = result.deadline
        deadline_block({"status": d.status, "local": d.local_display, "original": d.original_text, "timezone": d.timezone_label,
                        "cycle": d.cycle, "days_left": d.days_left, "flags": d.flags, "applies_to": rec.get("deadline_applies_to", "")})
        st.markdown("**How to apply (as stated):** " + (rec.get("application_instructions") or "not stated"))
    with t2:
        eligibility_table(result.eligibility.to_dict())
    with t3:
        match_breakdown(result.match.to_dict())
    with t4:
        conflicts_panel(result.conflicts)
        evidence_list(rec)
        sources_table([{"role": p.role, "final_url": p.fetch.final_url, "requested_url": p.fetch.requested_url, "source_type": p.source.source_type,
                        "source_domain": p.source.domain, "retrieved_at": p.fetch.retrieved_at, "ok": p.fetch.ok, "error": p.fetch.error}
                       for p in result.pages])
    with t5:
        if result.missing:
            st.markdown("The sources did not provide: " + ", ".join(result.missing))
        for u in rec.get("uncertainties", []):
            st.markdown(f"- {u}")
        for u in rec.get("interpretations", []):
            st.markdown(f"- *{u}*")

    if primary_fetch and primary_fetch.links:
        with st.expander(f"Linked official pages that may hold eligibility or application details ({len(primary_fetch.links)})"):
            st.caption("Researching a linked page adds it as extra evidence. If it disagrees with the main page, both values are kept.")
            for i, link in enumerate(primary_fetch.links):
                c1, c2 = st.columns([4, 1])
                c1.write(f"{link['text']}  \n{link['url']}")
                if c2.button("Research", key=f"linked_{i}"):
                    with st.spinner("Reading the linked page…"):
                        st.session_state["research"] = add_linked_page(result, link["url"], ctx.cfg, ctx.profile, ctx.weights,
                                                                       ctx.cfg.has_openai)
                    st.rerun()

    st.markdown("---")
    _add_section(ctx, result)


def _add_section(ctx: AppContext, result: ResearchResult) -> None:
    """Handle Add Opportunity including the duplicate-detection and update-existing workflow."""
    payload = build_payload(result)
    existing = repository.list_opportunities(ctx.db)
    candidate = {
        "official_url": payload["official_url"], "canonical_url": payload["canonical_url"], "name": result.record.get("name", ""),
        "provider": result.record.get("provider", ""),
        "urls": [p.fetch.final_url for p in result.pages if p.fetch.ok],
    }
    dups = find_duplicates(candidate, existing)
    saved_id = st.session_state.get("saved_id")
    if saved_id:
        st.success(f"Saved as opportunity #{saved_id}. Find it under 'Saved Opportunities'.")
        return
    if not dups:
        if st.button("Add Opportunity", type="primary", key="add_opp"):
            try:
                new_id = repository.add_opportunity(ctx.db, payload)
                _mark_discovery(ctx, payload["official_url"], "researched")
                st.session_state["saved_id"] = new_id
                st.rerun()
            except repository.DuplicateOpportunityError:
                st.error("This official URL was just saved by another action. Reload the page.")
            except Exception as exc:
                st.error(f"Could not save. {safe_error(exc)}")
        st.caption("Saves the complete structured record, sources, evidence, eligibility and match score.")
        return

    st.warning("This looks like an opportunity you already saved. Nothing is overwritten automatically.")
    for m in dups:
        reason = "same official URL" if m.reason == "same_url" else f"similar name/provider ({m.similarity:.0%})"
        st.markdown(f"- **#{m.opportunity_id} {m.name}** ({m.provider or 'provider unknown'}): {reason}")
    target = st.selectbox("Existing record to compare with", [m.opportunity_id for m in dups],
                          format_func=lambda i: f"#{i} " + next(m.name for m in dups if m.opportunity_id == i), key="dup_target")
    old = next(o for o in existing if o["id"] == target)
    changes = compare_records(old["record"], result.record)
    if changes:
        st.markdown("**What would change if you update the saved record:**")
        st.dataframe([{"Field": c.label, "Saved value": c.old or "(empty)", "New value": c.new or "(empty)"} for c in changes],
                     width="stretch", hide_index=True)
    else:
        st.caption("No tracked fields differ from the saved record.")
    if old.get("verified_at"):
        note("The saved record is verified. Updating it keeps the history and flags it 'Needs review'.")
    c1, c2 = st.columns(2)
    if c1.button("Update existing record (history kept)", key="update_existing", type="primary"):
        try:
            repository.update_opportunity_from_research(ctx.db, target, payload, changes)
            st.session_state["saved_id"] = target
            st.rerun()
        except Exception as exc:
            st.error(f"Could not update. {safe_error(exc)}")
    only_similar = all(m.reason != "same_url" for m in dups)
    if only_similar and c2.button("Save as a separate record anyway", key="add_anyway"):
        try:
            st.session_state["saved_id"] = repository.add_opportunity(ctx.db, payload)
            st.rerun()
        except repository.DuplicateOpportunityError:
            st.error("This URL is already saved; use 'Update existing record'.")
    elif not only_similar:
        c2.caption("A second copy with the same official URL is not allowed.")


def render(ctx: AppContext) -> None:
    """Render the Discover page.

    Args:
        ctx: Application context.
    """
    st.title("Discover")
    st.caption("Search results are leads only. The app opens each official page and reads it before extracting anything. "
               "Search and AI extraction may incur separate charges on your own API accounts.")
    result = st.session_state.get("research")
    if result is not None and st.button("Start a new search / research", key="clear_research"):
        st.session_state.pop("research", None)
        st.session_state.pop("saved_id", None)
        st.rerun()
    if result is not None:
        _result_section(ctx, result)
        return
    st.session_state.pop("saved_id", None)
    _search_section(ctx)
    _leads_section(ctx)
    _manual_section(ctx)
