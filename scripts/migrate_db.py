"""Migrate a scholarship database to the Version 3 schema without losing data.

Usage:
    python scripts/migrate_db.py                      # migrates the database named in .env (default data/scholarships_v3.db)
    python scripts/migrate_db.py path/to/old.db       # migrates a specific file (e.g. an old Version 2 database)

Before changing an existing database a backup copy "<name>.bak-<timestamp>" is written next to it. Old tables whose
names clash with Version 3 are renamed to legacy_<name>_v2 (never dropped) and their rows are imported best-effort,
marked "Imported (V2)" and unverified. The app also does all of this automatically on startup.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scholarship_agent.config import load_config  # noqa: E402
from scholarship_agent.migrations import run_migrations  # noqa: E402


def main(argv: list) -> int:
    """Run migrations on the chosen database file.

    Args:
        argv: Command-line arguments after the script name (optional database path).

    Returns:
        Process exit code (0 on success).
    """
    path = Path(argv[0]).expanduser() if argv else load_config().db_path
    if argv and not path.exists():
        print(f"File not found: {path}")
        return 1
    applied = run_migrations(path)
    print(f"Database: {path}")
    print("Applied: " + (", ".join(f"{v} ({n})" for v, n in applied) if applied else "nothing (already up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
