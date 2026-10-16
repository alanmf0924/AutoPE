"""Run exactly two authorized smoke checks against one pinned isolated Dify version.

This does not mutate AutoPE data or approve a candidate for production release.
Run only with the explicit --execute-two-calls flag after checking the call budget.
"""

import argparse
import json
from types import SimpleNamespace

from sqlalchemy import select

from app.db import SessionLocal
from app.dify_runtime import DifyRuntimeClient, DifyRuntimeError, configured_secret_resolver
from app.evaluation_worker import build_dify_inputs, extract_dify_text
from app.models import DatasetCaseModel, DifyTargetModel


DATASET_ID = "dataset_16353b53a69d"
TARGET_ID = "target_f78068dc1c05"
ISOLATED_APP_ID = "033045a8-2cec-4069-8d13-c7b6560f41ec"
WORKFLOW_ID = "899573b0-21c2-41af-a2f1-3975d5c9b350"
POSITIONS = (4, 10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-two-calls", action="store_true")
    args = parser.parse_args()
    if not args.execute_two_calls:
        parser.error("Explicit --execute-two-calls is required; this invokes paid Dify Runtime.")

    with SessionLocal() as session:
        stored = session.get(DifyTargetModel, TARGET_ID)
        cases = list(session.scalars(select(DatasetCaseModel).where(
            DatasetCaseModel.dataset_id == DATASET_ID,
            DatasetCaseModel.position.in_(POSITIONS),
        ).order_by(DatasetCaseModel.position)))
        if stored is None or stored.application_id != ISOLATED_APP_ID or len(cases) != 2:
            raise SystemExit("Preflight failed: target or frozen cases changed. No calls made.")
        if stored.published_workflow_id == WORKFLOW_ID:
            raise SystemExit("Unexpected stored target state: use a new versioned target. No calls made.")
        target = SimpleNamespace(
            base_url=stored.base_url,
            runtime_path=stored.runtime_path,
            target_kind=stored.target_kind,
            secret_ref=stored.secret_ref,
            published_workflow_id=WORKFLOW_ID,
            input_mapping=stored.input_mapping,
            dify_user=stored.dify_user,
        )
        prepared = [(case.position, case.prompt, build_dify_inputs(target, case)) for case in cases]

    runtime = DifyRuntimeClient(configured_secret_resolver(), timeout_s=180)
    for position, prompt, inputs in prepared:
        try:
            execution = runtime.execute(target, inputs=inputs, query=prompt, user=target.dify_user)
        except DifyRuntimeError as error:
            print(json.dumps({"position": position, "error_code": error.code, "error": error.safe_message}, ensure_ascii=False), flush=True)
            break  # Do not consume another call after an uncertain result.
        evidence = execution.evidence
        print(json.dumps({
            "position": position,
            "workflow_id": evidence.get("workflow_id"),
            "workflow_run_id": evidence.get("workflow_run_id"),
            "latency_ms": execution.latency_ms,
            "answer": extract_dify_text(execution.payload),
            "nodes": evidence.get("nodes", []),
        }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
