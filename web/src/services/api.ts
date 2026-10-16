import { createAlova } from 'alova'
import GlobalFetch from 'alova/fetch'
import VueHook from 'alova/vue'

import type {
  CandidateGenerationConfig,
  CandidateGenerationJob,
  DashboardReport,
  Dataset,
  DatasetCaseInput,
  DatasetDetail,
  DifyTarget,
  DifyTargetCreatePayload,
  DifyTargetDetail,
  DifyTargetUpdatePayload,
  EvaluationReportDetail,
  EvaluationRun,
  PromptCandidate,
  PromptCandidateDetail,
  ReviewCase,
  ReviewCaseDetail,
  ReviewAISuggestion,
  RiskLevel,
  ReleaseDecision,
  ReleaseHandoff,
  SessionIdentity,
} from '@/types'

let csrfToken = ''

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
    this.name = 'ApiError'
  }
}

const client = createAlova({
  baseURL: '/api',
  statesHook: VueHook,
  requestAdapter: GlobalFetch(),
  beforeRequest(method) {
    if (csrfToken && method.type !== 'GET' && method.type !== 'HEAD') {
      method.config.headers = { ...method.config.headers, 'X-CSRF-Token': csrfToken }
    }
  },
  responded: {
    onSuccess: async (response) => {
      const body = (await response.json().catch(() => ({}))) as unknown
      if (!response.ok) {
        if (response.status === 401) {
          const hadSession = csrfToken.length > 0
          csrfToken = ''
          if (hadSession) {
            window.dispatchEvent(new Event('autope:identity-changed'))
          }
        } else if (response.status === 403 && csrfToken) {
          window.dispatchEvent(new Event('autope:identity-changed'))
        }
        const detail =
          typeof body === 'object' && body !== null && 'detail' in body && typeof body.detail === 'string'
            ? body.detail
            : '请求失败，请稍后重试。'
        throw new ApiError(detail, response.status)
      }
      return body
    },
  },
})

type DatasetPayload = { name: string; description: string; case_count: number }
type CandidatePayload = {
  name: string
  base_version_fingerprint: string
  optimizable_prompt_block: string
  bound_target_id: string | null
}
type CandidateGenerationPayload = {
  dataset_id: string
  baseline_target_id: string
  authorize_external_transfer: boolean
  consented_model: string
  consented_endpoint: string
}
type EvaluationPayload = {
  dataset_id: string
  baseline_target_id: string
  mode: 'baseline' | 'comparison'
  candidate_id: string | null
  baseline_run_id: string | null
  rerun_scope: 'all' | 'failed_only'
  retry_source_run_id: string | null
  idempotency_key: string
}
type ReviewPayload = {
  business_understanding: number
  clarification: number
  tool_use: number
  safety: number
  risk_level: RiskLevel
  rationale: string
  ai_suggestion_id?: string
  ai_action?: 'accept' | 'edit'
}

function evidenceHeaders(digest: string): { headers: { 'If-Match': string } } {
  if (!/^[0-9a-f]{64}$/.test(digest)) {
    throw new Error('当前证据版本无效，请刷新详情后重试。')
  }
  return { headers: { 'If-Match': `"${digest}"` } }
}

export function getSession(): Promise<SessionIdentity> {
  return client.Get<SessionIdentity>('/session', { cacheFor: 0 }).send().then((session) => {
    csrfToken = typeof session.csrf_token === 'string' ? session.csrf_token : ''
    return session
  })
}

export function loginLocal(loginName: string, password: string): Promise<{ status: string }> {
  return client.Post<{ status: string }>('/login', { login_name: loginName, password }).send()
}

export function logoutLocal(): Promise<void> {
  return client.Post<void>('/logout').send().then(() => { csrfToken = '' })
}

export function changeOwnPassword(currentPassword: string, newPassword: string): Promise<void> {
  return client.Post<void>('/password/change', {
    current_password: currentPassword,
    new_password: newPassword,
  }).send().then(() => { csrfToken = '' })
}

export function resetPassword(resetToken: string, newPassword: string): Promise<void> {
  return client.Post<void>('/password/reset', {
    reset_token: resetToken,
    new_password: newPassword,
  }).send()
}

export type PlatformAccount = {
  id: string
  login_name: string
  display_name: string
  status: 'active' | 'disabled'
  is_platform_admin: boolean
  roles: string[]
}

export type CreatePlatformAccountPayload = {
  login_name: string
  display_name: string
  roles: string[]
  is_platform_admin: boolean
}

export function listPlatformAccounts(): Promise<PlatformAccount[]> {
  return client.Get<PlatformAccount[]>('/accounts', { cacheFor: 0 }).send()
}

