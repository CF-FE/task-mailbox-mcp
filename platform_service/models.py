"""Version 1 observation and query DTOs."""

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
Cursor = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,18})$")]
Entity = Literal["project", "task", "role", "delegation", "workflow", "blocker", "source"]
Count = Annotated[int, Field(ge=0, strict=True)]
Stamp = AwareDatetime
T = TypeVar("T")


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Evidence(DTO):
    source_type: Text
    ref: Text
    observed_at: Stamp | None
    digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    authority: Literal["self_report", "native_observation", "owner_decision", "formal_record"] | None = None


class ProjectFields(DTO):
    name: Text | None = None
    summary: Text | None = None
    owner_role_id: Id | None = None
    scope_revision: Text | None = None
    total_tasks: Count | None = None


class Project(ProjectFields):
    id: Id
    accepted_count: Count
    pending_acceptance_count: Count
    blocked_count: Count
    unresolved_blocker_count: Count


class RoleFields(DTO):
    name: Text | None = None
    kind: Literal["coordinator", "owner", "router", "worker"] | None = None
    binding_id: Id | None = None
    host_ref: Text | None = None
    session_ref: Text | None = None
    task_id: Id | None = None
    observed_state: Text | None = None
    latest_action: Text | None = None
    accepted_at: Stamp | None = None
    last_observed_at: Stamp | None = None
    last_progress_at: Stamp | None = None
    next_action: Text | None = None


class RoleActivity(RoleFields):
    id: Id
    elapsed_seconds: Count | None = None
    unknown_reasons: dict[str, str] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class TaskFields(DTO):
    title: Text | None = None
    owner_role_id: Id | None = None
    current_handler_role_id: Id | None = None
    current_step_id: Id | None = None
    reported_status: Text | None = None
    acceptance_status: Text | None = None
    formal_status: Text | None = None
    followup_status: Text | None = None
    followup_revision: Count | None = None
    followup_ref: Text | None = None
    next_action: Text | None = None
    waiting_on: Text | None = None
    committed_deadline_at: Stamp | None = None
    internal_check_at: Stamp | None = None
    workflow_run_id: Id | None = None
    requested_at: Stamp | None = None
    accepted_at: Stamp | None = None
    started_at: Stamp | None = None
    finished_at: Stamp | None = None
    last_progress_at: Stamp | None = None


class DelegationFields(DTO):
    task_id: Id | None = None
    from_role_id: Id | None = None
    to_role_id: Id | None = None
    actual_caller_role_id: Id | None = None
    state: Text | None = None
    requested_at: Stamp | None = None
    accepted_at: Stamp | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class Delegation(DelegationFields):
    id: Id


class WorkflowNode(DTO):
    id: Id
    label: Text
    state: Text | None = None
    started_at: Stamp | None = None
    finished_at: Stamp | None = None
    wait_started_at: Stamp | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class WorkflowEdge(DTO):
    source: Id
    target: Id
    evidence: list[Evidence] = Field(default_factory=list)


class WorkflowFields(DTO):
    task_id: Id | None = None
    definition_version: Text | None = None
    kind: Literal["planned", "observed"] | None = None
    nodes: list[WorkflowNode] = Field(default_factory=list, max_length=200)
    edges: list[WorkflowEdge] = Field(default_factory=list, max_length=400)

    @model_validator(mode="after")
    def graph(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes) or any(
            edge.source not in ids or edge.target not in ids for edge in self.edges
        ):
            raise ValueError("workflow edges must reference unique, declared nodes")
        return self


class WorkflowRun(WorkflowFields):
    id: Id
    unknown_reasons: dict[str, str] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class BlockerFields(DTO):
    task_id: Id | None = None
    step_id: Id | None = None
    state: Literal["OPEN", "RESOLVED", "UNKNOWN"] | None = None
    reason: Text | None = None
    handler_role_id: Id | None = None
    waiting_on: Text | None = None
    opened_at: Stamp | None = None
    resolved_at: Stamp | None = None
    next_action: Text | None = None
    committed_deadline_at: Stamp | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class Blocker(BlockerFields):
    id: Id
    elapsed_seconds: Count | None = None
    unknown_reasons: dict[str, str] = Field(default_factory=dict)


class SourceFields(DTO):
    kind: Text | None = None
    state: Literal["HEALTHY", "STALE", "UNAVAILABLE", "UNKNOWN"] | None = None
    gap_reason: Text | None = None


class Source(SourceFields):
    id: Id
    last_observed_at: Stamp | None = None
    last_received_at: Stamp | None = None


FIELDS = {
    "project": ProjectFields, "task": TaskFields, "role": RoleFields,
    "delegation": DelegationFields, "workflow": WorkflowFields,
    "blocker": BlockerFields, "source": SourceFields,
}


class Observation(DTO):
    event_id: Id
    project_id: Id
    entity_type: Entity
    entity_id: Id
    event_type: Literal["snapshot", "activity"]
    observed_at: Stamp
    payload: dict
    evidence: list[Evidence] = Field(max_length=50)
    task_id: Id | None = None
    run_id: Id | None = None
    step_id: Id | None = None
    role_binding_id: Id | None = None
    delegation_id: Id | None = None
    source_revision: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)] | None = None
    occurred_at: Stamp | None = None
    historical: bool = False

    @model_validator(mode="after")
    def payload_fields(self):
        FIELDS[self.entity_type].model_validate(self.payload)
        return self


class ObservationBatch(DTO):
    schema_version: Literal[1]
    batch_id: Id
    source_id: Id
    events: list[Observation] = Field(min_length=1, max_length=100)


class Receipt(DTO):
    batch_id: Id
    source_id: Id
    persisted: Literal[True] = True
    accepted_event_ids: list[Id]
    duplicate_event_ids: list[Id]
    watermark: Cursor


class TimelineEvent(Observation):
    source_id: Id
    cursor: Cursor
    received_at: Stamp


class TaskTrace(TaskFields):
    id: Id
    project_id: Id
    events: list[TimelineEvent]
    delegations: list[Delegation]
    evidence: list[Evidence]
    elapsed_seconds: Count | None = None
    execution_seconds: Count | None = None
    acceptance_wait_seconds: Count | None = None
    progress_age_seconds: Count | None = None
    unknown_reasons: dict[str, str] = Field(default_factory=dict)


class TaskSummary(TaskFields):
    id: Id
    project_id: Id


class Overview(Project):
    tasks: list[TaskSummary]


class Snapshot(DTO, Generic[T]):
    schema_version: Literal[1] = 1
    observed_at: Stamp
    watermark: Cursor
    sources: list[Source]
    data: T
    next_cursor: Cursor | None = None


class EventHint(DTO):
    watermark: Cursor
    project_id: Id
    entity_type: Entity
    entity_id: Id
    task_id: Id | None = None
    run_id: Id | None = None


class Session(DTO):
    github_login: Text
    subject: Text
    allowed_projects: list[Id]
    csrf_token: str


class Error(DTO):
    code: str
    message: str
    retryable: bool


class ErrorEnvelope(DTO):
    error: Error
    request_id: str
    login_url: str | None = None
