"""Application context shared by all UI pages (configuration, database, applicant settings)."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from ..config import Config, load_config, validate_config
from ..database import Database
from ..settings import load_profile, load_weights
from ..utils import register_secrets, setup_logging


@dataclass
class AppContext:
    """Everything a page needs to do its work.

    Attributes:
        cfg: Immutable configuration (no secrets are ever displayed).
        db: Migrated database.
        profile: Current applicant profile, loaded fresh on every rerun.
        weights: Current matching weights, loaded fresh on every rerun.
        config_issues: Warnings/errors from configuration validation.
    """

    cfg: Config
    db: Database
    profile: dict
    weights: dict
    config_issues: list


@st.cache_resource(show_spinner=False)
def _boot():
    """Create configuration, logging and the database once per server process."""
    cfg = load_config()
    register_secrets(cfg.secrets())
    setup_logging(cfg.log_level)
    issues = validate_config(cfg)
    db = Database(cfg.db_path)
    return cfg, db, issues


def get_context() -> AppContext:
    """Return the :class:`AppContext`, reading current settings from SQLite."""
    cfg, db, issues = _boot()
    return AppContext(cfg, db, load_profile(db), load_weights(db), issues)
