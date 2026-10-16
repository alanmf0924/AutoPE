from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.local_session import authenticate_local_request
from app.repository import audit, new_id

ALL_ROLES = frozenset({"annotator", "operator", "prompt_owner", "approver", "auditor"})
ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "operator": frozenset({"targets.manage", "datasets.manage", "evaluations.run"}),
    "annotator": frozenset({"reviews.annotate"}),
    "prompt_owner": frozenset({"candidates.manage"}),
    "approver": frozenset({"approvals.manage"}),
    "auditor": frozenset({"audit.read"}),
}


@dataclass(frozen=True)
class Actor:
    subject: str
    roles: frozenset[str]
    display_name: str = ""
    is_platform_admin: bool = False
    csrf_token: str = ""

    @property
    def allowed_actions(self) -> list[str]:
        actions = {"password.change"}
        for role in self.roles:
            actions.update(ROLE_ACTIONS.get(role, frozenset()))
        if self.is_platform_admin:
            actions.add("accounts.manage")
        return sorted(actions)


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID", "").strip()[:64] or new_id("req")


def current_actor(request: Request, session: Session = Depends(get_session)) -> Actor:
    settings = get_settings()
    if settings.auth_disabled:
        return Actor(
            subject=request.headers.get("X-AutoPE-Actor", "local-operator"), roles=ALL_ROLES
        )
    try:
        identity = authenticate_local_request(request, settings)
    except HTTPException as error:
        if error.status_code in {401, 403} and isinstance(session, Session):
            audit(
                session, actor_id="", action="security.authentication_denied",
                resource_type="http_route", resource_id=request.url.path[:64],
                request_id=_request_id(request), detail={"reason_status": error.status_code},
                identity_source="anonymous", outcome="denied",
            )
            session.commit()
        raise
    return Actor(
        subject=identity.account_id,
        roles=identity.roles,
        display_name=identity.display_name,
        is_platform_admin=identity.is_platform_admin,
        csrf_token=identity.csrf_token,
    )


def require_roles(*required_roles: str):
    def dependency(
        request: Request, actor: Actor = Depends(current_actor),
        session: Session = Depends(get_session),
    ) -> Actor:
        if not actor.roles.intersection(required_roles):
            from app.main import settings as api_settings

            audit(
                session, actor_id=actor.subject, action="security.role_denied",
                resource_type="http_route", resource_id=request.url.path[:64],
                request_id=_request_id(request), detail={"reason_status": 403},
                identity_source="local_unverified" if api_settings.auth_disabled else "local_verified",
                outcome="denied", actor_roles=sorted(actor.roles),
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission for this action.",
            )
        return actor

    return dependency
