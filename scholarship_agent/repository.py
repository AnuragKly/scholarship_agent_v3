"""Data access for opportunities, snapshots, change log and discoveries.

Every write uses parameterised SQL inside a transaction. Existing records are never
overwritten silently: updates always create a snapshot and change-log entries, and
a verified record that changes is flagged for human review.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from .database import Database
from .utils import dumps, iso, loads, normalize_url, utc_now

logger = logging.getLogger("scholarship_agent.repository")

RESEARCH_STATUSES = [
    "Researched", "Needs review", "Verified", "Applying", "Applied", "Not pursuing", "Imported (V2)",
]
_JSON_COLUMNS = {
    "record_json": "record", "eligibility_json": "eligibility", "match_json": "match",
    "conflicts_json": "conflicts", "sources_json": "sources", "overrides_json": "overrides",
}
_JSON_DEFAULTS = {"record": {}, "eligibility": {}, "match": {}, "conflicts": [], "sources": [], "overrides": {}}


class DuplicateOpportunityError(Exception):
    """Raised when saving would create a second record for the same normalised URL."""


def _parse(row: dict) -> dict:
    """Convert a raw database row to a dict with parsed JSON fields."""
    out = dict(row)
    for col, name in _JSON_COLUMNS.items():
        out[name] = loads(out.pop(col, None), _JSON_DEFAULTS[name])
    for col in ("subject_areas", "eligible_programmes"):
        out[col] = loads(out.get(col), [])
    return out


def _derived_columns(payload: dict) -> dict:
    """Build the flat, filterable columns from a save payload."""
    rec = payload["record"]
    official = payload.get("official_url", "")
    return {
        "name": rec.get("name", ""), "provider": rec.get("provider", ""), "country": rec.get("country", ""),
        "degree_level": rec.get("degree_level", "unknown"), "funding_type": rec.get("funding_type", "unknown"),
        "subject_areas": dumps(rec.get("subject_areas", [])),
        "eligible_programmes": dumps(rec.get("eligible_programmes", [])),
        "nepal_eligibility": rec.get("nepal_eligibility", "not_stated"),
        "overall_eligibility": payload.get("eligibility", {}).get("overall", "UNCLEAR"),
        "deadline_date": rec.get("deadline_date", ""), "deadline_original": rec.get("application_deadline", ""),
        "deadline_timezone": rec.get("deadline_timezone", ""), "application_cycle": rec.get("application_cycle", ""),
        "official_url": official, "normalized_url": normalize_url(official),
        "canonical_url": payload.get("canonical_url", ""),
        "source_type": payload.get("source_type", "unverified"), "source_domain": payload.get("source_domain", ""),
        "extraction_method": payload.get("extraction_method", ""), "extraction_model": payload.get("extraction_model", ""),
        "ai_verified": 1 if payload.get("ai_verified") else 0,
        "retrieved_at": payload.get("retrieved_at", ""),
        "match_score": float(payload.get("match", {}).get("total", 0.0)),
        "record_json": dumps(rec), "eligibility_json": dumps(payload.get("eligibility", {})),
        "match_json": dumps(payload.get("match", {})), "conflicts_json": dumps(payload.get("conflicts", [])),
        "sources_json": dumps(payload.get("sources", [])), "overrides_json": dumps(payload.get("overrides", {})),
    }


def _snapshot(conn: sqlite3.Connection, opp_id: int, reason: str, record: dict, eligibility: dict,
              source_url: str = "", content_hash: str = "") -> int:
    """Insert a historical snapshot and return its id."""
    cur = conn.execute(
        "INSERT INTO opportunity_snapshots (opportunity_id, taken_at, reason, source_url, content_hash, record_json, eligibility_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (opp_id, iso(utc_now()), reason, source_url, content_hash, dumps(record), dumps(eligibility)),
    )
    return cur.lastrowid


def add_opportunity(db: Database, payload: dict) -> int:
    """Save a new opportunity (only called after the user clicks Add Opportunity).

    Args:
        db: Database.
        payload: Keys ``record``, ``eligibility``, ``match``, ``conflicts``, ``sources``,
            ``overrides`` (objects) plus ``official_url``, ``canonical_url``, ``source_type``,
            ``source_domain``, ``extraction_method``, ``extraction_model``, ``ai_verified``,
            ``retrieved_at`` and optional ``notes``.

    Returns:
        The new database id.

    Raises:
        DuplicateOpportunityError: If the normalised official URL is already saved.
    """
    cols = _derived_columns(payload)
    now = iso(utc_now())
    cols.update({"notes": payload.get("notes", ""), "created_at": now, "updated_at": now})
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    try:
        with db.transaction() as conn:
            cur = conn.execute(f"INSERT INTO opportunities ({names}) VALUES ({marks})", tuple(cols.values()))
            opp_id = cur.lastrowid
            _snapshot(conn, opp_id, "initial save", payload["record"], payload.get("eligibility", {}),
                      payload.get("official_url", ""), (payload.get("sources") or [{}])[0].get("content_hash", ""))
    except sqlite3.IntegrityError as exc:
        raise DuplicateOpportunityError("An opportunity with this official URL is already saved.") from exc
    return opp_id


def get_opportunity(db: Database, opp_id: int) -> Optional[dict]:
    """Return one opportunity with parsed JSON fields, or None."""
    rows = db.query("SELECT * FROM opportunities WHERE id = ?", (opp_id,))
    return _parse(rows[0]) if rows else None


def list_opportunities(db: Database) -> list:
    """Return all saved opportunities (newest first) with parsed JSON fields."""
    return [_parse(r) for r in db.query("SELECT * FROM opportunities ORDER BY id DESC")]


def delete_opportunity(db: Database, opp_id: int) -> None:
    """Delete an opportunity and (by cascade) its snapshots and change log."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM opportunities WHERE id = ?", (opp_id,))


