"""Cross-platform path resolution checks."""

from __future__ import annotations

from pathlib import Path

from paths import (
    APP_DIR,
    CONFIG_EXAMPLE,
    CONFIG_SCHEMA,
    DATA_DIR,
    DB_FILE,
    ENV_FILE,
    PRESETS_EXAMPLE,
    STATIC_DIR,
    TEMPLATES_DIR,
)


def test_app_dir_is_absolute():
    assert APP_DIR.is_absolute()
    assert APP_DIR.name == "onboardkit" or (APP_DIR / "main.py").exists()


def test_core_paths_exist():
    assert STATIC_DIR.is_dir()
    assert TEMPLATES_DIR.is_dir()
    assert CONFIG_EXAMPLE.is_file()
    assert CONFIG_SCHEMA.is_file()
    assert PRESETS_EXAMPLE.is_file()
    assert (TEMPLATES_DIR / "login.html").is_file()
    assert (STATIC_DIR / "style.css").is_file()


def test_paths_join_with_pathlib_not_slash_strings():
    """Guards against regressions to hardcoded Unix-only path strings."""
    joined = APP_DIR / "static" / "style.css"
    assert joined == STATIC_DIR / "style.css"
    assert isinstance(joined, Path)
    assert joined.exists()


def test_sqlite_paths_under_app_data_dir():
    assert DATA_DIR == APP_DIR / "data"
    assert DB_FILE == DATA_DIR / "onboardkit.db"
    assert DB_FILE.parent == DATA_DIR


def test_data_dir_is_gitignored():
    gitignore = (APP_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in gitignore


def test_env_file_lives_beside_app(tmp_path, monkeypatch):
    """ENV_FILE is anchored to APP_DIR, independent of process CWD."""
    assert ENV_FILE.parent == APP_DIR
    monkeypatch.chdir(tmp_path)
    from paths import ENV_FILE as env_again

    assert env_again.parent == APP_DIR
