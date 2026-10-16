from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.credential_vault import CredentialVaultError, decrypt_local_api_key
from app.db import SessionLocal
from app.local_secrets import LocalEnvironmentSecretResolver
from app.models import DifyCredentialModel, DifyTargetModel


class SecretResolver(Protocol):
    """Resolves a server-only reference. Implement this with the approved secret manager."""

    def resolve(self, secret_ref: str) -> str: ...


class LocalDatabaseSecretResolver:
    """Reads local UI-provided credentials first, then keeps legacy env references working."""

    def resolve(self, secret_ref: str) -> str:
        with SessionLocal() as session:
            credential = self._find_credential(session, secret_ref)
        if credential is None:
            return LocalEnvironmentSecretResolver().resolve(secret_ref)
        try:
            return decrypt_local_api_key(credential.encrypted_api_key)
        except CredentialVaultError:
            return ""

    @staticmethod
    def _find_credential(session: Session, secret_ref: str) -> DifyCredentialModel | None:
        return session.get(DifyCredentialModel, secret_ref)


class MountedFileSecretResolver:
    """Reads one value per server-mounted secret file; API responses never contain it."""

    def __init__(self, secret_dir: str) -> None:
        self._secret_dir = Path(secret_dir)

    def resolve(self, secret_ref: str) -> str:
        relative_path = Path(secret_ref)
        if not secret_ref or relative_path.is_absolute() or ".." in relative_path.parts:
            return ""
        try:
            secret_dir = self._secret_dir.resolve()
            secret_file = (secret_dir / relative_path).resolve()
            if secret_dir not in secret_file.parents:
                return ""
            return secret_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""


def configured_secret_resolver(settings: Settings | None = None) -> SecretResolver:
    runtime_settings = settings or get_settings()
    if runtime_settings.environment in {"shared_test", "production"}:
        return MountedFileSecretResolver(runtime_settings.external_secret_dir)
    return LocalDatabaseSecretResolver()


class DifyRuntimeError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True)
class DifyExecution:
    payload: dict[str, Any]
    latency_ms: int
    evidence: dict[str, Any] = field(default_factory=dict)


