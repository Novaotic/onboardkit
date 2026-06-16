import json
from pathlib import Path

import jsonschema
import pytest

APP_DIR = Path(__file__).resolve().parent.parent

@pytest.fixture
def schema():
    return json.loads((APP_DIR / "config.schema.json").read_text(encoding="utf-8"))

def test_schema_validates_config_example(schema):
    config = json.loads((APP_DIR / "config.example.json").read_text(encoding="utf-8"))
    jsonschema.validate(config, schema)

def test_branding_requires_app_name(schema):
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        jsonschema.validate({"branding": {}}, schema)

