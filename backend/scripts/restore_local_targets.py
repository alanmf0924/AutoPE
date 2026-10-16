"""Re-register local Dify targets from a verified AutoPE backup through the API.

This does not restore historical runs, reports, reviews, candidates, or Dify data.
Run only after an intentional local reset; the API target list must be empty.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.credential_vault import decrypt_local_api_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    backup = args.backup.resolve(strict=True)
    with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        rows = database.execute(
            """SELECT t.*, c.encrypted_api_key FROM dify_targets AS t
               JOIN dify_credentials AS c ON c.secret_ref = t.secret_ref
               ORDER BY t.created_at, t.id"""
        ).fetchall()
    if not rows:
        raise RuntimeError("Backup has no locally credentialed Dify targets to restore.")
    if any(urlsplit(row["base_url"]).hostname not in {"127.0.0.1", "localhost", "::1"} for row in rows):
        raise RuntimeError("Refusing to send stored credentials to a non-local Dify target.")
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=15, trust_env=False) as client:
        existing = client.get("/api/targets")
        existing.raise_for_status()
        if existing.json():
            raise RuntimeError("AutoPE targets are not empty; refusing to duplicate restored targets.")
        for row in rows:
            response = client.post("/api/targets", json={
                "name": row["name"].replace("DSPy候选草稿", "隔离候选应用"),
                "base_url": row["base_url"],
                "runtime_path": row["runtime_path"],
                "target_kind": row["target_kind"],
                "application_id": row["application_id"],
                "input_mapping": json.loads(row["input_mapping"]),
                "dify_user": row["dify_user"],
                "api_key": decrypt_local_api_key(row["encrypted_api_key"]),
                "version_fingerprint": row["version_fingerprint"],
            })
            response.raise_for_status()
            created = response.json()
            print(json.dumps({"old_target_id": row["id"], "new_target_id": created["id"],
                              "name": created["name"], "version_fingerprint": created["version_fingerprint"]},
                             ensure_ascii=False))


if __name__ == "__main__":
    main()