def update_opportunity_from_research(
    db: Database, opp_id: int, payload: dict, changes: list, reason: str = "updated from new research"
) -> int:
    """Update an existing record with newly researched data, keeping history.

    The previous state is already in earlier snapshots; this adds a new snapshot,
    writes one change-log row per difference, and updates the row. If the record was
    verified, it is flagged ``needs_review`` and its status becomes 'Needs review' - a
    verified decision is never changed silently. User notes and manual overrides are kept
    unless the payload carries overrides.

    Args:
        db: Database.
        opp_id: Existing record id.
        payload: Same shape as for :func:`add_opportunity`.
        changes: List of ``changes.Change`` between the old and new records.
        reason: Snapshot label.

    Returns:
        The id of the new snapshot.

    Raises:
        KeyError: If ``opp_id`` does not exist.
    """
    old = get_opportunity(db, opp_id)
    if old is None:
        raise KeyError(opp_id)
    cols = _derived_columns(payload)
    if "overrides" not in payload:
        cols.pop("overrides_json")
    was_verified = bool(old["verified_at"])
    source_url = payload.get("official_url", old["official_url"])
    with db.transaction() as conn:
        snap_id = _snapshot(conn, opp_id, reason, payload["record"], payload.get("eligibility", {}), source_url,
                            (payload.get("sources") or [{}])[0].get("content_hash", ""))
        for ch in changes:
            conn.execute(
                "INSERT INTO change_log (opportunity_id, detected_at, field, old_value, new_value, source_url, snapshot_id, applied, needs_review) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (opp_id, iso(utc_now()), ch.field, ch.old, ch.new, source_url, snap_id, 1 if (was_verified or ch.affects_eligibility) else 0),
            )
        cols["updated_at"] = iso(utc_now())
        if was_verified:
            cols["needs_review"], cols["research_status"] = 1, "Needs review"
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(f"UPDATE opportunities SET {sets} WHERE id = ?", (*cols.values(), opp_id))
    return snap_id


