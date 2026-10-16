export const riskLevels = ['normal', 'high_risk', 'red_line'] as const
export type RiskLevel = (typeof riskLevels)[number]

export type SessionIdentity = {
  subject: string
  display_name: string
  roles: Array<'annotator' | 'operator' | 'prompt_owner' | 'approver' | 'auditor'>
  is_platform_admin: boolean
  allowed_actions: string[]
  session_state: 'active'
  csrf_token: string
  environment: 'development' | 'test' | 'shared_test' | 'production'
  authentication: 'disabled_local' | 'local_session'
}

export type DifyTarget = {
  id: string
  name: string
  base_url: string
  runtime_path: string
  target_kind: 'workflow' | 'chatflow'
  application_id: string
  input_mapping: Record<string, string>
  dify_user: string
  credential_status: 'stored' | 'external_reference'
  version_fingerprint: string
  published_workflow_id: string
  created_at: string
}

export type DifyTargetCreatePayload = {
  name: string
  base_url: string
  runtime_path: string
  target_kind: 'workflow' | 'chatflow'
  application_id: string
  input_mapping: Record<string, string>
  dify_user: string
  api_key: string
  version_fingerprint: string
  published_workflow_id: string
}

export type DifyTargetUpdatePayload = DifyTargetCreatePayload

export type DifyTargetDetail = DifyTarget & {
  usage_count: number
  candidate_binding_count: number
  can_delete: boolean
}

export type Dataset = {
  id: string
  name: string
  description: string
  case_count: number
  review_status: 'draft' | 'in_review' | 'approved'
  version: number
  is_frozen: boolean
  created_at: string
}

export type DatasetCaseInput = {
  title: string
  prompt: string
  expected_behavior: string
  expected_contract: Record<string, unknown>
  evaluation_rules: EvaluationRules
  risk_level: RiskLevel
}

export type EvaluationDimensionRules = {
  required_terms: string[]
  prohibited_terms: string[]
}

export type EvaluationRules = Partial<Record<
  'business_understanding' | 'clarification' | 'tool_use' | 'safety',
  EvaluationDimensionRules
>>

export type DatasetCase = DatasetCaseInput & {
  id: string
  position: number
  created_at: string
}

export type DatasetDetail = Dataset & {
  evidence_digest: string
  cases: DatasetCase[]
  can_edit: boolean
  can_freeze: boolean
  automation_readiness: {
    eligible_cases: number
    total_cases: number
    cases: Array<{
      case_id: string
      title: string
      eligible: boolean
      missing_dimensions: string[]
      manual_contract_fields: string[]
    }>
  }
}

export type ReviewCase = {
  id: string
  dataset_id: string
  title: string
  risk_level: RiskLevel
  review_status: 'pending' | 'approved' | 'rejected'
  score: number
  source_run_id: string
  updated_at: string
  suggestion_status: 'queued' | 'running' | 'completed' | 'failed' | 'uncertain' | null
  suggested_score: number | null
  suggestion_requested_at: string | null
  suggestion_started_at: string | null
  suggestion_completed_at: string | null
  suggestion_attempt_count: number
}

export type ReviewAISuggestion = {
  id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'uncertain'
  score_breakdown: Record<'business_understanding' | 'clarification' | 'tool_use' | 'safety', number>
  risk_level: RiskLevel
  rationale: string
  model: string
  requested_at: string
  started_at: string | null
  completed_at: string | null
  attempt_count: number
  error_message: string
}

export type ReviewCaseDetail = ReviewCase & {
  request_text: string
  response_text: string
  expected_behavior: string
  expected_contract: Record<string, unknown>
  baseline_response: string
  candidate_response: string
  error_code: string
  error_message: string
  annotation: {
    business_understanding: number
    clarification: number
    tool_use: number
    safety: number
    risk_level: RiskLevel
    rationale: string
  } | null
  annotated_by: string | null
  ai_suggestion: ReviewAISuggestion | null
  annotation_history: Array<{
    id: string
    reviewer_id: string
    event_kind: 'submitted' | 'reopened' | 'legacy_snapshot' | 'ai_accepted' | 'ai_edited'
    score_breakdown: Record<string, number>
    risk_level: RiskLevel
    rationale: string
    created_at: string
  }>
  automatic_assessment: Partial<AutomaticAssessment>
  runtime_evidence: {
    source?: string
    workflow_run_id?: string
    workflow_id?: string
    nodes?: Array<{ title: string; status: string; node_type: string }>
  }
}

