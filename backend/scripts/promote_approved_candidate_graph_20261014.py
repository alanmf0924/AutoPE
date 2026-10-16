"""Copy the approved candidate graph into the isolated baseline Dify draft.

Run inside the local Dify API container, with stdin redirected from this file.
The default invocation is a read-only preflight; pass --apply to save the draft.
This never publishes a workflow.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys

from app import app
from extensions.ext_database import db
from models.account import Account
from models.model import App
from models.workflow import Workflow
from services.workflow_service import WorkflowService


BASELINE_APP_ID = "21f9e56c-86c8-4237-82c3-b29ead19316e"
CANDIDATE_APP_ID = "033045a8-2cec-4069-8d13-c7b6560f41ec"
APPROVED_WORKFLOW_ID = "899573b0-21c2-41af-a2f1-3975d5c9b350"
CANDIDATE_LATEST_ID = "a1d8b402-5267-4469-9f0e-5ddf04fb7b5d"
BASELINE_PUBLISHED_ID = "0b16fed0-0b26-4e55-9556-b68d9ddefc84"
BASELINE_DRAFT_ID = "6775bbd7-7a04-4833-81bd-a4fe000140bc"
OWNER_ID = "e6214e37-3efa-4c04-abad-8961f3ffeacb"


def normalized_graph(workflow: Workflow) -> dict:
    graph = copy.deepcopy(dict(workflow.graph_dict))
    graph.pop("viewport", None)
    for node in graph["nodes"]:
        node.pop("selected", None)
        node.pop("zIndex", None)
        node.get("data", {}).pop("selected", None)
    graph["nodes"].sort(key=lambda node: node["id"])
    return graph


def digest(workflow: Workflow) -> str:
    data = json.dumps(normalized_graph(workflow), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def main() -> None:
    apply = sys.argv[1:] == ["--apply"]
    if sys.argv[1:] not in ([], ["--apply"]):
        raise SystemExit("Usage: promote_approved_candidate_graph_20261014.py [--apply]")

    with app.app_context():
        session = db.session
        baseline_app = session.get(App, BASELINE_APP_ID)
        candidate_app = session.get(App, CANDIDATE_APP_ID)
        approved = session.get(Workflow, APPROVED_WORKFLOW_ID)
        candidate_latest = session.get(Workflow, CANDIDATE_LATEST_ID)
        baseline_published = session.get(Workflow, BASELINE_PUBLISHED_ID)
        baseline_draft = session.get(Workflow, BASELINE_DRAFT_ID)
        owner = session.get(Account, OWNER_ID)
        if not all((baseline_app, candidate_app, approved, candidate_latest, baseline_published, baseline_draft, owner)):
            raise SystemExit("One or more expected Dify records are missing; no changes made.")
        if baseline_app.tenant_id != candidate_app.tenant_id:
            raise SystemExit("Apps are in different tenants; no changes made.")
        if candidate_app.workflow_id != CANDIDATE_LATEST_ID or baseline_app.workflow_id != BASELINE_PUBLISHED_ID:
            raise SystemExit("A published pointer changed; no changes made.")
        if approved.app_id != CANDIDATE_APP_ID or candidate_latest.app_id != CANDIDATE_APP_ID:
            raise SystemExit("Candidate workflow belongs to another app; no changes made.")
        if baseline_published.app_id != BASELINE_APP_ID or baseline_draft.app_id != BASELINE_APP_ID:
            raise SystemExit("Baseline workflow belongs to another app; no changes made.")
        if baseline_draft.version != "draft" or approved.version == "draft":
            raise SystemExit("Unexpected draft/published version; no changes made.")
        if normalized_graph(approved) != normalized_graph(candidate_latest):
            raise SystemExit("Candidate #9 differs from approved #8; no changes made.")
        if approved.features_dict != candidate_latest.features_dict:
            raise SystemExit("Candidate #9 features differ from approved #8; no changes made.")
        if normalized_graph(baseline_draft) != normalized_graph(baseline_published):
            raise SystemExit("Baseline draft contains functional edits; no changes made.")
        owner.set_tenant_id_with_session(baseline_app.tenant_id, session=session)
        if owner.current_tenant_id != baseline_app.tenant_id:
            raise SystemExit("Owner lacks access to baseline tenant; no changes made.")

        print(f"approved_graph_sha256={digest(approved)}")
        print(f"candidate_latest_graph_sha256={digest(candidate_latest)}")
        print(f"baseline_previous_graph_sha256={digest(baseline_published)}")
        print(f"baseline_draft_id={baseline_draft.id}")
        if not apply:
            print("preflight_only=true")
            return

        graph = copy.deepcopy(dict(approved.graph_dict))
        updated = WorkflowService().sync_draft_workflow(
            app_model=baseline_app,
            graph=graph,
            features=baseline_draft.features_dict,
            unique_hash=baseline_draft.unique_hash,
            account=owner,
            environment_variables=baseline_draft.environment_variables,
            conversation_variables=baseline_draft.conversation_variables,
            session=session,
            graph_only=True,
        )
        if normalized_graph(updated) != normalized_graph(approved):
            raise SystemExit("Draft verification failed after sync.")
        print(f"updated_draft_id={updated.id}")
        print(f"updated_graph_sha256={digest(updated)}")


if __name__ == "__main__":
    main()
