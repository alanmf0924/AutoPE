"""Register the user-confirmed isolated Dify v8 as a new AutoPE candidate.

Idempotent for the exact published workflow and prompt hash. Does not run evaluation.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "autope.db"
GRAPH = ROOT / "backups" / "dify-candidate-draft-after-case4-case10-20261014.json"
OLD_TARGET_ID = "target_f78068dc1c05"
OLD_CANDIDATE_ID = "candidate_09fc024ce5e7"
NEW_WORKFLOW_ID = "899573b0-21c2-41af-a2f1-3975d5c9b350"
NEW_VERSION = "dify-chatflow@isolated-candidate-fix-v3-20261014"
API = "http://127.0.0.1:8010"


def post(client: httpx.Client, path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


def main() -> None:
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    published_prompt = next(
        node["data"]["prompt_template"][0]["text"]
        for node in graph["nodes"] if node["id"] == "1791797581802"
    )
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        old_target = db.execute("SELECT * FROM dify_targets WHERE id=?", (OLD_TARGET_ID,)).fetchone()
        old_candidate = db.execute("SELECT * FROM prompt_candidates WHERE id=?", (OLD_CANDIDATE_ID,)).fetchone()
        existing_target = db.execute("SELECT * FROM dify_targets WHERE published_workflow_id=?", (NEW_WORKFLOW_ID,)).fetchone()
        if old_target is None or old_candidate is None:
            raise SystemExit("Old version evidence is missing; no write made.")
        prompt_hash = hashlib.sha256(published_prompt.encode("utf-8")).hexdigest()
        if published_prompt != old_candidate["prompt_block"] or prompt_hash != old_candidate["prompt_sha256"]:
            raise SystemExit("Published prompt differs from the prior candidate; no write made.")
        if old_target["application_id"] != "033045a8-2cec-4069-8d13-c7b6560f41ec":
            raise SystemExit("Candidate application changed; no write made.")
        with httpx.Client(base_url=API, timeout=20) as client:
            if existing_target is None:
                target = post(client, "/api/targets", {
                    "name": "护肤选购助手 · 隔离候选证据门禁第 8 版 20261014",
                    "base_url": old_target["base_url"],
                    "runtime_path": old_target["runtime_path"],
                    "target_kind": old_target["target_kind"],
                    "secret_ref": old_target["secret_ref"],
                    "application_id": old_target["application_id"],
                    "input_mapping": json.loads(old_target["input_mapping"]),
                    "dify_user": old_target["dify_user"],
                    "version_fingerprint": NEW_VERSION,
                    "published_workflow_id": NEW_WORKFLOW_ID,
                })
            else:
                if existing_target["version_fingerprint"] != NEW_VERSION:
                    raise SystemExit("Version fingerprint collision; no further write made.")
                target = {"id": existing_target["id"]}
            with sqlite3.connect(DB) as fresh:
                fresh.row_factory = sqlite3.Row
                existing_candidate = fresh.execute(
                    "SELECT * FROM prompt_candidates WHERE bound_target_id=?", (target["id"],)
                ).fetchone()
            if existing_candidate is None:
                candidate = post(client, "/api/candidates", {
                    "name": "隔离候选 · 第 8 版商品证据门禁 20261014",
                    "base_version_fingerprint": old_candidate["base_version_fingerprint"],
                    "optimizable_prompt_block": published_prompt,
                    "bound_target_id": target["id"],
                })
            else:
                if existing_candidate["prompt_sha256"] != prompt_hash:
                    raise SystemExit("Existing candidate prompt mismatch; no attestation made.")
                candidate = {"id": existing_candidate["id"]}
            detail = client.get(f"/api/candidates/{candidate['id']}")
            detail.raise_for_status()
            if not detail.json().get("binding_attestation"):
                post(client, f"/api/candidates/{candidate['id']}/binding-attestation", {
                    "published_workflow_id": NEW_WORKFLOW_ID,
                    "published_prompt_block": published_prompt,
                    "review_note": "用户在本轮对话明确确认隔离 Dify 第 8 版与同 SHA-256 Prompt 的人工版本绑定；仅用于评测，不构成生产发布审批。",
                    "human_confirmed": True,
                })
            print(json.dumps({
                "target_id": target["id"],
                "candidate_id": candidate["id"],
                "published_workflow_id": NEW_WORKFLOW_ID,
                "prompt_sha256": prompt_hash,
            }, ensure_ascii=False))


if __name__ == "__main__":
    main()