class DifyRuntimeClient:
    def __init__(self, resolver: SecretResolver, timeout_s: float = 60.0) -> None:
        self._resolver = resolver
        self._timeout_s = timeout_s

    def execute(
        self,
        target: DifyTargetModel,
        *,
        inputs: dict[str, Any],
        query: str | None,
        user: str,
    ) -> DifyExecution:
        api_key = self._resolver.resolve(target.secret_ref)
        if not api_key:
            raise DifyRuntimeError("secret_unavailable", "Dify credential is unavailable.")
        body = self._request_body(target, inputs=inputs, query=query, user=user)
        runtime_path = target.runtime_path
        if target.published_workflow_id and target.target_kind == "workflow":
            if runtime_path != "/v1/workflows/run":
                raise DifyRuntimeError(
                    "unsupported_pinned_path",
                    "Pinned workflow versions require the standard Dify workflow runtime path.",
                )
            runtime_path = f"/v1/workflows/{target.published_workflow_id}/run"
        if target.target_kind == "chatflow":
            body["response_mode"] = "streaming"
        started_at = perf_counter()
        try:
            if target.target_kind == "chatflow":
                with httpx.stream(
                    "POST",
                    f"{target.base_url.rstrip('/')}{runtime_path}",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=self._timeout_s,
                ) as response:
                    response.raise_for_status()
                    payload, evidence = self._read_chatflow_stream(response)
                if target.published_workflow_id and evidence["workflow_id"] != target.published_workflow_id:
                    raise DifyRuntimeError("version_mismatch", "Dify executed a different published workflow version.")
                return DifyExecution(
                    payload=payload,
                    latency_ms=round((perf_counter() - started_at) * 1000),
                    evidence=evidence,
                )
            response = httpx.post(
                f"{target.base_url.rstrip('/')}{runtime_path}",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=self._timeout_s,
            )
            response.raise_for_status()
        except httpx.TimeoutException as error:
            raise DifyRuntimeError("timeout", "Dify runtime timed out.") from error
        except httpx.HTTPStatusError as error:
            raise DifyRuntimeError(
                "http_error", f"Dify runtime returned HTTP {error.response.status_code}."
            ) from error
        except httpx.HTTPError as error:
            raise DifyRuntimeError(
                "transport_error", "Dify runtime could not be reached."
            ) from error
        try:
            payload = response.json()
        except ValueError as error:
            raise DifyRuntimeError(
                "invalid_response", "Dify runtime did not return JSON."
            ) from error
        if not isinstance(payload, dict):
            raise DifyRuntimeError(
                "invalid_response", "Dify runtime returned an unexpected payload."
            )
        return DifyExecution(
            payload=payload,
            latency_ms=round((perf_counter() - started_at) * 1000),
            evidence={"source": "dify_workflow_response", "workflow_run_id": payload.get("workflow_run_id", "")},
        )

    @staticmethod
    def _read_chatflow_stream(response: httpx.Response) -> tuple[dict[str, Any], dict[str, Any]]:
        answer_parts: list[str] = []
        nodes: list[dict[str, str]] = []
        workflow_run_id = ""
        workflow_id = ""
        workflow_status = ""
        message_ended = False
        event_count = 0
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event_count += 1
            if event_count > 2000:
                raise DifyRuntimeError("response_too_large", "Dify streaming response exceeded the event limit.")
            try:
                item = json.loads(line[6:])
            except ValueError as error:
                raise DifyRuntimeError("invalid_response", "Dify returned malformed streaming data.") from error
            if not isinstance(item, dict):
                raise DifyRuntimeError("invalid_response", "Dify returned an unexpected streaming event.")
            kind = item.get("event")
            if kind == "error":
                raise DifyRuntimeError("dify_error", "Dify reported a workflow execution error.")
            run_id = item.get("workflow_run_id")
            if isinstance(run_id, str) and run_id:
                workflow_run_id = run_id
            data = item.get("data")
            if kind == "node_finished" and isinstance(data, dict):
                node = {key: data.get(key, "") for key in ("node_id", "node_type", "title", "status")}
                if all(isinstance(value, str) for value in node.values()):
                    nodes.append(node)
            elif kind == "workflow_finished" and isinstance(data, dict):
                workflow_status = str(data.get("status", ""))
                workflow_id = str(data.get("workflow_id", ""))
            elif kind == "message" and isinstance(item.get("answer"), str):
                answer_parts.append(item["answer"])
            elif kind == "message_replace" and isinstance(item.get("answer"), str):
                answer_parts = [item["answer"]]
            elif kind == "message_end":
                message_ended = True
        if workflow_status and workflow_status != "succeeded":
            raise DifyRuntimeError("workflow_failed", "Dify workflow did not finish successfully.")
        if not message_ended or not workflow_run_id or not workflow_status:
            raise DifyRuntimeError("incomplete_response", "Dify streaming response ended before completion evidence.")
        answer = "".join(answer_parts).strip()
        if not answer:
            raise DifyRuntimeError("empty_response", "Dify returned no answer.")
        return {"answer": answer}, {
            "source": "dify_stream_node_finished",
            "workflow_run_id": workflow_run_id,
            "workflow_id": workflow_id,
            "workflow_status": workflow_status,
            "nodes": nodes,
        }

    @staticmethod
    def _request_body(
        target: DifyTargetModel, *, inputs: dict[str, Any], query: str | None, user: str
    ) -> dict[str, Any]:
        if target.target_kind == "workflow":
            return {"inputs": inputs, "response_mode": "blocking", "user": user}
        if not query:
            raise DifyRuntimeError("missing_query", "Chatflow evaluation cases require a query.")
        body = {"inputs": inputs, "query": query, "response_mode": "blocking", "user": user}
        if target.published_workflow_id:
            body["workflow_id"] = target.published_workflow_id
        return body