def record_source_check(db: Database, opp_id: int, new_record: dict, new_eligibility: dict, changes: list,
                        source_url: str, content_hash: str, sources: list) -> Optional[int]:
    """Store the outcome of re-checking a saved source WITHOUT changing the saved record.

    If differences exist, a snapshot of the newly extracted state and unapplied
    change-log rows are written and the opportunity is flagged ``needs_review``.
    The saved record, eligibility and verification stay exactly as they were until a
    human applies the changes.

    Args:
        db: Database.
        opp_id: Opportunity id.
        new_record: Newly extracted record.
        new_eligibility: Eligibility dict computed from ``new_record``.
        changes: Differences from :func:`changes.compare_records`.
        source_url: Checked URL.
        content_hash: Hash of the page text just read.
        sources: Updated sources list (with new ``last_checked_at``/``content_hash``).

    Returns:
        The new snapshot id, or None when nothing differed.
    """
    snap_id = None
    with db.transaction() as conn:
        if changes:
            snap_id = _snapshot(conn, opp_id, "source check (not yet applied)", new_record, new_eligibility, source_url, content_hash)
            for ch in changes:
                conn.execute(
                    "INSERT INTO change_log (opportunity_id, detected_at, field, old_value, new_value, source_url, snapshot_id, applied, needs_review) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)",
                    (opp_id, iso(utc_now()), ch.field, ch.old, ch.new, source_url, snap_id),
                )
            conn.execute("UPDATE opportunities SET needs_review = 1, updated_at = ? WHERE id = ?", (iso(utc_now()), opp_id))
        conn.execute("UPDATE opportunities SET sources_json = ? WHERE id = ?", (dumps(sources), opp_id))
    return snap_id


def get_snapshot(db: Database, snapshot_id: int) -> Optional[dict]:
    """Return a snapshot with parsed record/eligibility, or None."""
    rows = db.query("SELECT * FROM opportunity_snapshots WHERE id = ?", (snapshot_id,))
    if not rows:
        return None
    row = rows[0]
    row["record"] = loads(row.pop("record_json"), {})
    row["eligibility"] = loads(row.pop("eligibility_json"), {})
    return row


def list_snapshots(db: Database, opp_id: int) -> list:
    """Return an opportunity's snapshots, newest first."""
    out = []
    for row in db.query("SELECT * FROM opportunity_snapshots WHERE opportunity_id = ? ORDER BY id DESC", (opp_id,)):
        row["record"] = loads(row.pop("record_json"), {})
        row["eligibility"] = loads(row.pop("eligibility_json"), {})
        out.append(row)
    return out


def list_changes(db: Database, opp_id: Optional[int] = None, only_unreviewed: bool = False) -> list:
    """Return change-log rows (newest first), joined with the opportunity name."""
    sql = ("SELECT c.*, o.name AS opportunity_name FROM change_log c "
           "JOIN opportunities o ON o.id = c.opportunity_id WHERE 1=1")
    params: list = []
    if opp_id is not None:
        sql += " AND c.opportunity_id = ?"
        params.append(opp_id)
    if only_unreviewed:
        sql += " AND c.reviewed_at = ''"
    sql += " ORDER BY c.id DESC"
    return db.query(sql, tuple(params))


def mark_changes_applied(db: Database, snapshot_id: int) -> None:
    """Mark every change tied to ``snapshot_id`` as applied (record updated by a human action)."""
    with db.transaction() as conn:
        conn.execute("UPDATE change_log SET applied = 1 WHERE snapshot_id = ?", (snapshot_id,))


def review_change(db: Database, change_id: int, note: str = "") -> None:
    """Mark a change as reviewed by the user and clear the record's review flag if none remain."""
    with db.transaction() as conn:
        row = conn.execute("SELECT opportunity_id FROM change_log WHERE id = ?", (change_id,)).fetchone()
        if row is None:
            return
        conn.execute("UPDATE change_log SET reviewed_at = ?, review_note = ?, needs_review = 0 WHERE id = ?",
                     (iso(utc_now()), note, change_id))
        left = conn.execute("SELECT COUNT(*) FROM change_log WHERE opportunity_id = ? AND reviewed_at = ''",
                            (row["opportunity_id"],)).fetchone()[0]
        if left == 0:
            conn.execute("UPDATE opportunities SET needs_review = 0 WHERE id = ?", (row["opportunity_id"],))