export function createPlatformAccount(payload: CreatePlatformAccountPayload): Promise<{ account_id: string; invite_token: string }> {
  return client.Post<{ account_id: string; invite_token: string }>('/accounts', payload).send()
}

export function updatePlatformAccount(accountId: string, payload: Partial<Pick<PlatformAccount, 'display_name' | 'status' | 'is_platform_admin' | 'roles'>>): Promise<PlatformAccount> {
  return client.Patch<PlatformAccount>(`/accounts/${encodeURIComponent(accountId)}`, payload).send()
}

export function issuePlatformResetToken(accountId: string): Promise<{ reset_token: string }> {
  return client.Post<{ reset_token: string }>(`/accounts/${encodeURIComponent(accountId)}/reset-token`).send()
}

export type BootstrapAdminPayload = {
  bootstrap_token: string
  login_name: string
  display_name: string
  password: string
}

export function getSetupStatus(): Promise<{ initialized: boolean }> {
  return client.Get<{ initialized: boolean }>('/setup/status', { cacheFor: 0 }).send()
}

export function createInitialAdmin(payload: BootstrapAdminPayload): Promise<{ account_id: string }> {
  return client.Post<{ account_id: string }>('/setup', payload).send()
}

export function listTargets(): Promise<DifyTarget[]> {
  return client.Get<DifyTarget[]>('/targets').send()
}

export function createTarget(payload: DifyTargetCreatePayload): Promise<DifyTarget> {
  return client.Post<DifyTarget>('/targets', payload).send()
}

export function getTarget(targetId: string): Promise<DifyTargetDetail> {
  return client.Get<DifyTargetDetail>(`/targets/${encodeURIComponent(targetId)}`).send()
}

export function updateTarget(targetId: string, payload: DifyTargetUpdatePayload): Promise<DifyTarget> {
  return client.Patch<DifyTarget>(`/targets/${encodeURIComponent(targetId)}`, payload).send()
}

export function deleteTarget(targetId: string): Promise<void> {
  return client.Delete<void>(`/targets/${encodeURIComponent(targetId)}`).send()
}

export function listDatasets(): Promise<Dataset[]> {
  return client.Get<Dataset[]>('/datasets').send()
}

export function createDataset(payload: DatasetPayload): Promise<Dataset> {
  return client.Post<Dataset>('/datasets', payload).send()
}

export function getDataset(datasetId: string): Promise<DatasetDetail> {
  return client.Get<DatasetDetail>(`/datasets/${encodeURIComponent(datasetId)}`).send()
}

export function uploadDatasetCases(datasetId: string, cases: DatasetCaseInput[]): Promise<DatasetDetail> {
  return client.Post<DatasetDetail>(`/datasets/${encodeURIComponent(datasetId)}/cases`, { cases }).send()
}

export function freezeDataset(datasetId: string): Promise<DatasetDetail> {
  return client.Post<DatasetDetail>(`/datasets/${encodeURIComponent(datasetId)}/freeze`).send()
}

export function approveDataset(datasetId: string, evidenceDigest: string): Promise<DatasetDetail> {
  return client.Post<DatasetDetail>(`/datasets/${encodeURIComponent(datasetId)}/approve`, {}, evidenceHeaders(evidenceDigest)).send()
}

export function listReviewCases(): Promise<ReviewCase[]> {
  return client.Get<ReviewCase[]>('/review-cases', { cacheFor: 0 }).send()
}

export function labelReviewCase(caseId: string, payload: ReviewPayload): Promise<ReviewCase> {
  return client.Post<ReviewCase>(`/review-cases/${encodeURIComponent(caseId)}/label`, payload).send()
}

export function reopenReviewCase(caseId: string, reason: string): Promise<ReviewCase> {
  return client.Post<ReviewCase>(`/review-cases/${encodeURIComponent(caseId)}/reopen`, { reason }).send()
}

export function getReviewCase(caseId: string): Promise<ReviewCaseDetail> {
  return client.Get<ReviewCaseDetail>(`/review-cases/${encodeURIComponent(caseId)}`, { cacheFor: 0 }).send()
}

export function getReviewSuggestionConfig(): Promise<{ model: string; endpoint: string; available: boolean }> {
  return client.Get<{ model: string; endpoint: string; available: boolean }>('/review-suggestions/config').send()
}

export function queueReviewSuggestions(runId: string, model: string, endpoint: string): Promise<ReviewAISuggestion[]> {
  return client.Post<ReviewAISuggestion[]>(`/evaluation-runs/${encodeURIComponent(runId)}/review-suggestions`, {
    authorize_external_transfer: true,
    consented_model: model,
    consented_endpoint: endpoint,
  }).send()
}

