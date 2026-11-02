"""Caller-supplied GitHub references, not Projects access or verification."""

from copy import deepcopy
import re
from urllib.parse import urlsplit


OWNER = r"[A-Za-z0-9][A-Za-z0-9-]{0,38}"
REPOSITORY = re.compile(rf"({OWNER})/([A-Za-z0-9_.-]{{1,100}})\Z", re.ASCII)
MAX_ID = 2**63 - 1


def _positive_id(value):
    if type(value) is not int or not 1 <= value <= MAX_ID:
        raise ValueError("tracking IDs must be positive integers")


def _github_path(value):
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or "?" in value or "#" in value):
        raise ValueError("tracking requires a plain GitHub HTTPS URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("tracking requires a plain GitHub HTTPS URL")
    return parsed.path


def validate_tracking(value):
    """Validate only syntax and consistency; no network or inferred references."""
    fields = {"repository", "project_url", "item_id", "iteration"}
    if (not isinstance(value, dict) or not fields <= set(value)
            or set(value) - fields not in ({"issue_url"}, {"draft_id"})):
        raise ValueError("tracking requires repository, project_url, item_id, iteration and exactly one issue or draft")
    repository = value["repository"]
    if (not isinstance(repository, str) or REPOSITORY.fullmatch(repository) is None
            or repository.split("/", 1)[1] in {".", ".."}):
        raise ValueError("tracking repository must be owner/repo")
    project = re.fullmatch(rf"/(?:orgs|users)/({OWNER})/projects/([1-9][0-9]*)", _github_path(value["project_url"]), re.ASCII)
    if project is None:
        raise ValueError("tracking project URL has an unsupported path")
    if "issue_url" in value:
        issue = re.fullmatch(rf"/({OWNER})/([A-Za-z0-9_.-]{{1,100}})/issues/([1-9][0-9]*)", _github_path(value["issue_url"]), re.ASCII)
        if issue is None:
            raise ValueError("tracking issue URL has an unsupported path")
        if (issue[1] + "/" + issue[2]).casefold() != repository.casefold():
            raise ValueError("tracking issue must belong to the declared repository")
        _positive_id(int(issue[3]))
    elif not isinstance(value["draft_id"], str) or re.fullmatch(r"DI_[A-Za-z0-9_-]{1,125}", value["draft_id"], re.ASCII) is None:
        raise ValueError("tracking draft_id must retain the native DI_ reference")
    for identifier in (value["item_id"], int(project[2])):
        _positive_id(identifier)
    iteration = value["iteration"]
    if not isinstance(iteration, dict):
        raise ValueError("tracking iteration must be an object")
    if set(iteration) == {"field_id", "iteration_id"}:
        _positive_id(iteration["field_id"])
        identifier = iteration["iteration_id"]
        if (not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in identifier)):
            raise ValueError("tracking iteration_id must be a short nonempty identifier")
    elif set(iteration) == {"state", "reason"}:
        reason = iteration["reason"]
        if (iteration["state"] != "UNSCHEDULED" or not isinstance(reason, str)
                or not reason.strip() or len(reason) > 512):
            raise ValueError("an unscheduled iteration needs an explicit reason")
    else:
        raise ValueError("tracking iteration needs IDs or an UNSCHEDULED reason")
    return deepcopy(value)


def attach_tracking(result, tracking):
    """Attach a validated stored job reference; validate any external echo."""
    if not isinstance(result, dict):
        raise ValueError("result must be an object")
    prepared = dict(result)
    if tracking is not None:
        if "task_tracking" in result and validate_tracking(result["task_tracking"]) != tracking:
            raise ValueError("result task_tracking conflicts with its original job")
        prepared["task_tracking"] = deepcopy(tracking)
    return prepared


def hook_tracking_view(tracking):
    """Bounded references only; do not elevate free-form reasons into context."""
    view = {key: tracking[key] for key in ("repository", "project_url", "item_id")}
    source = "issue_url" if "issue_url" in tracking else "draft_id"
    view[source] = tracking[source]
    iteration = tracking["iteration"]
    view["iteration"] = ({"state": "UNSCHEDULED"} if "state" in iteration else dict(iteration))
    return view
