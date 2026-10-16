from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest

from app.dify_runtime import DifyRuntimeClient, DifyRuntimeError
from app.models import DifyTargetModel
from app.repository import now_utc


class FakeResolver:
    def resolve(self, secret_ref: str) -> str:
        assert secret_ref == "sandbox/customer-service"
        return "secret-value"


def test_workflow_request_is_normalized_and_authenticated(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(
            200,
            json={"workflow_run_id": "dify-run-1", "data": {"outputs": {"answer": "ok"}}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    target = DifyTargetModel(
        id="target_1",
        name="Sandbox",
        base_url="https://dify.example",
        runtime_path="/v1/workflows/run",
        target_kind="workflow",
        secret_ref="sandbox/customer-service",
        version_fingerprint="v1",
        created_at=now_utc(),
    )

    result = DifyRuntimeClient(FakeResolver()).execute(
        target, inputs={"question": "hello"}, query=None, user="autope:run_1"
    )

    assert captured["url"] == "https://dify.example/v1/workflows/run"
    assert captured["headers"] == {
        "Authorization": "Bearer secret-value",
        "Content-Type": "application/json",
    }
    assert captured["json"] == {
        "inputs": {"question": "hello"},
        "response_mode": "blocking",
        "user": "autope:run_1",
    }
    assert result.payload["workflow_run_id"] == "dify-run-1"


def test_published_workflow_version_is_pinned(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    version_id = "36b246ff-a5f5-4065-a0e6-1d1cf5ca2f19"
    events = [
        {"event": "node_finished", "workflow_run_id": "run-1", "data": {"node_id": "tool-1", "node_type": "tool", "title": "plan_selection_request", "status": "succeeded"}},
        {"event": "workflow_finished", "workflow_run_id": "run-1", "data": {"workflow_id": version_id, "status": "succeeded"}},
        {"event": "message", "answer": "hello"},
        {"event": "message_end"},
    ]

    @contextmanager
    def fake_stream(method: str, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["json"] = kwargs["json"]
        assert method == "POST"
        body = "\n\n".join("data: " + json.dumps(event) for event in events) + "\n\n"
        yield httpx.Response(200, text=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "stream", fake_stream)
    target = DifyTargetModel(
        id="target_pinned", name="Pinned", base_url="https://dify.example",
        runtime_path="/v1/chat-messages", target_kind="chatflow",
        secret_ref="sandbox/customer-service", version_fingerprint="v1",
        published_workflow_id=version_id, created_at=now_utc(),
    )
    execution = DifyRuntimeClient(FakeResolver()).execute(
        target, inputs={}, query="hello", user="autope:run_2",
    )
    assert captured["url"] == "https://dify.example/v1/chat-messages"
    assert captured["json"] == {
        "inputs": {}, "query": "hello", "response_mode": "streaming",
        "user": "autope:run_2", "workflow_id": version_id,
    }
    assert execution.payload == {"answer": "hello"}
    assert execution.evidence["workflow_run_id"] == "run-1"
    assert execution.evidence["nodes"][0]["title"] == "plan_selection_request"

    target.target_kind = "workflow"
    target.runtime_path = "/v1/workflows/run"
    DifyRuntimeClient(FakeResolver()).execute(
        target, inputs={"question": "hello"}, query=None, user="autope:run_3",
    )
    assert captured["url"] == f"https://dify.example/v1/workflows/{version_id}/run"


def test_chatflow_stream_requires_complete_matching_version(monkeypatch) -> None:
    @contextmanager
    def fake_stream(method: str, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        events = [
            {"event": "workflow_finished", "workflow_run_id": "run-2", "data": {"workflow_id": "wrong-version", "status": "succeeded"}},
            {"event": "message", "answer": "answer"},
            {"event": "message_end"},
        ]
        body = "\n\n".join("data: " + json.dumps(event) for event in events) + "\n\n"
        yield httpx.Response(200, text=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "stream", fake_stream)
    target = DifyTargetModel(
        id="target_pinned", name="Pinned", base_url="https://dify.example",
        runtime_path="/v1/chat-messages", target_kind="chatflow",
        secret_ref="sandbox/customer-service", version_fingerprint="v1",
        published_workflow_id="36b246ff-a5f5-4065-a0e6-1d1cf5ca2f19", created_at=now_utc(),
    )
    with pytest.raises(DifyRuntimeError) as error:
        DifyRuntimeClient(FakeResolver()).execute(target, inputs={}, query="hello", user="autope:run_2")
    assert error.value.code == "version_mismatch"