export function retryFailedReviewSuggestions(runId: string, model: string, endpoint: string, additionalAuthorizedCalls: number): Promise<ReviewAISuggestion[]> {
  return client.Post<ReviewAISuggestion[]>(`/evaluation-runs/${encodeURIComponent(runId)}/review-suggestions/retry-failed`, {
    authorize_external_transfer: true,
    consented_model: model,
    consented_endpoint: endpoint,
    additional_authorized_calls: additionalAuthorizedCalls,
  }).send()
}

export function listCandidates(): Promise<PromptCandidate[]> {
  return client.Get<PromptCandidate[]>('/candidates').send()
}

export function getCandidate(candidateId: string): Promise<PromptCandidateDetail> {
  return client.Get<PromptCandidateDetail>(`/candidates/${encodeURIComponent(candidateId)}`).send()
}

export function createCandidate(payload: CandidatePayload): Promise<PromptCandidate> {
  return client.Post<PromptCandidate>('/candidates', payload).send()
}

export function generateCandidate(payload: CandidateGenerationPayload): Promise<CandidateGenerationJob> {
  return client.Post<CandidateGenerationJob>('/candidates/generate', payload).send()
}

export function listCandidateGenerationJobs(): Promise<CandidateGenerationJob[]> {
  return client.Get<CandidateGenerationJob[]>('/candidates/generation-jobs').send()
}

export function getCandidateGenerationConfig(): Promise<CandidateGenerationConfig> {
  return client.Get<CandidateGenerationConfig>('/candidates/generation-config').send()
}

export function cancelCandidateGenerationJob(jobId: string): Promise<CandidateGenerationJob> {
  return client.Post<CandidateGenerationJob>(`/candidates/generation-jobs/${encodeURIComponent(jobId)}/cancel`, {}).send()
}

export function bindCandidate(candidateId: string, boundTargetId: string): Promise<PromptCandidate> {
  return client.Patch<PromptCandidate>(`/candidates/${encodeURIComponent(candidateId)}/binding`, {
    bound_target_id: boundTargetId,
  }).send()
}

export function attestCandidateBinding(candidateId: string, payload: { published_workflow_id: string; published_prompt_block: string; review_note: string; human_confirmed: boolean }): Promise<PromptCandidateDetail> {
  return client.Post<PromptCandidateDetail>(`/candidates/${encodeURIComponent(candidateId)}/binding-attestation`, payload).send()
}

export function listRuns(): Promise<EvaluationRun[]> {
  return client.Get<EvaluationRun[]>('/evaluation-runs').send()
}

export function createRun(payload: EvaluationPayload): Promise<EvaluationRun> {
  return client.Post<EvaluationRun>('/evaluation-runs', payload).send()
}

export function cancelRun(runId: string): Promise<EvaluationRun> {
  return client.Post<EvaluationRun>(`/evaluation-runs/${encodeURIComponent(runId)}/cancel`, {}).send()
}

export function deleteRun(runId: string): Promise<void> {
  return client.Delete<void>(`/evaluation-runs/${encodeURIComponent(runId)}`).send()
}

export function getDashboard(runId: string): Promise<DashboardReport> {
  return client.Get<DashboardReport>(`/evaluation-runs/${encodeURIComponent(runId)}/dashboard`).send()
}

export function getReportDetail(runId: string): Promise<EvaluationReportDetail> {
  // This report includes live AI-draft progress and human review state.
  return client.Get<EvaluationReportDetail>(`/evaluation-runs/${encodeURIComponent(runId)}/report`, { cacheFor: 0 }).send()
}

export function listReleaseDecisions(): Promise<ReleaseDecision[]> {
  return client.Get<ReleaseDecision[]>('/release-decisions').send()
}

export function createReleaseDecision(payload: { run_id: string; decision: 'approved' | 'rejected'; rationale: string; idempotency_key: string }, evidenceDigest: string): Promise<ReleaseDecision> {
  return client.Post<ReleaseDecision>('/release-decisions', payload, evidenceHeaders(evidenceDigest)).send()
}

export function listReleaseHandoffs(): Promise<ReleaseHandoff[]> {
  return client.Get<ReleaseHandoff[]>('/release-handoffs').send()
}

export function createReleaseHandoff(payload: { decision_id: string; action: 'publish' | 'rollback'; source_workflow_id: string; idempotency_key: string }, evidenceDigest: string): Promise<ReleaseHandoff> {
  return client.Post<ReleaseHandoff>('/release-handoffs', payload, evidenceHeaders(evidenceDigest)).send()
}

export function confirmReleaseHandoff(handoffId: string, payload: { published_workflow_id: string; notes: string; human_confirmed: boolean }, evidenceDigest: string): Promise<ReleaseHandoff> {
  return client.Post<ReleaseHandoff>(`/release-handoffs/${encodeURIComponent(handoffId)}/confirm`, payload, evidenceHeaders(evidenceDigest)).send()
}

