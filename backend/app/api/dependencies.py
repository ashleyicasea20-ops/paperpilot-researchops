"""FastAPI dependencies for verified identity and workspace authorization."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.authorization import Permission, WorkspaceAccessContext
from app.core.config import AuthSettings, get_auth_settings
from app.core.token import InvalidAccessToken, read_access_token
from app.db.session import auth_read_session
from app.services.authentication import AuthenticatedIdentity, require_active_identity
from app.services.workspace_access import require_workspace_permission

_bearer = HTTPBearer(auto_error=False)
JwtSettings = Annotated[AuthSettings, Depends(get_auth_settings)]


def _require_bearer(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or "," in credentials.credentials:
        raise InvalidAccessToken
    if sum(name.lower() == b"authorization" for name, _ in request.headers.raw) != 1:
        raise InvalidAccessToken
    return credentials.credentials


def get_token_subject(token: Annotated[str, Depends(_require_bearer)], settings: JwtSettings) -> UUID:
    return read_access_token(token, settings)


TokenSubject = Annotated[UUID, Depends(get_token_subject)]


def get_current_identity(subject: TokenSubject) -> AuthenticatedIdentity:
    with auth_read_session() as session:
        return require_active_identity(session, subject)


CurrentIdentity = Annotated[AuthenticatedIdentity, Depends(get_current_identity)]


def workspace_permission(permission: Permission) -> Callable[[UUID, UUID], WorkspaceAccessContext]:
    """Bind a server-selected permission to a route with a workspace_id path parameter."""
    def authorize(workspace_id: Annotated[UUID, Path()], subject: TokenSubject) -> WorkspaceAccessContext:
        with auth_read_session() as session:
            return require_workspace_permission(session, subject, workspace_id, permission)

    return authorize
