"""Versioned SQLite migrations that preserve existing data.

* Each migration runs inside a transaction and is recorded in ``schema_migrations``.
* Before the first pending migration touches an existing database file, a copy is
  saved next to it as ``<name>.bak-<timestamp>``.
* If a database from a previous version already has tables whose names clash with the
  Version 3 schema (for example an old ``opportunities`` table), they are renamed to
  ``legacy_<name>_v2`` - never dropped - and their rows are imported best-effort into
  the new table, marked "Imported (V2)" and unverified.

Note: the exact Version 2 schema was not available when this was written, so the
legacy import maps *commonly named* columns only. It is tested against a simulated
legacy table, not against a real Version 2 file.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .utils import dumps, iso, normalize_url, utc_now

logger = logging.getLogger("scholarship_agent.migrations")
V3_TABLES = ["opportunities", "opportunity_snapshots", "change_log", "app_settings", "discoveries"]


@dataclass(frozen=True)
class Migration:
    """One schema step.

    Attributes:
        version: Increasing integer.
        name: Short description.
        apply: Function taking an open connection (inside a transaction).
    """

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _tables(conn: sqlite3.Connection) -> set:
    """Return the set of user table names in the database."""
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set:
    """Return the column names of ``table``."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _exec_script(conn: sqlite3.Connection, script: str) -> None:
    """Run several ``;``-separated statements without ``executescript``.

    ``executescript`` silently commits the open transaction, which would break the
    all-or-nothing behaviour of a migration, so statements are run one by one.
    """
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _migration_1(conn: sqlite3.Connection) -> None:
    """Create the Version 3 core schema, rescuing any clashing legacy tables first."""
    existing = _tables(conn)
    legacy_opportunities = False
    for table in V3_TABLES:
        if table in existing:
            if table == "opportunities" and "normalized_url" in _columns(conn, table):
                continue  # already a V3 table
            conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}_v2")
            legacy_opportunities = legacy_opportunities or table == "opportunities"
            logger.info("Renamed legacy table %s to legacy_%s_v2", table, table)

    _exec_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            degree_level TEXT NOT NULL DEFAULT 'unknown',
            funding_type TEXT NOT NULL DEFAULT 'unknown',
            subject_areas TEXT NOT NULL DEFAULT '[]',
            eligible_programmes TEXT NOT NULL DEFAULT '[]',
            nepal_eligibility TEXT NOT NULL DEFAULT 'not_stated',
            overall_eligibility TEXT NOT NULL DEFAULT 'UNCLEAR',
            deadline_date TEXT NOT NULL DEFAULT '',
            deadline_original TEXT NOT NULL DEFAULT '',
            deadline_timezone TEXT NOT NULL DEFAULT '',
            application_cycle TEXT NOT NULL DEFAULT '',
            official_url TEXT NOT NULL DEFAULT '',
            normalized_url TEXT NOT NULL DEFAULT '',
            canonical_url TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'unverified',
            source_domain TEXT NOT NULL DEFAULT '',
            extraction_method TEXT NOT NULL DEFAULT '',
            extraction_model TEXT NOT NULL DEFAULT '',
            ai_verified INTEGER NOT NULL DEFAULT 0,
            retrieved_at TEXT NOT NULL DEFAULT '',
            verified_at TEXT NOT NULL DEFAULT '',
            research_status TEXT NOT NULL DEFAULT 'Researched',
            needs_review INTEGER NOT NULL DEFAULT 0,
            match_score REAL NOT NULL DEFAULT 0,
            record_json TEXT NOT NULL DEFAULT '{}',
            eligibility_json TEXT NOT NULL DEFAULT '{}',
            match_json TEXT NOT NULL DEFAULT '{}',
            conflicts_json TEXT NOT NULL DEFAULT '[]',
            sources_json TEXT NOT NULL DEFAULT '[]',
            overrides_json TEXT NOT NULL DEFAULT '{}',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_opportunities_normalized_url
            ON opportunities(normalized_url) WHERE normalized_url <> '';
        CREATE INDEX IF NOT EXISTS ix_opportunities_country ON opportunities(country);
        CREATE INDEX IF NOT EXISTS ix_opportunities_deadline ON opportunities(deadline_date);
        CREATE TABLE IF NOT EXISTS opportunity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
            taken_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            record_json TEXT NOT NULL DEFAULT '{}',
            eligibility_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS ix_snapshots_opp ON opportunity_snapshots(opportunity_id);
        CREATE TABLE IF NOT EXISTS change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
            detected_at TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT NOT NULL DEFAULT '',
            new_value TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            snapshot_id INTEGER,
            applied INTEGER NOT NULL DEFAULT 0,
            needs_review INTEGER NOT NULL DEFAULT 1,
            reviewed_at TEXT NOT NULL DEFAULT '',
            review_note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_change_opp ON change_log(opportunity_id);
        """
    )
    if legacy_opportunities:
        _import_legacy_opportunities(conn)


_LEGACY_MAP = {
    "name": ["name", "title", "scholarship_name", "scholarship", "program", "programme"],
    "provider": ["provider", "university", "institution", "organization", "organisation", "sponsor"],
    "country": ["country", "location"],
    "official_url": ["official_url", "url", "link", "website", "source_url", "application_url"],
    "deadline_original": ["deadline", "application_deadline", "due_date"],
    "funding": ["funding", "funding_type", "coverage"],
    "notes": ["notes", "note", "comments", "description", "summary"],
}


def _import_legacy_opportunities(conn: sqlite3.Connection) -> int:
    """Copy rows from ``legacy_opportunities_v2`` into the new table, best-effort.

    Only values that already exist in the old rows are copied; nothing is invented.
    Imported rows are unverified, have UNCLEAR eligibility and an explanatory note.

    Returns:
        Number of rows imported.
    """
    cols = _columns(conn, "legacy_opportunities_v2")
    pick = {}
    for target, candidates in _LEGACY_MAP.items():
        for c in candidates:
            if c in cols:
                pick[target] = c
                break
    now = iso(utc_now())
    imported = 0
    for row in conn.execute("SELECT * FROM legacy_opportunities_v2").fetchall():
        data = {t: str(row[c]).strip() if row[c] is not None else "" for t, c in pick.items()}
        legacy_id = row[0]
        record = {
            "name": data.get("name", ""), "provider": data.get("provider", ""), "country": data.get("country", ""),
            "application_deadline": data.get("deadline_original", ""),
            "tuition_coverage": data.get("funding", ""),
            "uncertainties": ["Imported from Version 2. None of these values has been re-verified against the official source."],
        }
        url = data.get("official_url", "")
        norm = normalize_url(url) if url.lower().startswith(("http://", "https://")) else ""
        if norm and conn.execute("SELECT 1 FROM opportunities WHERE normalized_url=?", (norm,)).fetchone():
            norm = ""  # avoid breaking the unique index; keep the row, mark URL only in official_url
        conn.execute(
            """INSERT INTO opportunities (name, provider, country, official_url, normalized_url, deadline_original,
               research_status, extraction_method, record_json, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'Imported (V2)', 'legacy_v2_import', ?, ?, ?, ?)""",
            (data.get("name", ""), data.get("provider", ""), data.get("country", ""), url, norm,
             data.get("deadline_original", ""), dumps(record),
             (data.get("notes", "") + f"\n[Imported from Version 2 row id {legacy_id}]").strip(), now, now),
        )
        imported += 1
    logger.info("Imported %d legacy Version 2 rows", imported)
    return imported


def _migration_2(conn: sqlite3.Connection) -> None:
    """Add the discovery log (search hits are leads, not saved opportunities)."""
    _exec_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS discoveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            snippet TEXT NOT NULL DEFAULT '',
            query TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'unverified',
            source_domain TEXT NOT NULL DEFAULT '',
            found_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_discoveries_url ON discoveries(normalized_url);
        """
    )


