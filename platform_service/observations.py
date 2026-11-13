"""Durable observations and source-scoped, repeatable-read project queries."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from mailbox_store import _hash, _json
from .http import APIError, MAX_BATCH_BYTES
from .models import (
    FIELDS, Blocker, Delegation, EventHint, ObservationBatch, Overview, Project, Receipt,
    RoleActivity, Snapshot, Source, TaskSummary, TaskTrace, TimelineEvent, WorkflowRun,
)

MAX_CURSOR = 2**63 - 1
PRIORITY = {"self_report": 0, "native_observation": 1, "owner_decision": 2, "formal_record": 3}
TASK_AUTHORITY = {
    "acceptance_status": "owner_decision", "formal_status": "formal_record",
    "followup_status": "native_observation", "followup_revision": "native_observation",
    "followup_ref": "native_observation",
}
SELF_REPORT_FIELDS = {"title", "reported_status"}
ROLE_ACTIVITY_FIELDS = {"observed_state", "latest_action", "last_observed_at", "task_id"}


def integer_cursor(value):
    if (not isinstance(value, str) or not value.isascii() or not value.isdigit()
            or len(value) > 19 or int(value) > MAX_CURSOR):
        raise APIError(422, "INVALID_CURSOR", "Invalid cursor")
    return int(value)


def require_project(allowed, project_id):
    if project_id not in allowed:
        raise APIError(404, "NOT_FOUND", "Project or record not found")


def snapshot_page(items, cursor, limit):
    offset = integer_cursor(cursor)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise APIError(422, "INVALID_LIMIT", "Limit must be 1 through 200")
    end = offset + limit
    return items[offset:end], str(end) if end < len(items) else None


def age(start, end, field, unknown):
    if start is None or end is None or end < start:
        unknown[field] = "missing_or_inconsistent_time_evidence"
        return None
    return int((end - start).total_seconds())


class Observations:
    def __init__(self, engine, policy, stale_seconds=120):
        self.engine = engine
        self.policy = policy
        self.stale_seconds = stale_seconds

    def ingest(self, source, raw):
        if len(raw) > MAX_BATCH_BYTES:
            raise APIError(413, "BATCH_TOO_LARGE", "Maximum batch size is 1 MiB")
        try:
            original = json.loads(raw)
            canonical = _json(original)
            request_hash = _hash(canonical)
            batch = ObservationBatch.model_validate_json(raw)
            digests = [_hash(_json(item)) for item in original["events"]]
        except (ValidationError, ValueError, TypeError, RecursionError):
            raise APIError(422, "INVALID_OBSERVATION", "Invalid observation batch or payload") from None
        if batch.source_id != source.id:
            raise APIError(403, "SOURCE_FORBIDDEN", "Source does not match its authenticated grant")
        allowed = source.grant
        if any(event.project_id not in allowed.projects or event.entity_type not in allowed.entity_types
               for event in batch.events):
            raise APIError(403, "SOURCE_FORBIDDEN", "Event outside source project or entity grant")
        if any(event.entity_type == "project" and event.entity_id != event.project_id
               for event in batch.events):
            raise APIError(422, "INVALID_OBSERVATION", "Project entity ID must match project ID")
        if any(event.entity_type == "task" and event.task_id not in (None, event.entity_id)
               for event in batch.events):
            raise APIError(422, "INVALID_OBSERVATION", "Task entity ID must match its task association")
        if len({event.event_id for event in batch.events}) != len(batch.events):
            raise APIError(422, "INVALID_OBSERVATION", "Batch event IDs must be unique")

        bodies = []
        for event in batch.events:
            body = event.model_dump(mode="json")
            body["payload"] = FIELDS[event.entity_type].model_validate(event.payload).model_dump(
                mode="json", exclude_unset=True)
            if event.entity_type in {"blocker", "role", "delegation", "workflow"} and event.task_id:
                payload_task = body["payload"].get("task_id")
                if payload_task is not None and payload_task != event.task_id:
                    raise APIError(422, "INVALID_OBSERVATION", "Task association does not match")
                body["payload"]["task_id"] = event.task_id
            evidence = body["evidence"].copy()
            evidence.extend(body["payload"].get("evidence", []))
            for field in ("nodes", "edges"):
                for item in body["payload"].get(field, []):
                    evidence.extend(item.get("evidence", []))
            for item in evidence:
                # A missing authority follows the source grant, with self-report as the default
                # for Agent-only sources. An explicit authority can never exceed that grant.
                if item.get("authority") is None:
                    item["authority"] = ("native_observation" if "native_observation" in allowed.authorities
                                         else "self_report")
                if item["authority"] not in allowed.authorities:
                    raise APIError(403, "AUTHORITY_FORBIDDEN", "Evidence authority exceeds source grant")
            authorities = {item["authority"] for item in body["evidence"]}
            if event.entity_type == "task":
                for field, authority in TASK_AUTHORITY.items():
                    if body["payload"].get(field) not in (None, "UNKNOWN") and authority not in authorities:
                        raise APIError(403, "AUTHORITY_FORBIDDEN", f"{field} requires matching evidence authority")
            if event.entity_type == "workflow":
                for node in body["payload"].get("nodes", []):
                    if node.get("measured_duration_seconds") is None:
                        continue
                    if ("native_observation" not in authorities or
                            body["payload"].get("kind") != "observed" or
                            not any(item["authority"] == "native_observation" for item in node.get("evidence", []))):
                        raise APIError(403, "AUTHORITY_FORBIDDEN",
                                       "Node measurements require native observation of the run and node")
            # PostgreSQL JSONB cannot represent embedded U+0000 or invalid Unicode.
            try:
                encoded = _json(body)
                encoded.encode("utf-8")
                if "\\u0000" in encoded:
                    # Distinguish a literal backslash escape from an actual null character.
                    def has_null(value):
                        if isinstance(value, str):
                            return "\0" in value
                        if isinstance(value, dict):
                            return any(has_null(k) or has_null(v) for k, v in value.items())
                        return isinstance(value, list) and any(has_null(v) for v in value)
                    if has_null(body):
                        raise ValueError("embedded null")
            except (ValueError, RecursionError):
                raise APIError(422, "INVALID_OBSERVATION", "Observation contains unsupported text") from None
            bodies.append(body)

        accepted, duplicate = [], []
        with self.engine.begin() as conn:
            watermark = conn.exec_driver_sql(
                "SELECT value FROM platform_cursors WHERE stream='observations' FOR UPDATE"
            ).scalar_one()
            # Keep this statement after the row lock so a blocked retry sees
            # the preceding transaction's committed batch and event hashes.
            prior_batch, prior_receipt, prior = conn.exec_driver_sql("""
                SELECT b.request_hash, b.receipt, COALESCE((
                    SELECT jsonb_object_agg(event_id, request_hash)
                    FROM observation_events
                    WHERE source_id=%s AND event_id=ANY(%s) AND b.request_hash IS NULL
                ), '{}'::jsonb)
                FROM (VALUES (1)) AS seed(n)
                LEFT JOIN observation_batches b ON b.source_id=%s AND b.batch_id=%s
            """, (source.id, [event.event_id for event in batch.events],
                  source.id, batch.batch_id)).one()
            if prior_batch is not None:
                if prior_batch != request_hash:
                    raise APIError(409, "OBSERVATION_CONFLICT", "Batch ID already has different content")
                return Receipt.model_validate(prior_receipt)
            received = datetime.now(timezone.utc)
            inserts, snapshots = [], []
            for event, body, digest in zip(batch.events, bodies, digests, strict=True):
                if event.event_id in prior:
                    if prior[event.event_id] != digest:
                        raise APIError(409, "OBSERVATION_CONFLICT", "Event ID already has different content")
                    duplicate.append(event.event_id)
                    continue
                watermark += 1
                linked_task = (event.entity_id if event.entity_type == "task"
                               else event.task_id or body["payload"].get("task_id"))
                inserts.append((watermark, source.id, event.event_id, event.project_id, event.entity_type,
                                event.entity_id, linked_task, event.observed_at, received, digest, Jsonb(body)))
                accepted.append(event.event_id)
                if event.event_type == "snapshot" and not event.historical:
                    snapshots.append((event.project_id, event.entity_type, event.entity_id, source.id,
                                      watermark, event.observed_at, event.source_revision, linked_task))
            if inserts:
                conn.exec_driver_sql("""
                    INSERT INTO observation_events
                    (cursor,source_id,event_id,project_id,entity_type,entity_id,task_id,
                     observed_at,received_at,request_hash,body)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, inserts)
            if snapshots:
                conn.exec_driver_sql("""
                    INSERT INTO observation_snapshots
                    (project_id, entity_type, entity_id, source_id, cursor, observed_at, source_revision, task_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (project_id, entity_type, entity_id, source_id) DO UPDATE
                    SET cursor=excluded.cursor, observed_at=excluded.observed_at,
                        source_revision=excluded.source_revision, task_id=excluded.task_id
                    WHERE (excluded.source_revision IS NOT NULL AND
                          (observation_snapshots.source_revision IS NULL OR
                           excluded.source_revision > observation_snapshots.source_revision))
                       OR (excluded.source_revision IS NULL AND observation_snapshots.source_revision IS NULL
                           AND excluded.observed_at > observation_snapshots.observed_at)
                """, snapshots)
            receipt = Receipt(batch_id=batch.batch_id, source_id=source.id, accepted_event_ids=accepted,
                              duplicate_event_ids=duplicate, watermark=str(watermark))
            conn.exec_driver_sql("""
                WITH advance AS (
                    UPDATE platform_cursors SET value=%s WHERE stream='observations' AND %s
                )
                INSERT INTO observation_batches (source_id,batch_id,request_hash,receipt,projects)
                VALUES (%s,%s,%s,%s,%s)
            """, (watermark, bool(accepted), source.id, batch.batch_id, request_hash,
                  Jsonb(receipt.model_dump()), sorted({event.project_id for event in batch.events})))
        return receipt

    def receipt(self, source, batch_id):
        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT receipt, projects FROM observation_batches WHERE source_id=%s AND batch_id=%s",
                (source.id, batch_id),
            ).one_or_none()
        if row is None or not set(row.projects) <= source.grant.projects:
            raise APIError(404, "NOT_FOUND", "Observation receipt not found")
        return Receipt.model_validate(row.receipt)

    @contextmanager
    def _read(self):
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            watermark = conn.exec_driver_sql(
                "SELECT value FROM platform_cursors WHERE stream='observations'"
            ).scalar_one()
            yield conn, datetime.now(timezone.utc), str(watermark)

    @staticmethod
    def _snapshots(conn, projects, kinds, entity_id=None, task_id=None):
        sql = """
            SELECT s.project_id, s.entity_type, s.entity_id, s.source_id, s.observed_at, e.body
            FROM observation_snapshots s JOIN observation_events e USING(cursor)
            WHERE s.project_id=ANY(%s) AND s.entity_type=ANY(%s)
        """
        params = [list(projects), list(kinds)]
        if entity_id is not None:
            sql += " AND s.entity_id=%s"
            params.append(entity_id)
        if task_id is not None:
            sql += " AND s.task_id=%s"
            params.append(task_id)
        sql += " ORDER BY s.project_id COLLATE \"C\", s.entity_id COLLATE \"C\", s.source_id COLLATE \"C\""
        return conn.exec_driver_sql(sql, tuple(params)).all()

    @staticmethod
    def _merge(rows):
        role_activity = {}
        for row in rows:
            if row.entity_type != "role":
                continue
            body = row.body
            payload = body["payload"]
            authorities = {item["authority"] for item in body["evidence"]}
            if not ROLE_ACTIVITY_FIELDS.intersection(payload) or not authorities - {"self_report"}:
                continue
            role = row.project_id, row.entity_id
            session, binding = payload.get("session_ref"), payload.get("binding_id")
            observed = payload.get("last_observed_at")
            if "native_observation" not in authorities or not (session and observed):
                role_activity.setdefault(role, []).append(None)
            else:
                role_activity.setdefault(role, []).append(
                    (session, binding, datetime.fromisoformat(observed)))

        # Use explicit activity time only when all contributing observations identify
        # the same session and binding. The normal conflict rule covers other roles.
        comparable_roles = {
            role for role, observations in role_activity.items()
            if None not in observations and len({item[:2] for item in observations}) == 1
        }
        groups = {}
        for row in rows:
            fields, strengths, conflicts, evidence, last = groups.setdefault(
                (row.project_id, row.entity_type, row.entity_id), ({}, {}, set(), [], []))
            body = row.body
            authorities = {item["authority"] for item in body["evidence"]}
            trusted = bool(authorities - {"self_report"})
            if not trusted and row.entity_type != "task":
                continue
            evidence.extend(body["evidence"])
            if trusted:
                last.append(row.observed_at)
            for key, value in body["payload"].items():
                if not trusted and key not in SELF_REPORT_FIELDS:
                    continue
                required = TASK_AUTHORITY.get(key)
                if required and required not in authorities:
                    continue
                score = PRIORITY[required] if required else max(
                    (PRIORITY[item] for item in authorities if item != "formal_record"), default=0)
                if key == "reported_status":
                    score = int("self_report" in authorities)
                activity_time = datetime.min.replace(tzinfo=timezone.utc)
                if (row.entity_type == "role" and key in ROLE_ACTIVITY_FIELDS and
                        (row.project_id, row.entity_id) in comparable_roles):
                    activity_time = datetime.fromisoformat(body["payload"]["last_observed_at"])
                # Explicit null in a newer activity means the session is unassigned.
                # Without comparable identity, a conflicting association stays unknown.
                role_task = row.entity_type == "role" and key == "task_id"
                rank = (score, activity_time, role_task or value is not None)
                previous = strengths.get(key, (-1, activity_time, False))
                if rank > previous:
                    strengths[key], fields[key] = rank, value
                    conflicts.discard(key)
                elif rank == previous and value != fields[key]:
                    conflicts.add(key)
        result = {}
        for key, (fields, _, conflicts, evidence, last) in groups.items():
            if not fields and not last:
                continue
            for field in conflicts:
                fields.pop(field)
            result[key] = (fields, evidence, max(last, default=None), conflicts)
        return result

    def _sources(self, conn, projects, now):
        live = conn.exec_driver_sql("""
            SELECT DISTINCT ON (source_id) source_id, observed_at, received_at
            FROM observation_events WHERE project_id=ANY(%s) AND NOT (body->>'historical')::boolean
            ORDER BY source_id, observed_at DESC, cursor DESC
        """, (list(projects),)).all()
        live_by_id = {row.source_id: row for row in live}
        health = self._snapshots(conn, projects, ["source"])
        explicit = {}
        for row in health:
            facts = row.body["payload"]
            native = any(item["authority"] == "native_observation" for item in row.body["evidence"])
            if native:
                explicit.setdefault(row.entity_id, facts)
        expected = ({source_id for source_id, grant in self.policy.sources.items() if projects & grant.projects}
                    | explicit.keys())
        result = []
        for source_id in sorted(expected | live_by_id.keys()):
            signal = live_by_id.get(source_id)
            facts = explicit.get(source_id, {})
            state, gap = facts.get("state"), facts.get("gap_reason")
            if not signal:
                state, gap = state if state in ("UNKNOWN", "UNAVAILABLE", "STALE") else "UNKNOWN", gap or "no_live_observations"
            elif state in ("UNAVAILABLE", "UNKNOWN", "STALE"):
                pass
            elif (now - min(signal.received_at, signal.observed_at)).total_seconds() > self.stale_seconds:
                state, gap = "STALE", gap or "source_freshness_window_exceeded"
            else:
                state = "HEALTHY"
            result.append(Source(id=source_id, kind=facts.get("kind"), state=state, gap_reason=gap,
                                 last_observed_at=signal.observed_at if signal else None,
                                 last_received_at=signal.received_at if signal else None))
        return result

    def _summary(self, conn, projects):
        groups = self._merge(self._snapshots(conn, projects, ["project", "task", "blocker"]))
        task_lists = {project: [] for project in projects}
        blockers = {project: set() for project in projects}
        unresolved = dict.fromkeys(projects, 0)
        project_fields = {}
        for (project_id, kind, entity_id), (fields, _, _, _) in groups.items():
            if kind == "task":
                task_lists[project_id].append(TaskSummary(id=entity_id, project_id=project_id, **fields))
            elif kind == "blocker":
                state = fields.get("state")
                if state == "OPEN" and fields.get("task_id"):
                    blockers[project_id].add(fields["task_id"])
                elif state != "RESOLVED":
                    unresolved[project_id] += 1
            elif kind == "project" and entity_id == project_id:
                project_fields[project_id] = fields
        return [
            (Project(id=project, **project_fields.get(project, {}),
                     accepted_count=sum(task.acceptance_status == "ACCEPTED" for task in task_lists[project]),
                     pending_acceptance_count=sum(task.reported_status == "REPORTED_COMPLETED"
                                                  and task.acceptance_status in (None, "UNKNOWN", "PENDING")
                                                  for task in task_lists[project]),
                     blocked_count=len(blockers[project]),
                     unresolved_blocker_count=unresolved[project]), task_lists[project])
            for project in sorted(projects)
        ]

    def _envelope(self, conn, projects, now, watermark, data, next_cursor=None):
        return Snapshot(observed_at=now, watermark=watermark, sources=self._sources(conn, projects, now),
                        data=data, next_cursor=next_cursor)

    def projects(self, allowed, cursor, limit):
        with self._read() as (conn, now, watermark):
            page, more = snapshot_page([item for item, _ in self._summary(conn, allowed)], cursor, limit)
            return self._envelope(conn, {item.id for item in page}, now, watermark, page, more)

    def overview(self, allowed, project_id, cursor, limit):
        require_project(allowed, project_id)
        with self._read() as (conn, now, watermark):
            [(project, tasks)] = self._summary(conn, {project_id})
            page, more = snapshot_page(tasks, cursor, limit)
            return self._envelope(conn, {project_id}, now, watermark,
                                  Overview(**project.model_dump(), tasks=page), more)

    def roles(self, allowed, project_id, cursor, limit):
        require_project(allowed, project_id)
        with self._read() as (conn, now, watermark):
            groups = self._merge(self._snapshots(conn, {project_id}, ["role"]))
            roles = []
            for (_, _, entity_id), (fields, evidence, last, conflicts) in groups.items():
                role = RoleActivity(id=entity_id, evidence=evidence, **fields)
                role.unknown_reasons.update(dict.fromkeys(conflicts, "conflicting_source_observations"))
                if "last_observed_at" not in conflicts:
                    role.last_observed_at = role.last_observed_at or last
                role.elapsed_seconds = age(role.accepted_at, role.last_observed_at,
                                           "elapsed_seconds", role.unknown_reasons)
                roles.append(role)
            page, more = snapshot_page(roles, cursor, limit)
            return self._envelope(conn, {project_id}, now, watermark, page, more)

    def blockers(self, allowed, project_id, cursor, limit):
        require_project(allowed, project_id)
        with self._read() as (conn, now, watermark):
            groups = self._merge(self._snapshots(conn, {project_id}, ["blocker"]))
            items = []
            for (_, _, entity_id), (fields, evidence, last, conflicts) in groups.items():
                blocker = Blocker(id=entity_id, **{**fields, "evidence": evidence or fields.get("evidence", [])})
                blocker.unknown_reasons.update(dict.fromkeys(conflicts, "conflicting_source_observations"))
                end = blocker.resolved_at if blocker.state == "RESOLVED" else last if blocker.state == "OPEN" else None
                blocker.elapsed_seconds = age(blocker.opened_at, end, "elapsed_seconds", blocker.unknown_reasons)
                if blocker.committed_deadline_at is None:
                    blocker.unknown_reasons["committed_deadline_at"] = "no_commitment_recorded"
                items.append(blocker)
            page, more = snapshot_page(items, cursor, limit)
            return self._envelope(conn, {project_id}, now, watermark, page, more)

    @staticmethod
    def _resolve(conn, allowed, project_id, kind, entity_id):
        if project_id is not None:
            require_project(allowed, project_id)
            allowed = {project_id}
        matches = conn.exec_driver_sql("""
            SELECT DISTINCT project_id FROM observation_events
            WHERE project_id=ANY(%s) AND entity_type=%s AND entity_id=%s
            LIMIT 2
        """, (list(allowed), kind, entity_id)).scalars().all()
        if not matches:
            raise APIError(404, "NOT_FOUND", "Project or record not found")
        if len(matches) > 1:
            raise APIError(409, "AMBIGUOUS_ID", "Specify project_id for this record")
        return matches[0]

    def trace(self, allowed, task_id, project_id, cursor, limit):
        after = integer_cursor(cursor)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise APIError(422, "INVALID_LIMIT", "Limit must be 1 through 200")
        with self._read() as (conn, now, watermark):
            project = self._resolve(conn, allowed, project_id, "task", task_id)
            groups = self._merge(self._snapshots(conn, {project}, ["task"], entity_id=task_id))
            fields, evidence, _, conflicts = groups.get((project, "task", task_id), ({}, [], None, set()))
            rows = conn.exec_driver_sql("""
                SELECT cursor, source_id, received_at, body FROM observation_events
                WHERE project_id=%s AND task_id=%s AND cursor>%s ORDER BY cursor LIMIT %s
            """, (project, task_id, after, limit + 1)).all()
            events = [TimelineEvent(cursor=str(row.cursor), source_id=row.source_id,
                                    received_at=row.received_at, **row.body) for row in rows[:limit]]
            delegations = self._merge(self._snapshots(conn, {project}, ["delegation"], task_id=task_id))
            trace = TaskTrace(id=task_id, project_id=project, events=events, evidence=evidence,
                              delegations=[Delegation(id=key[2], **{**values, "evidence": facts or values.get("evidence", [])})
                                           for key, (values, facts, _, _) in delegations.items()], **fields)
            for field, start, end in (
                ("elapsed_seconds", trace.requested_at, trace.finished_at),
                ("execution_seconds", trace.started_at, trace.finished_at),
                ("acceptance_wait_seconds", trace.requested_at, trace.accepted_at),
                ("progress_age_seconds", trace.last_progress_at, now),
            ):
                setattr(trace, field, age(start, end, field, trace.unknown_reasons))
            for field in ("committed_deadline_at", "internal_check_at"):
                if getattr(trace, field) is None:
                    trace.unknown_reasons[field] = "no_time_commitment_recorded"
            trace.unknown_reasons.update(dict.fromkeys(conflicts, "conflicting_source_observations"))
            return self._envelope(conn, {project}, now, watermark, trace,
                                  events[-1].cursor if len(rows) > limit else None)

    def workflow(self, allowed, run_id, project_id):
        with self._read() as (conn, now, watermark):
            project = self._resolve(conn, allowed, project_id, "workflow", run_id)
            rows = self._snapshots(conn, {project}, ["workflow"], entity_id=run_id)
            # A graph is one source's declared object. Never merge its nodes and edges separately.
            trusted = [(max((PRIORITY[e["authority"]] for e in row.body["evidence"]
                            if e["authority"] != "self_report"), default=0), row) for row in rows]
            rank = max((score for score, _ in trusted), default=0)
            preferred = [row for score, row in trusted if score == rank and score > 0]
            agrees = preferred and all(row.body["payload"] == preferred[0].body["payload"] for row in preferred)
            if agrees:
                workflow = WorkflowRun(id=run_id, **preferred[0].body["payload"],
                                       evidence=preferred[0].body["evidence"])
            else:
                workflow = WorkflowRun(id=run_id, unknown_reasons={
                    "graph": "conflicting_source_observations" if preferred else "no_trusted_graph_observation"})
            return self._envelope(conn, {project}, now, watermark, workflow)

    def events(self, allowed, project_id, after):
        require_project(allowed, project_id)
        with self.engine.connect() as conn:
            rows = conn.exec_driver_sql("""
                SELECT cursor, entity_id, entity_type, task_id, body->>'run_id' AS run_id
                FROM observation_events WHERE project_id=%s AND cursor>%s ORDER BY cursor LIMIT 100
            """, (project_id, integer_cursor(after))).all()
        return [EventHint(watermark=str(row.cursor), project_id=project_id,
                          entity_id=row.entity_id, entity_type=row.entity_type,
                          task_id=row.task_id, run_id=row.run_id) for row in rows]
