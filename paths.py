"""Filesystem roots for OnboardKit — always resolve from this package, not CWD.

Using pathlib keeps Linux and Windows path handling consistent even when the
process is started from another working directory (services, IDE launchers).
"""

from __future__ import annotations

from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
ENV_FILE = APP_DIR / ".env"

CONFIG_FILE = APP_DIR / "config.json"
CONFIG_EXAMPLE = APP_DIR / "config.example.json"
CONFIG_SCHEMA = APP_DIR / "config.schema.json"

PRESETS_FILE = APP_DIR / "presets.json"
PRESETS_EXAMPLE = APP_DIR / "presets.example.json"
