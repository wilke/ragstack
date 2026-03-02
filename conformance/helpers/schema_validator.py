"""Utilities for loading and validating JSON schemas."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema


def load_schemas(contracts_dir: str | Path) -> dict[str, dict]:
    """Load all JSON schemas from *contracts_dir*/schemas/.

    Returns a mapping of schema name (file stem, e.g. ``"query_response"``)
    to parsed schema dict.
    """
    schemas_dir = Path(contracts_dir) / "schemas"
    result: dict[str, dict] = {}
    if schemas_dir.is_dir():
        for schema_file in sorted(schemas_dir.glob("*.json")):
            with open(schema_file, encoding="utf-8") as fh:
                result[schema_file.stem] = json.load(fh)
    return result


def validate_response(response_data: dict | list, schema: dict) -> None:
    """Validate *response_data* against *schema*.

    Raises ``jsonschema.ValidationError`` when the data does not conform.
    """
    jsonschema.validate(instance=response_data, schema=schema)