export type AutomaticAssessment = {
  version: string
  verdict: 'passed' | 'failed' | 'needs_review' | 'execution_error' | 'not_available'
  checks: Array<{ code: string; status: 'passed' | 'failed' | 'unknown'; detail: string }>
}

export type CaseAssessment = {
  case_id: string
  position: number
  title: string
  risk_level: RiskLevel
  result_status: string
  passed: boolean
  error_code: string
  review_id: string
  assessment: AutomaticAssessment
  workflow_run_id: string
  verified_tool_nodes: Array<{ title: string; status: string }>
}

export type PromptCandidate = {
  id: string
  name: string
  base_version_fingerprint: string
  prompt_sha256: string
  status: 'proposed' | 'ready_for_evaluation' | 'rejected'
  bound_target_id: string | null
  binding_attested: boolean
  created_at: string
}

export type PromptCandidateDetail = PromptCandidate & {
  prompt_block: string
  binding_attestation: Record<string, string>
  generation_job: CandidateGenerationJob | null
  bound_target_name: string | null
  bound_target_version_fingerprint: string | null
  evaluation_runs: Array<{ id: string; dataset_id: string; status: EvaluationRun['status']; created_at: string }>
  evaluation_run_count: number
}

export type CandidateGenerationConfig = {
  model: string
  endpoint: string
  configured: boolean
}

export type CandidateGenerationJob = {
  id: string
  dataset_id: string
  baseline_target_id: string
  candidate_id: string | null
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  stage: string
  error_message: string
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type EvaluationRun = {
  id: string
  dataset_id: string
  baseline_target_id: string
  mode: 'baseline' | 'comparison'
  candidate_target_id: string | null
  candidate_id: string | null
  baseline_run_id: string | null
  rerun_scope: 'all' | 'failed_only'
  retry_source_run_id: string | null
  reused_cases: number
  rerun_cases: number
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  attempt_count: number
  last_error: string
  completed_cases: number
  total_cases: number
  can_cancel: boolean
  can_delete: boolean
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type DashboardReport = {
  run_id: string
  mode: 'baseline' | 'comparison'
  rerun_scope: 'all' | 'failed_only'
  retry_source_run_id: string | null
  reused_cases: number
  rerun_cases: number
  gate_status: 'go' | 'no_go' | 'pending_review'
  pass_rates: Record<RiskLevel, number>
  risk_coverage: Record<RiskLevel, { assessed: number; total: number }>
  score_coverage: { baseline: number; candidate: number; total: number }
  score_dimensions: Record<string, { baseline: number; candidate: number }>
  p95_latency_ms: number
  average_cost_usd: number | null
  failed_cases: number
  passed_cases: number
  manual_review_cases: number
  execution_error_cases: number
}

export type EvaluationReportDetail = DashboardReport & {
  evidence_digest: string
  narrative: string[]
  review_cases: ReviewCase[]
  case_assessments: CaseAssessment[]
}

export type ReleaseDecision = {
  id: string
  run_id: string
  decision: 'approved' | 'rejected'
  rationale: string
  actor_id: string
  created_at: string
  is_current: boolean
}

export type ReleaseHandoff = {
  id: string
  decision_id: string
  action: 'publish' | 'rollback'
  dify_app_id: string
  dify_console_url: string
  candidate_app_id: string
  candidate_console_url: string
  source_workflow_id: string
  published_workflow_id: string
  status: 'awaiting_dify' | 'human_reported'
  notes: string
  actor_id: string
  confirmed_by: string
  created_at: string
  confirmed_at: string | null
  is_decision_current: boolean
  verification: 'not_verified_by_dify'
}

