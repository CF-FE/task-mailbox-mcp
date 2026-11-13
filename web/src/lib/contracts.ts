import { z } from "zod"

const id = z.string().min(1)
const isoTime = z.iso.datetime({ offset: true })
const time = isoTime.nullable().optional()
const text = z.string().nullable().optional()
const count = z.number().int().nonnegative()
const seconds = z.number().nonnegative().nullish()
const state = text
const reasons = z.record(z.string(), z.string()).default({})

export const evidenceSchema = z.object({
  source_type: z.string(),
  ref: z.string(),
  observed_at: isoTime.nullable(),
  digest: text,
  authority: z.enum(["self_report", "native_observation", "owner_decision", "formal_record"]).nullish(),
})
export type Evidence = z.infer<typeof evidenceSchema>
const evidence = z.array(evidenceSchema).default([])

export const sourceSchema = z.object({
  id,
  kind: text,
  last_observed_at: time,
  last_received_at: time,
  state,
  gap_reason: text,
})
export type Source = z.infer<typeof sourceSchema>

export const cursorSchema = z.string().regex(/^(0|[1-9][0-9]{0,18})$/)
export function envelope<T extends z.ZodType>(data: T) {
  return z.object({
    schema_version: z.literal(1),
    observed_at: isoTime,
    watermark: cursorSchema,
    sources: z.array(sourceSchema),
    data,
    next_cursor: cursorSchema.nullable().default(null),
  })
}

export const sessionSchema = z.object({
  github_login: z.string(),
  subject: z.string(),
  allowed_projects: z.array(id),
})
export const projectSchema = z.object({
  id,
  name: text,
  summary: text,
  owner_role_id: text,
  scope_revision: text,
  accepted_count: count,
  pending_acceptance_count: count,
  blocked_count: count,
  unresolved_blocker_count: count.optional(),
  total_tasks: count.nullable().optional(),
})
export type Project = z.infer<typeof projectSchema>

export const taskSummarySchema = z.object({
  id,
  project_id: id,
  title: text,
  owner_role_id: text,
  current_handler_role_id: text,
  current_step_id: text,
  waiting_on: text,
  next_action: text,
  last_progress_at: time,
  requested_at: time,
  accepted_at: time,
  started_at: time,
  finished_at: time,
  workflow_run_id: text,
  reported_status: state,
  acceptance_status: state,
  formal_status: state,
  followup_status: state,
  followup_revision: count.nullish(),
  followup_ref: text,
  committed_deadline_at: time,
  internal_check_at: time,
})
export type TaskSummary = z.infer<typeof taskSummarySchema>
export const overviewSchema = projectSchema.extend({ tasks: z.array(taskSummarySchema) })

export const roleSchema = z.object({
  id,
  name: text,
  kind: text,
  binding_id: text,
  host_ref: text,
  session_ref: text,
  task_id: text,
  observed_state: state,
  latest_action: text,
  accepted_at: time,
  last_observed_at: time,
  last_progress_at: time,
  elapsed_seconds: seconds,
  next_action: text,
  unknown_reasons: reasons,
  evidence,
})
export type Role = z.infer<typeof roleSchema>

const activity = z.object({
  cursor: cursorSchema,
  event_id: id,
  source_id: id,
  entity_type: id,
  entity_id: id,
  event_type: id,
  occurred_at: time,
  observed_at: isoTime,
  received_at: isoTime,
  historical: z.boolean().default(false),
  payload: z.object({ summary: text, latest_action: text, action: text, state: text }),
  evidence,
})
const delegation = z.object({
  id,
  from_role_id: text,
  to_role_id: text,
  actual_caller_role_id: text,
  state,
  requested_at: time,
  accepted_at: time,
  evidence,
})
export const traceSchema = taskSummarySchema.extend({
  elapsed_seconds: seconds,
  execution_seconds: seconds,
  acceptance_wait_seconds: seconds,
  progress_age_seconds: seconds,
  unknown_reasons: reasons,
  events: z.array(activity),
  delegations: z.array(delegation),
  evidence: z.array(evidenceSchema),
})

export const stepSchema = z.object({
  id,
  label: z.string(),
  state,
  started_at: time,
  finished_at: time,
  wait_started_at: time,
  measured_duration_seconds: seconds,
  measured_duration_basis: z.string().trim().min(1).max(2048).nullish(),
  evidence,
})
export type Step = z.infer<typeof stepSchema>
export const runSchema = z.object({
  id,
  task_id: text,
  definition_version: text,
  kind: z.enum(["planned", "observed"]).nullable().optional(),
  nodes: z.array(stepSchema).default([]),
  edges: z.array(z.object({ source: id, target: id, evidence })).default([]),
  evidence,
})
export type Run = z.infer<typeof runSchema>

export const blockerSchema = z.object({
  id,
  task_id: text,
  step_id: text,
  state,
  reason: text,
  handler_role_id: text,
  waiting_on: text,
  opened_at: time,
  resolved_at: time,
  elapsed_seconds: seconds,
  next_action: text,
  committed_deadline_at: time,
  unknown_reasons: reasons,
  evidence,
})
export type Blocker = z.infer<typeof blockerSchema>
