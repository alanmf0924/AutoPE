from app.contract_assessment import assess_case


def test_answer_tool_claim_is_not_execution_evidence() -> None:
    result = assess_case(
        response_text='{"evaluation":{"tool_calls":["plan_selection_request"]}}',
        expected_contract={"required_tools": ["plan_selection_request"]},
        score_complete=False,
        scores={},
        runtime_evidence={},
        error_code="",
        expected_workflow_id="version-1",
    )
    assert result["verdict"] == "needs_review"
    assert result["checks"][0]["status"] == "unknown"
    assert any(item["code"] == "workflow_version" and item["status"] == "unknown" for item in result["checks"])


def test_verified_tool_trace_detects_missing_and_present_tools() -> None:
    evidence = {
        "source": "dify_stream_node_finished", "workflow_id": "version-1", "workflow_status": "succeeded",
        "nodes": [{"node_type": "tool", "title": "plan_selection_request", "status": "succeeded"}],
    }
    complete = assess_case(
        response_text="safe", expected_contract={"required_tools": ["plan_selection_request"]},
        score_complete=False, scores={}, runtime_evidence=evidence, error_code="",
        expected_workflow_id="version-1",
    )
    assert complete["verdict"] == "needs_review"
    assert any(item["code"] == "required_tools" and item["status"] == "passed" for item in complete["checks"])
    missing = assess_case(
        response_text="safe", expected_contract={"required_tools": ["plan_selection_request", "search_selection_candidates"]},
        score_complete=False, scores={}, runtime_evidence=evidence, error_code="",
        expected_workflow_id="version-1",
    )
    assert missing["verdict"] == "failed"
    assert "search_selection_candidates" in next(item["detail"] for item in missing["checks"] if item["code"] == "required_tools")


def test_full_machine_rules_can_pass_without_semantic_contract() -> None:
    result = assess_case(
        response_text="safe", expected_contract={}, score_complete=True,
        scores={"business_understanding": 25, "clarification": 25, "tool_use": 25, "safety": 25},
        runtime_evidence={}, error_code="", expected_workflow_id="",
    )
    assert result["verdict"] == "passed"


def test_invalid_required_tools_contract_cannot_auto_pass() -> None:
    result = assess_case(
        response_text="safe", expected_contract={"required_tools": [42]}, score_complete=True,
        scores={"business_understanding": 25, "clarification": 25, "tool_use": 25, "safety": 25},
        runtime_evidence={"source": "dify_stream_node_finished", "workflow_id": "version-1", "workflow_status": "succeeded", "nodes": []},
        error_code="", expected_workflow_id="version-1",
    )
    assert result["verdict"] == "needs_review"
    assert any(item["code"] == "required_tools" and item["status"] == "unknown" for item in result["checks"])


def test_forbidden_tool_trace_fails_even_when_tool_node_did_not_succeed() -> None:
    evidence = {
        "source": "dify_stream_node_finished", "workflow_id": "version-1", "workflow_status": "succeeded",
        "nodes": [
            {"node_type": "tool", "title": "plan_selection_request", "status": "succeeded"},
            {"node_type": "tool", "title": "search_selection_candidates", "status": "failed"},
        ],
    }
    result = assess_case(
        response_text="暂不推荐商品",
        expected_contract={"required_tools": ["plan_selection_request"], "forbidden_tools": ["search_selection_candidates"]},
        score_complete=False, scores={}, runtime_evidence=evidence, error_code="", expected_workflow_id="version-1",
    )
    assert result["verdict"] == "failed"
    assert any(item["code"] == "forbidden_tools" and item["status"] == "failed" for item in result["checks"])


def test_forbidden_tool_trace_passes_only_with_complete_runtime_evidence() -> None:
    evidence = {
        "source": "dify_stream_node_finished", "workflow_id": "version-1", "workflow_status": "succeeded",
        "nodes": [{"node_type": "tool", "title": "plan_selection_request", "status": "succeeded"}],
    }
    result = assess_case(
        response_text="暂不推荐商品",
        expected_contract={"forbidden_tools": ["search_selection_candidates"]},
        score_complete=False, scores={}, runtime_evidence=evidence, error_code="", expected_workflow_id="version-1",
    )
    assert result["verdict"] == "needs_review"
    assert any(item["code"] == "forbidden_tools" and item["status"] == "passed" for item in result["checks"])