def set_verified(db: Database, opp_id: int, verified: bool) -> None:
    """Mark a record as human-verified (or remove verification)."""
    with db.transaction() as conn:
        if verified:
            conn.execute("UPDATE opportunities SET verified_at = ?, research_status = 'Verified', needs_review = 0, updated_at = ? WHERE id = ?",
                         (iso(utc_now()), iso(utc_now()), opp_id))
        else:
            conn.execute("UPDATE opportunities SET verified_at = '', research_status = 'Researched', updated_at = ? WHERE id = ?",
                         (iso(utc_now()), opp_id))


def set_status(db: Database, opp_id: int, status: str) -> None:
    """Set the research status (must be one of :data:`RESEARCH_STATUSES`)."""
    if status not in RESEARCH_STATUSES:
        raise ValueError("Unknown research status.")
    with db.transaction() as conn:
        conn.execute("UPDATE opportunities SET research_status = ?, updated_at = ? WHERE id = ?", (status, iso(utc_now()), opp_id))


def set_notes(db: Database, opp_id: int, notes: str) -> None:
    """Save the user's free-text notes (limited to 5000 characters)."""
    with db.transaction() as conn:
        conn.execute("UPDATE opportunities SET notes = ?, updated_at = ? WHERE id = ?", (notes[:5000], iso(utc_now()), opp_id))


def save_review_state(db: Database, opp_id: int, overrides: dict, eligibility: dict, match: dict,
                      source_type: Optional[str] = None, source_domain: Optional[str] = None) -> None:
    """Store manual criterion overrides together with the recomputed eligibility and match.

    Args:
        db: Database.
        opp_id: Opportunity id.
        overrides: ``{criterion_key: {status, note, at}}``.
        eligibility: Recomputed eligibility dict.
        match: Recomputed match dict.
        source_type: Optional corrected source type.
        source_domain: Domain stored with the source type.
    """
    with db.transaction() as conn:
        conn.execute(
            "UPDATE opportunities SET overrides_json = ?, eligibility_json = ?, overall_eligibility = ?, match_json = ?, match_score = ?, updated_at = ? WHERE id = ?",
            (dumps(overrides), dumps(eligibility), eligibility.get("overall", "UNCLEAR"), dumps(match), float(match.get("total", 0)), iso(utc_now()), opp_id),
        )
        if source_type:
            conn.execute("UPDATE opportunities SET source_type = ? WHERE id = ?", (source_type, opp_id))


def refresh_scores(db: Database, opp_id: int, eligibility: dict, match: dict) -> None:
    """Store a recomputed eligibility/match (e.g. after Settings changed) without touching anything else."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE opportunities SET eligibility_json = ?, overall_eligibility = ?, match_json = ?, match_score = ? WHERE id = ?",
            (dumps(eligibility), eligibility.get("overall", "UNCLEAR"), dumps(match), float(match.get("total", 0)), opp_id),
        )


def upsert_discoveries(db: Database, results: list) -> int:
    """Remember search hits as leads (status 'new'); existing URLs are left untouched.

    Args:
        db: Database.
        results: ``discovery.SearchResult`` objects.

    Returns:
        Number of newly stored leads.
    """
    added = 0
    with db.transaction() as conn:
        for r in results:
            if not r.normalized_url:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO discoveries (url, normalized_url, title, snippet, query, source_type, source_domain, found_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')",
                (r.url, r.normalized_url, r.title, r.snippet, r.query, r.source_type, r.domain, iso(utc_now())),
            )
            added += cur.rowcount
    return added


def list_discoveries(db: Database, status: Optional[str] = None) -> list:
    """Return stored search leads (newest first), optionally filtered by status."""
    if status:
        return db.query("SELECT * FROM discoveries WHERE status = ? ORDER BY id DESC", (status,))
    return db.query("SELECT * FROM discoveries ORDER BY id DESC")


def set_discovery_status(db: Database, discovery_id: int, status: str) -> None:
    """Set a lead's status: ``new``, ``researched`` or ``dismissed``."""
    if status not in ("new", "researched", "dismissed"):
        raise ValueError("Unknown discovery status.")
    with db.transaction() as conn:
        conn.execute("UPDATE discoveries SET status = ? WHERE id = ?", (status, discovery_id))
