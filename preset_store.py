import json
import shutil
from pathlib import Path

APP_DIR = Path(__file__).parent
PRESETS_FILE = APP_DIR / "presets.json"
PRESETS_EXAMPLE = APP_DIR / "presets.example.json"


def get_presets() -> dict:
    """Return all role presets from presets.json."""
    if PRESETS_FILE.exists():
        return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    return {}


def save_presets(presets: dict) -> None:
    """Persist the full presets dict to presets.json."""
    PRESETS_FILE.write_text(json.dumps(presets, indent=2), encoding="utf-8")


def init_store() -> None:
    """Seed presets.json from presets.example.json on first run."""
    if not PRESETS_FILE.exists():
        if not PRESETS_EXAMPLE.exists():
            save_presets({})
            return
        shutil.copy(PRESETS_EXAMPLE, PRESETS_FILE)
