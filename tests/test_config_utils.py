"""Configuration validation, secret redaction, URL normalisation, CSV safety."""

from scholarship_agent.config import load_config, validate_config
from scholarship_agent.utils import normalize_url, redact, register_secrets, rows_to_csv, to_list


def test_missing_keys_run_with_warnings(tmp_path):
    cfg = load_config({"SCHOLARSHIP_DB_PATH": str(tmp_path / "x.db")})
    assert not cfg.has_openai and not cfg.has_search
    msgs = " ".join(i.message for i in validate_config(cfg))
    assert "OPENAI_API_KEY" in msgs and "TAVILY_API_KEY" in msgs


def test_keys_read_from_env_and_not_in_repr(tmp_path):
    cfg = load_config({"OPENAI_API_KEY": "sk-secretvalue123", "OPENAI_MODEL": "gpt-4o-mini", "SCHOLARSHIP_DB_PATH": str(tmp_path / "x.db")})
    assert cfg.has_openai and cfg.openai_model == "gpt-4o-mini"
    assert "sk-secretvalue123" not in repr(cfg)


def test_invalid_numbers_fall_back_with_warning(tmp_path):
    cfg = load_config({"FETCH_TIMEOUT_SECONDS": "abc", "CLOSING_SOON_DAYS": "-3", "LOG_LEVEL": "LOUD", "SCHOLARSHIP_DB_PATH": str(tmp_path / "x.db")})
    assert cfg.fetch_timeout_seconds == 15.0 and cfg.closing_soon_days == 30 and cfg.log_level == "INFO"
    assert len(cfg.issues) == 3


def test_redact_masks_registered_and_pattern_secrets():
    register_secrets(["my-custom-secret-key"])
    assert "my-custom-secret-key" not in redact("error with my-custom-secret-key inside")
    assert "sk-abcdefgh12345" not in redact("Authorization failed for sk-abcdefgh12345")


def test_normalize_url_removes_noise():
    a = normalize_url("HTTP://www.Example.com/path/?utm_source=x&b=2&a=1#frag")
    b = normalize_url("https://example.com/path?a=1&b=2")
    assert a == b
    assert normalize_url("") == "" and normalize_url("notaurl") == ""


def test_to_list_variants():
    assert to_list(None) == [] and to_list(" a ") == ["a"] and to_list('["x","y"]') == ["x", "y"] and to_list(["", "z"]) == ["z"]


def test_csv_blocks_formula_injection():
    out = rows_to_csv([{"a": "=HYPERLINK(1)", "b": ["x", "y"]}], ["a", "b"])
    assert "'=HYPERLINK" in out and "x; y" in out
