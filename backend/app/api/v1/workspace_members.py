"""Production member management, reusing Workspace authentication/error rules."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import TokenSubject
from app.api.protected_route import protected_error_response
from app.api.v1.workspaces import ERRORS, WorkspaceRoute
from app.schemas.workspace import MAX_OFFSET
from app.schemas.workspace_member import MemberCreateRequest, MemberListResponse, MemberResponse, MemberRoleRequest
from app.services import workspace_members as service


class MemberRoute(WorkspaceRoute):
    invalid_input_detail = "Invalid member input"
    failure_detail = "Member request failed"

    def error_response(self, error: Exception):
        if isinstance(error, service.MemberNotFound):
            return protected_error_response(404, "Member not found")
        if isinstance(error, service.MemberAlreadyExists):
            return protected_error_response(409, "Member already exists")
        return super().error_response(error)


router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/members", tags=["workspace members"], route_class=MemberRoute)
MEMBER_ERRORS = {
    **ERRORS,
    404: {"description": "Workspace inaccessible or target member not found"},
    409: {"description": "Member already exists"},
    500: {"description": "Member request failed"},
}


@router.get("", response_model=MemberListResponse, responses=MEMBER_ERRORS)
def list_members(workspace_id: UUID, subject: TokenSubject,
                 limit: Annotated[int, Query(ge=1, le=100)] = 20,
                 offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0) -> MemberListResponse:
    return service.list_members(subject, workspace_id, limit, offset)


@router.post("", status_code=201, response_model=MemberResponse, responses=MEMBER_ERRORS)
def add_member(workspace_id: UUID, request: MemberCreateRequest, subject: TokenSubject) -> MemberResponse:
    return service.add_member(subject, workspace_id, request.user_id, request.role)


@router.patch("/{user_id}", response_model=MemberResponse, responses=MEMBER_ERRORS)
def update_member(workspace_id: UUID, user_id: UUID, request: MemberRoleRequest, subject: TokenSubject) -> MemberResponse:
    return service.update_member(subject, workspace_id, user_id, request.role)


@router.delete("/{user_id}", status_code=204, response_class=Response, responses=MEMBER_ERRORS)
def remove_member(workspace_id: UUID, user_id: UUID, subject: TokenSubject) -> Response:
    service.remove_member(subject, workspace_id, user_id)
    return Response(status_code=204)