MIGRATIONS = [
    Migration(1, "initial_v3_schema_and_legacy_rescue", _migration_1),
    Migration(2, "discovery_log", _migration_2),
]


def run_migrations(db_path: Path, migrations: list = None, backup: bool = True) -> list:
    """Apply all pending migrations to the database at ``db_path``.

    Args:
        db_path: SQLite file path (created if missing).
        migrations: Migration list (defaults to :data:`MIGRATIONS`); injectable for tests.
        backup: Copy an existing non-empty database file before the first change.

    Returns:
        List of ``(version, name)`` tuples that were applied (empty if up to date).

    Raises:
        sqlite3.Error: If a migration fails; that migration is rolled back and earlier
            ones stay applied.
    """
    migrations = migrations if migrations is not None else MIGRATIONS
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    applied: list = []
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        pending = [m for m in sorted(migrations, key=lambda m: m.version) if m.version not in done]
        if pending and backup and db_path.exists() and db_path.stat().st_size > 0 and (_tables(conn) - {"schema_migrations"}):
            stamp = utc_now().strftime("%Y%m%d%H%M%S")
            target = db_path.with_name(f"{db_path.name}.bak-{stamp}")
            conn.commit()
            shutil.copy2(db_path, target)
            logger.info("Backed up database to %s", target.name)
        for m in pending:
            conn.execute("BEGIN")
            try:
                m.apply(conn)
                conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                             (m.version, m.name, iso(utc_now())))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                logger.error("Migration %s (%s) failed and was rolled back", m.version, m.name)
                raise
            applied.append((m.version, m.name))
    finally:
        conn.close()
    return applied
