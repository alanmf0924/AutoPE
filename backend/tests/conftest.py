"""Force every test module onto an isolated in-memory database before app imports."""

from __future__ import annotations

import os

os.environ["AUTOPE_ENVIRONMENT"] = "test"
os.environ["AUTOPE_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["AUTOPE_AUTH_DISABLED"] = "true"
os.environ["AUTOPE_SEED_DEMO_DATA"] = "true"
os.environ["AUTOPE_LOCAL_CREDENTIAL_ENCRYPTION_KEY"] = (
    "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE="
)

from app.db import engine

if engine.dialect.name != "sqlite" or engine.url.database != ":memory:":
    raise RuntimeError("Tests must not use the developer or deployed database.")
