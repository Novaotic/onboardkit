import json
from pathlib import Path
import pytest

from preset_validation import validate_presets

APP_DIR = Path(__file__).parent.parent

MINIMAL_CONFIG = {
    "offices": ["Headquarters", "Remote"],
    "option_groups": {
        "hardware": [{"id": "laptop", "label": "Laptop"}],
        "software": [{"id": "microsoft_365", "label": "Microsoft 365"}],
        "portals": [{"id": "vendor_portal_a", "label": "Vendor Portal A"}],
        "mailboxes": [{"id": "engineering_team", "label": "Engineering Team"}],
    },

}

VALID_PRESET = {
    "Analyst": {
        "hardware": ["laptop"],
        "software": ["microsoft_365"],
        "portals": ["vendor_portal_a"],
        "mailboxes": ["engineering_team"],
        "location_email_groups": {"Headquarters": ["team@company.com"]},
    },
}

def test_validate_preset_returns_no_errors():
    assert validate_presets(VALID_PRESET, MINIMAL_CONFIG) == []

@pytest.mark.parametrize(
    ("field", "bad_id", "snippet"),
    [
        ("hardware", "desktop", "Unknown hardware ID"),
        ("software", "crm_suite", "Unknown software ID"),
        ("portals", "vendor_portal_b", "Unknown portal ID"),
        ("mailboxes", "fake_mailbox", "Unknown mailbox ID"),
    ],
)
def test_unknown_id_errors(field, bad_id, snippet):
    preset = {"Analyst": {**VALID_PRESET["Analyst"], field: [bad_id]}}
    errors = validate_presets(preset, MINIMAL_CONFIG)
    assert len(errors) == 1
    assert snippet in errors[0]
    assert bad_id in errors[0]

def test_unknown_office_in_location_email_groups():
    preset = {
        "Analyst": {
            **VALID_PRESET["Analyst"],
            "location_email_groups": {"Unknown Office": ["team@company.com"]},
        }
    }
    errors = validate_presets(preset, MINIMAL_CONFIG)
    assert len(errors) == 1
    assert "Unknown Office" in errors[0]

def test_multiple_errors_across_fields():
    preset = {
        "Analyst": {
            "hardware": ["desktop"],
            "software": ["crm_suite"],
            "portals": [],
            "mailboxes": [],
        }
    }
    errors = validate_presets(preset, MINIMAL_CONFIG)
    assert len(errors) == 2

def test_example_files_pass_validation():
    config = json.loads((APP_DIR / "config.example.json").read_text(encoding="utf-8"))
    presets = json.loads((APP_DIR / "presets.example.json").read_text(encoding="utf-8"))
    assert validate_presets(presets, config) == []