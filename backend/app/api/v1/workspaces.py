"""Production Workspace CRUD routes using the shared JWT/RBAC boundary."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import TokenSubject
from app.api.protected_route import ProtectedRoute, protected_error_response
from app.db.workspace_session import WorkspaceCommitUnknownError
from app.schemas.workspace import (
    MAX_OFFSET, WorkspaceCreateRequest, WorkspaceListResponse, WorkspaceResponse, WorkspaceUpdateRequest,
)
from app.services import workspaces


class WorkspaceRoute(ProtectedRoute):
    invalid_input_detail = "Invalid workspace input"
    failure_detail = "Workspace request failed"

    def error_response(self, error: Exception):
        if isinstance(error, WorkspaceCommitUnknownError):
            return protected_error_response(503, "Workspace commit outcome is unknown; reconcile before retrying")
        return super().error_response(error)


router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"], route_class=WorkspaceRoute)
ERRORS = {
    401: {"description": "Invalid identity or disabled account"},
    403: {"description": "Insufficient workspace permissions"},
    404: {"description": "Workspace not found"},
    503: {"description": "Database unavailable or commit outcome unknown"},
    500: {"description": "Workspace request failed"},
}


@router.post("", status_code=201, response_model=WorkspaceResponse, responses=ERRORS)
def create_workspace(request: WorkspaceCreateRequest, subject: TokenSubject) -> WorkspaceResponse:
    return workspaces.create_workspace(subject, request.name)


@router.get("", response_model=WorkspaceListResponse, responses=ERRORS)
def list_workspaces(
    subject: TokenSubject,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> WorkspaceListResponse:
    return workspaces.list_workspaces(subject, limit, offset)


@router.get("/{workspace_id}", response_model=WorkspaceResponse, responses=ERRORS)
def get_workspace(workspace_id: UUID, subject: TokenSubject) -> WorkspaceResponse:
    return workspaces.get_workspace(subject, workspace_id)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse, responses=ERRORS)
def update_workspace(workspace_id: UUID, request: WorkspaceUpdateRequest, subject: TokenSubject) -> WorkspaceResponse:
    return workspaces.update_workspace(subject, workspace_id, request.name)


@router.delete("/{workspace_id}", status_code=204, response_class=Response, responses=ERRORS)
def delete_workspace(workspace_id: UUID, subject: TokenSubject) -> Response:
    workspaces.delete_workspace(subject, workspace_id)
    return Response(status_code=204)
