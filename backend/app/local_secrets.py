from __future__ import annotations

import os
import re

from app.config import get_settings


def environment_variable_for_secret_ref(secret_ref: str) -> str:
    normalized_ref = re.sub(r"[^A-Za-z0-9]", "_", secret_ref).upper().strip("_")
    return f"AUTOPE_DEV_SECRET_{normalized_ref}"


class LocalEnvironmentSecretResolver:
    """Development-only resolver. Deployed environments require an external resolver."""

    def resolve(self, secret_ref: str) -> str:
        if get_settings().environment in {"shared_test", "production"}:
            return ""
        value = os.getenv(environment_variable_for_secret_ref(secret_ref), "").strip()
        if value.startswith("replace-with-"):
            return ""
        return value
