"""Shared identity configuration for Computer's single managed gateway model."""

from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_MANAGED_MODEL = {
    "id": "cptr",
    "name": "Open WebUI Computer",
    "owner": "cptr",
}


def normalize_managed_model(value: Mapping[str, object] | None) -> dict[str, str]:
    """Return a complete managed-model identity using safe public defaults."""
    source = value or {}
    return {
        key: str(source.get(key) or default).strip() or default
        for key, default in DEFAULT_MANAGED_MODEL.items()
    }


def managed_model_from_environment() -> dict[str, str]:
    """Read installer-provided model identity without exposing branding in code."""
    return normalize_managed_model(
        {
            "id": os.environ.get("CPTR_MANAGED_MODEL_ID"),
            "name": os.environ.get("CPTR_MANAGED_MODEL_NAME"),
            "owner": os.environ.get("CPTR_MANAGED_MODEL_OWNER"),
        }
    )
