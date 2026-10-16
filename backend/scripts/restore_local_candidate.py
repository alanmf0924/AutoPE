"""Re-register the saved candidate proposal after a local AutoPE reset.

No model or Dify runtime is called. The prompt is sent only to local AutoPE API.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    backup = args.backup.resolve(strict=True)
    with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        rows = database.execute(
            "SELECT name, base_version_fingerprint, prompt_block, prompt_sha256 FROM prompt_candidates ORDER BY created_at DESC"
        ).fetchall()
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one saved candidate in the local backup.")
    candidate = rows[0]
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=15, trust_env=False) as client:
        existing = client.get("/api/candidates")
        existing.raise_for_status()
        if existing.json():
            raise RuntimeError("Candidate list is not empty; refusing duplicate import.")
        targets_response = client.get("/api/targets")
        targets_response.raise_for_status()
        targets = targets_response.json()
        matches = [item for item in targets if item["version_fingerprint"] != candidate["base_version_fingerprint"]]
        if len(matches) != 1:
            raise RuntimeError("Expected exactly one isolated candidate target in AutoPE.")
        created = client.post("/api/candidates", json={
            "name": candidate["name"],
            "base_version_fingerprint": candidate["base_version_fingerprint"],
            "optimizable_prompt_block": candidate["prompt_block"],
            "bound_target_id": matches[0]["id"],
        })
        created.raise_for_status()
        result = created.json()
        if result["prompt_sha256"] != candidate["prompt_sha256"]:
            raise RuntimeError("Restored candidate hash does not match the backup.")
        print(json.dumps({"candidate_id": result["id"], "prompt_sha256": result["prompt_sha256"],
                          "bound_target_id": result["bound_target_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
