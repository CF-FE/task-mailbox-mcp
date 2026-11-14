# Agent observation and direct task relationships

The observation API keeps schema version 1 and uses the existing sixteen MCP tools. This contract describes the source changes. Deployment and client discovery require separate verification.

## Instance-scoped reports

The private access policy can bind a dedicated HTTP source grant to exactly one `execution` object with `project_id`, `task_id`, `run_id` and `execution_id`. The grant has a unique token digest, one matching project, `task` and/or `blocker` entity types, and `self_report` authority. Give the bearer credential only to the assigned execution through its authorized host channel. The service rejects events outside the scope and accepts only current activity reports through this grant.

`reporter_binding_verified` in the timeline records that the reporting credential matched the configured scope. Native session presence, actual execution, result accuracy and business acceptance still require evidence from their respective sources. An existing MCP login can include a claimed execution ID in a self-report. It receives no verified-binding marker even when the ID matches another source's grant.

An HTTP source can read back its own receipt by the original batch ID. MCP callers use their original batch and source IDs. Reuse stable IDs and check receipts when the outcome is uncertain.

## Direct relationships and workflow ownership

A native source can publish a `delegation` snapshot with both task IDs, both execution IDs, a source revision, and one observed state of `DISPATCHED`, `ACCEPTED`, `CANCEL_REQUESTED`, `STOPPED` or `RESUMED`. Use the upstream task as the event's `task_id`. Record receipt times and stop evidence when actually observed. Keep cancellation request and confirmed stop separate from business acceptance.

Add `execution_id` to `get_task_trace` or its HTTP equivalent to filter timeline events and direct relationships for that execution. The response exposes `relationships.upstream` and `relationships.downstream`. Existing project read authorization applies first. Conflicting native snapshots of the same delegation appear in `unknown_reasons["delegation:<id>"]` for either affected endpoint. Legacy delegations remain available in task-wide traces.

Workflow nodes may add task and execution references. For a run with execution-qualified nodes, configure `workflow_authors[project_id][run_id]` to its native HTTP source on both writer and reader. The writer requires the source credential digest. The local reader uses only source metadata and the author mapping. Omit token digests and MCP login mappings from the reader policy. One author publishes the graph. Worker activity belongs in task reports.

The PostgreSQL schema already stores these fields in JSONB. This change does not require a migration. The monitor continues to read through its existing loopback-only service.
