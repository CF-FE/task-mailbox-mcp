"""Expose observation operations through the authenticated MCP facade."""

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from sqlalchemy.exc import SQLAlchemyError

from mailbox_store import _json
from .http import APIError
from .models import Cursor, Id


def add_tools(server, service, access, read, write):
    def login():
        token = get_access_token()
        return (token.claims or {}).get("login") if token else None

    def invoke(operation, *args):
        try:
            return operation(*args).model_dump(mode="json")
        except APIError as error:
            raise ToolError(f"{error.code}: {error.message}") from None
        except (ValueError, TypeError, RecursionError):
            raise ToolError("INVALID_REQUEST: invalid observation data") from None
        except SQLAlchemyError:
            raise ToolError("STORAGE_UNAVAILABLE: reconcile using the original IDs") from None

    @server.tool(annotations=read)
    def get_project_overview(project_id: Id, cursor: Cursor = "0", limit: int = 50) -> dict:
        """Get project metrics and task summaries for an authorized project."""
        return invoke(service.overview, access.viewer_projects(login()), project_id, cursor, limit)

    @server.tool(annotations=read)
    def list_role_activity(project_id: Id, cursor: Cursor = "0", limit: int = 50) -> dict:
        """Read observed role activity and its evidence."""
        return invoke(service.roles, access.viewer_projects(login()), project_id, cursor, limit)

    @server.tool(annotations=read)
    def get_task_trace(task_id: Id, project_id: Id | None = None,
                       cursor: Cursor = "0", limit: int = 50, execution_id: Id | None = None) -> dict:
        """Read reported, accepted and formal states with a paginated evidence timeline."""
        return invoke(service.trace, access.viewer_projects(login()), task_id, project_id, cursor, limit, execution_id)

    @server.tool(annotations=read)
    def get_workflow_run(run_id: Id, project_id: Id | None = None) -> dict:
        """Read the declared workflow nodes and dependencies."""
        return invoke(service.workflow, access.viewer_projects(login()), run_id, project_id)

    @server.tool(annotations=read)
    def list_blockers(project_id: Id, cursor: Cursor = "0", limit: int = 50) -> dict:
        """Read observed blockers, handoff information, and evidence."""
        return invoke(service.blockers, access.viewer_projects(login()), project_id, cursor, limit)

    @server.tool(annotations=write)
    def publish_observation_batch(batch: dict) -> dict:
        """Persist a self-reported observation batch using the caller's configured source grant."""
        def publish():
            source = access.mcp_source(login(), batch.get("source_id"))
            return service.ingest(source, _json(batch).encode("utf-8"))
        return invoke(publish)

    @server.tool(annotations=read)
    def get_observation_receipt(batch_id: Id, source_id: Id) -> dict:
        """Reconcile an observation batch by the original source and batch IDs."""
        def read_receipt():
            return service.receipt(access.mcp_source(login(), source_id), batch_id)
        return invoke(read_receipt)
