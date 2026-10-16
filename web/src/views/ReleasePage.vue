<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { showConfirmDialog } from 'vant'

import PageHeader from '@/components/PageHeader.vue'
import { ApiError, confirmReleaseHandoff, createReleaseDecision, createReleaseHandoff, getReportDetail, listReleaseDecisions, listReleaseHandoffs, listRuns } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import { formatBackendTime } from '@/services/time'
import type { DashboardReport, EvaluationRun, ReleaseDecision, ReleaseHandoff } from '@/types'

const runs = ref<EvaluationRun[]>([])
const canApprove = computed(() => activeSession.value?.allowed_actions.includes('approvals.manage') ?? false)
const dashboards = ref<Record<string, DashboardReport>>({})
const reportDigests = reactive<Record<string, string>>({})
const staleReportIds = ref<string[]>([])
const evidenceReadFailed = ref(false)
const decisions = ref<ReleaseDecision[]>([])
const handoffs = ref<ReleaseHandoff[]>([])
const errorMessage = ref('')
const successMessage = ref('')
const isSubmitting = ref(false)
const form = reactive({ runId: '', decision: 'approved' as 'approved' | 'rejected', rationale: '' })
const pendingIdempotencyKey = ref('')
const pendingHandoffKey = ref('')
const isCreatingHandoff = ref(false)
const confirmingHandoffId = ref('')
const handoffForm = reactive({ decisionId: '', action: 'publish' as 'publish' | 'rollback', sourceWorkflowId: '' })
const confirmationForms = reactive<Record<string, { publishedWorkflowId: string; notes: string; humanConfirmed: boolean }>>({})
const comparisonRuns = computed(() => runs.value.filter((item) => item.status === 'completed' && item.mode === 'comparison'))
const selectedDashboard = computed(() => dashboards.value[form.runId])
const approvedDecisions = computed(() => decisions.value.filter((item) => item.decision === 'approved'))
const selectedHandoffDecision = computed(() => approvedDecisions.value.find((item) => item.id === handoffForm.decisionId))

function isReleaseEligible(report: DashboardReport | undefined): boolean {
  return Boolean(report && report.gate_status === 'go' && report.failed_cases === 0 && report.manual_review_cases === 0 && report.execution_error_cases === 0 && Object.values(report.risk_coverage).some((group) => group.total > 0) && Object.values(report.risk_coverage).every((group) => group.assessed === group.total))
}

function submissionError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if ([412, 428].includes(error.status)) return '报告证据版本已变化，请重新读取并核对后再提交。'
    if (error.status === 403) return '当前账号无权执行此操作；权限变化后不会自动重试。'
    if (error.status === 401) return '登录已失效；表单仍保留在本页，请重新登录并核对证据。'
  }
  return error instanceof Error ? error.message : fallback
}

watch(() => [form.runId, form.decision, form.rationale], () => { pendingIdempotencyKey.value = '' })
watch(() => [handoffForm.decisionId, handoffForm.action, handoffForm.sourceWorkflowId], () => { pendingHandoffKey.value = '' })

async function loadData(): Promise<void> {
  try {
    const [runItems, decisionItems, handoffItems] = await Promise.all([listRuns(), listReleaseDecisions(), listReleaseHandoffs()])
    const completedComparisons = runItems.filter((item) => item.status === 'completed' && item.mode === 'comparison')
    const reports = await Promise.all(completedComparisons.map(async (item) => [item.id, await getReportDetail(item.id)] as const))
    for (const [runId, report] of reports) {
      if (!reportDigests[runId]) {
        reportDigests[runId] = report.evidence_digest
      } else if (reportDigests[runId] !== report.evidence_digest && !staleReportIds.value.includes(runId)) {
        staleReportIds.value = [...staleReportIds.value, runId]
      }
    }
    runs.value = runItems
    decisions.value = decisionItems
    handoffs.value = handoffItems
    dashboards.value = Object.fromEntries(reports)
    evidenceReadFailed.value = false
    if (!completedComparisons.some((item) => item.id === form.runId)) form.runId = completedComparisons.find((item) => isReleaseEligible(dashboards.value[item.id]))?.id ?? completedComparisons[0]?.id ?? ''
    if (!decisionItems.some((item) => item.id === handoffForm.decisionId && item.decision === 'approved')) handoffForm.decisionId = decisionItems.find((item) => item.decision === 'approved' && item.is_current)?.id ?? ''
    errorMessage.value = ''
  } catch {
    evidenceReadFailed.value = true
    errorMessage.value = '无法读取审批所需的评测报告与历史决策，请检查 API 服务。'
  }
}

async function refreshEvidence(runId: string): Promise<void> {
  try {
    const report = await getReportDetail(runId)
    reportDigests[runId] = report.evidence_digest
    staleReportIds.value = staleReportIds.value.filter((item) => item !== runId)
    pendingIdempotencyKey.value = ''
    pendingHandoffKey.value = ''
    errorMessage.value = ''
    successMessage.value = '已读取最新报告版本。请重新核对证据并再次确认提交。'
  } catch {
    errorMessage.value = '无法读取最新报告版本；请稍后重试，不要沿用旧审批。'
  }
}

async function submitDecision(): Promise<void> {
  if (isSubmitting.value || !form.runId || form.rationale.trim().length < 8 || (form.decision === 'approved' && !isReleaseEligible(selectedDashboard.value))) return
  const digest = reportDigests[form.runId]
  if (evidenceReadFailed.value || staleReportIds.value.includes(form.runId) || !digest) {
    errorMessage.value = '报告证据已变化或尚未加载，请重新读取版本后再确认。'
    return
  }
  try {
    await showConfirmDialog({
      title: form.decision === 'approved' ? '确认批准进入人工发布流程？' : '确认驳回候选？',
      message: '只记录审批与报告证据，不会向 Dify 发布、灰度或修改 Prompt。报告或标注改变后，该决策会显示为失效。',
      confirmButtonText: form.decision === 'approved' ? '记录批准' : '记录驳回',
    })
  } catch { return }
  isSubmitting.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const key = pendingIdempotencyKey.value || crypto.randomUUID()
  pendingIdempotencyKey.value = key
  try {
    const saved = await createReleaseDecision({ run_id: form.runId, decision: form.decision, rationale: form.rationale.trim(), idempotency_key: key }, digest)
    decisions.value = [saved, ...decisions.value.filter((item) => item.id !== saved.id)]
    pendingIdempotencyKey.value = ''
    form.rationale = ''
    notifyPlatformDataChanged('releases', 'reports', 'dashboard')
    successMessage.value = '人工决策已记录并审计。Dify 工作流未发生变更。可在下方创建发布交接。'
  } catch (error) {
    if (error instanceof ApiError && [412, 428].includes(error.status)) markEvidenceStale(form.runId)
    errorMessage.value = submissionError(error, '审批记录保存失败。')
  } finally { isSubmitting.value = false }
}

async function submitHandoff(): Promise<void> {
  if (isCreatingHandoff.value || !selectedHandoffDecision.value) return
  const runId = selectedHandoffDecision.value.run_id
  const digest = reportDigests[runId]
  if (evidenceReadFailed.value || staleReportIds.value.includes(runId) || !digest) {
    errorMessage.value = '交接对应的报告证据已变化，请重新读取版本并核对审批。'
    return
  }
  if (handoffForm.action === 'publish' && !selectedHandoffDecision.value.is_current) {
    errorMessage.value = '审批证据已失效，请重新评测和审批。'
    return
  }
  if (handoffForm.action === 'rollback' && !handoffForm.sourceWorkflowId.trim()) {
    errorMessage.value = '请从 Dify 版本历史填写要恢复的已发布版本 ID。'
    return
  }
  try {
    await showConfirmDialog({
      title: handoffForm.action === 'publish' ? '创建 Dify 发布交接？' : '创建 Dify 回滚交接？',
      message: handoffForm.action === 'publish'
        ? '仅创建交接记录。仍需有权限人员在 Dify 控制台检查草稿并亲自发布。'
        : 'Dify 恢复旧版只会覆盖草稿；还需亲自重新发布，才会影响在线版本。',
      confirmButtonText: '创建交接',
    })
  } catch { return }
  isCreatingHandoff.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const key = pendingHandoffKey.value || crypto.randomUUID()
  pendingHandoffKey.value = key
  try {
    const saved = await createReleaseHandoff({ decision_id: handoffForm.decisionId, action: handoffForm.action, source_workflow_id: handoffForm.action === 'rollback' ? handoffForm.sourceWorkflowId.trim() : '', idempotency_key: key }, digest)
    handoffs.value = [saved, ...handoffs.value.filter((item) => item.id !== saved.id)]
    pendingHandoffKey.value = ''
    handoffForm.sourceWorkflowId = ''
    notifyPlatformDataChanged('releases', 'dashboard')
    successMessage.value = '交接已创建。请打开对应 Dify 应用完成操作；本平台尚未修改工作流。'
  } catch (error) {
    if (error instanceof ApiError && [412, 428].includes(error.status)) markEvidenceStale(runId)
    errorMessage.value = submissionError(error, '创建交接失败。')
  } finally { isCreatingHandoff.value = false }
}

function confirmationForm(handoffId: string): { publishedWorkflowId: string; notes: string; humanConfirmed: boolean } {
  confirmationForms[handoffId] ??= { publishedWorkflowId: '', notes: '', humanConfirmed: false }
  return confirmationForms[handoffId]
}

async function confirmHandoff(handoff: ReleaseHandoff): Promise<void> {
  const runId = decisions.value.find((item) => item.id === handoff.decision_id)?.run_id ?? ''
  const digest = reportDigests[runId]
  if (!runId || evidenceReadFailed.value || staleReportIds.value.includes(runId) || !digest) {
    errorMessage.value = '交接对应的报告证据尚未核对或已变化，请刷新报告后再回填。'
    return
  }
  if (handoff.action === 'publish' && !handoff.is_decision_current) {
    errorMessage.value = '这份 GO 审批已失效，请停止发布并重新评测、审批；不能回填为已完成。'
    return
  }
  const values = confirmationForm(handoff.id)
  if (confirmingHandoffId.value || !values.humanConfirmed || values.notes.trim().length < 8 || !values.publishedWorkflowId.trim()) return
  try {
    await showConfirmDialog({
      title: '确认已在 Dify 发布？',
      message: '此处仅记录人工回填的版本 ID，不会自动查询 Dify 或验证线上流量。请先在 Dify 版本历史核对新发布版本。',
      confirmButtonText: '记录人工回填',
    })
  } catch { return }
  confirmingHandoffId.value = handoff.id
  errorMessage.value = ''
  try {
    const saved = await confirmReleaseHandoff(handoff.id, { published_workflow_id: values.publishedWorkflowId.trim(), notes: values.notes.trim(), human_confirmed: true }, digest)
    handoffs.value = handoffs.value.map((item) => item.id === saved.id ? saved : item)
    notifyPlatformDataChanged('releases', 'dashboard')
    successMessage.value = '版本已人工回填并写入审计；尚未通过 Dify API 自动核验。'
  } catch (error) {
    if (error instanceof ApiError && [412, 428].includes(error.status)) markEvidenceStale(runId)
    errorMessage.value = submissionError(error, '版本回填失败。')
  } finally { confirmingHandoffId.value = '' }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['reports', 'reviews', 'runs', 'releases'], refresh: loadData, pollIntervalMs: 8_000 })

function markEvidenceStale(runId: string): void {
  if (runId && !staleReportIds.value.includes(runId)) staleReportIds.value = [...staleReportIds.value, runId]
  pendingIdempotencyKey.value = ''
  pendingHandoffKey.value = ''
}

function onSessionRestored(): void {
  const activeRunIds = new Set<string>()
  if (form.runId) activeRunIds.add(form.runId)
  if (selectedHandoffDecision.value) activeRunIds.add(selectedHandoffDecision.value.run_id)
  for (const handoff of handoffs.value) {
    const values = confirmationForms[handoff.id]
    if (!values || (!values.publishedWorkflowId && !values.notes && !values.humanConfirmed)) continue
    const runId = decisions.value.find((item) => item.id === handoff.decision_id)?.run_id
    if (runId) activeRunIds.add(runId)
  }
  for (const runId of activeRunIds) markEvidenceStale(runId)
  void loadData()
}

onMounted(() => window.addEventListener('autope:session-restored', onSessionRestored))
onBeforeUnmount(() => window.removeEventListener('autope:session-restored', onSessionRestored))
</script>

<template>
  <PageHeader title="评测报告审批与发布交接" description="先读逐条自动证据和例外复核，再由审批人判断整份报告；只有完整 GO 报告可批准，发布仍由 Dify 控制台人工执行。" :is-refreshing="dataRefresh.isRefreshing" @refresh="dataRefresh.refresh" />
  <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
  <section v-if="canApprove && staleReportIds.length" class="chart-panel stale-evidence" role="status">
    <h2>报告证据需要重新核对</h2>
    <p>登录状态或报告版本已变化。原表单内容保留在本页，但旧证据摘要不可继续提交。请先打开报告核对，再手动读取当前版本并重新确认操作。</p>
    <div v-for="runId in staleReportIds" :key="runId" class="stale-evidence-row"><router-link class="detail-link" :to="{ name: 'report-detail', params: { runId } }">查看 {{ runId }} 的证据</router-link><van-button size="small" plain @click="refreshEvidence(runId)">已核对，读取当前版本</van-button></div>
  </section>
  <section class="split-layout" :class="{ 'read-only-layout': !canApprove }">
    <form v-if="canApprove" class="form-card" @submit.prevent="submitDecision">
      <h2>人工判断整份评测报告</h2>
      <label>候选对比评测<select v-model="form.runId" required><option v-for="run in comparisonRuns" :key="run.id" :value="run.id">{{ run.id }} · {{ isReleaseEligible(dashboards[run.id]) ? 'GO · 可批准' : dashboards[run.id]?.gate_status === 'pending_review' ? '待复核' : '不可批准' }}</option></select></label>
      <p v-if="selectedDashboard" class="gate-state" :class="isReleaseEligible(selectedDashboard) ? 'go' : selectedDashboard.gate_status">当前门禁：{{ isReleaseEligible(selectedDashboard) ? 'GO · 证据完整，可人工批准' : selectedDashboard.gate_status === 'pending_review' ? `待人工复核 ${selectedDashboard.manual_review_cases} 条` : '未满足完整判定与零失败要求，不可批准' }}</p>
      <fieldset class="decision-options"><legend>人工决策</legend><label><input v-model="form.decision" type="radio" value="approved" />批准进入人工发布流程</label><label><input v-model="form.decision" type="radio" value="rejected" />驳回候选</label></fieldset>
      <label>依据与后续动作<textarea v-model.trim="form.rationale" required minlength="8" maxlength="1000" placeholder="说明版本证据、业务风险和后续操作要求" /></label>
      <p class="helper-copy">请先查看右侧的证据报告。人工可以驳回 GO；未判定、调用错误或工具/安全硬失败不能靠批准按钮覆盖。批准只创建审计记录；Dify 实际发布、流量观察和回滚仍由有权限人员执行。</p>
      <van-button native-type="submit" type="primary" block :loading="isSubmitting" :disabled="isSubmitting || !form.runId || form.rationale.trim().length < 8 || (form.decision === 'approved' && !isReleaseEligible(selectedDashboard))">记录人工决策</van-button>
      <p v-if="successMessage" class="success-message" aria-live="polite">{{ successMessage }}</p>
    </form>
    <section class="chart-panel release-runs"><h2>候选对比门禁</h2><div v-if="comparisonRuns.length" class="data-list"><article v-for="run in comparisonRuns" :key="run.id" class="row-card"><div><h3>{{ run.id }}</h3><p>{{ isReleaseEligible(dashboards[run.id]) ? 'GO · 可人工批准' : dashboards[run.id]?.gate_status === 'pending_review' ? '待复核 · 不可批准' : '门禁或证据不完整 · 不可批准' }}</p></div><router-link class="detail-link" :to="{ name: 'report-detail', params: { runId: run.id } }">查看证据报告</router-link></article></div><p v-else class="helper-copy">暂无已完成的候选对比评测。</p></section>
  </section>
  <section class="chart-panel decision-history"><h2>决策审计记录</h2><div v-if="decisions.length" class="data-list"><article v-for="item in decisions" :key="item.id" class="row-card"><div><h3>{{ item.run_id }}</h3><p>{{ item.decision === 'approved' ? '批准进入人工发布流程' : '驳回候选' }} · {{ item.is_current ? '证据仍有效' : '报告或标注已变化，此决策失效' }} · {{ formatBackendTime(item.created_at) }}</p><p>{{ item.rationale }}</p></div><span class="decision-state" :class="{ stale: !item.is_current }">{{ item.is_current ? '有效记录' : '已失效' }}</span></article></div><p v-else class="helper-copy">尚无人工发布决策；待 GO 报告形成后再记录。</p></section>
  <section v-if="canApprove" class="chart-panel handoff-panel" aria-label="Dify 操作交接">
    <h2>连接 Dify 人工操作</h2>
    <p class="helper-copy">发布：从隔离候选应用核对 Prompt，先检查并保留基线应用现有草稿改动，再把获批内容应用到基线草稿并发布。回滚：在基线应用版本历史恢复旧版到草稿，然后重新发布。两种操作都要回填基线应用“新发布版本 ID”；本平台不持有 Dify 控制台会话。</p>
    <form class="handoff-form" @submit.prevent="submitHandoff">
      <label>已批准的评测<select v-model="handoffForm.decisionId" required><option value="">选择有效审批</option><option v-for="decision in approvedDecisions" :key="decision.id" :value="decision.id">{{ decision.run_id }} · {{ decision.is_current ? '有效' : '已失效' }}</option></select></label>
      <label>操作类型<select v-model="handoffForm.action"><option value="publish">发布候选</option><option value="rollback">回滚至旧版</option></select></label>
      <label v-if="handoffForm.action === 'rollback'">要恢复的旧版工作流 ID<input v-model.trim="handoffForm.sourceWorkflowId" required placeholder="从 Dify 版本历史复制 UUID" /></label>
      <van-button native-type="submit" type="primary" :loading="isCreatingHandoff" :disabled="!selectedHandoffDecision || (handoffForm.action === 'publish' && !selectedHandoffDecision.is_current) || isCreatingHandoff">创建 Dify 交接</van-button>
    </form>
    <p v-if="!approvedDecisions.length" class="helper-copy">尚无 GO 且已批准的候选，不能创建发布交接。</p>
  </section>
  <section class="chart-panel handoff-history"><h2>Dify 版本交接记录</h2><div v-if="handoffs.length" class="data-list"><article v-for="handoff in handoffs" :key="handoff.id" class="row-card handoff-card"><div><h3>{{ handoff.action === 'publish' ? '候选发布' : '旧版回滚' }} · 基线应用 {{ handoff.dify_app_id }}</h3><p>{{ handoff.status === 'awaiting_dify' ? '等待 Dify 控制台操作' : '已人工回填 · 未经 Dify API 核验' }} · {{ formatBackendTime(handoff.created_at) }}</p><p>隔离候选应用：{{ handoff.candidate_app_id }}</p><p v-if="handoff.source_workflow_id">待恢复的基线旧版：{{ handoff.source_workflow_id }}</p><p v-if="handoff.published_workflow_id">人工报告的基线新发布版本：{{ handoff.published_workflow_id }}</p><p v-if="handoff.notes">操作记录：{{ handoff.notes }}</p><p v-if="!handoff.is_decision_current" class="error-message">原审批证据已失效。候选发布交接已锁定；如需紧急回滚，请按独立回滚流程处理。</p><a class="detail-link" :href="handoff.candidate_console_url" target="_blank" rel="noopener noreferrer">查看隔离候选来源</a><a class="detail-link" :href="handoff.dify_console_url" target="_blank" rel="noopener noreferrer">打开待发布的基线应用</a></div><form v-if="canApprove && handoff.status === 'awaiting_dify' && (handoff.action === 'rollback' || handoff.is_decision_current)" class="handoff-confirm" @submit.prevent="confirmHandoff(handoff)"><label>基线应用新发布版本 ID<input v-model.trim="confirmationForm(handoff.id).publishedWorkflowId" required placeholder="基线应用版本历史中的新 UUID" /></label><label>操作和观察记录<textarea v-model.trim="confirmationForm(handoff.id).notes" required minlength="8" maxlength="1000" placeholder="说明发布/回滚步骤、观察结果及后续负责人" /></label><label class="confirm-check"><input v-model="confirmationForm(handoff.id).humanConfirmed" type="checkbox" />我已在基线 Dify 应用完成发布并核对版本历史</label><van-button native-type="submit" size="small" type="primary" :loading="confirmingHandoffId === handoff.id" :disabled="!confirmationForm(handoff.id).humanConfirmed || confirmingHandoffId === handoff.id">回填版本记录</van-button></form></article></div><p v-else class="helper-copy">暂无发布或回滚交接。创建交接不会自动修改 Dify。</p></section>
</template>

<style scoped>
.decision-options { display: flex; flex-wrap: wrap; gap: 12px 20px; margin: 0; padding: 0; border: 0; }
.stale-evidence { margin-bottom: 18px; border: 1px solid #e3bf7d; }.stale-evidence h2 { margin: 0 0 8px; font-size: 17px; }.stale-evidence p { margin: 0 0 10px; line-height: 1.6; }.stale-evidence-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.decision-options legend { margin-bottom: 7px; color: #4e4665; font-size: 13px; font-weight: 700; }
.decision-options label { display: flex; gap: 6px; align-items: center; }
.decision-options input { width: auto; }
.gate-state { margin: 0; padding: 10px 12px; border-radius: 9px; color: #fff; background: var(--orchid-deep); font-size: 13px; }
.gate-state.pending_review { background: #8a602f; }.gate-state.no_go { background: #b13a65; }
.release-runs { margin-top: 0; }.release-runs h3, .decision-history h3 { margin: 0; font-size: 15px; overflow-wrap: anywhere; }
.release-runs .row-card { align-items: center; box-shadow: none; background: rgba(247, 245, 255, .8); }
.decision-state { flex: 0 0 auto; align-self: center; color: #087a64; font-size: 12px; font-weight: 700; }.decision-state.stale { color: var(--error); }
.handoff-panel { margin-top: 21px; }
.handoff-form { display: flex; flex-wrap: wrap; align-items: end; gap: 14px; margin-top: 18px; }
.handoff-form label { min-width: min(100%, 230px); flex: 1; }
.handoff-history { margin-top: 21px; }
.handoff-card { align-items: flex-start; }
.handoff-card > div { min-width: 0; overflow-wrap: anywhere; }
.handoff-card h3 { margin: 0; font-size: 15px; }
.handoff-confirm { display: grid; min-width: min(100%, 300px); gap: 10px; }
.confirm-check { display: flex; gap: 8px; align-items: flex-start; line-height: 1.5; }
.confirm-check input { width: auto; margin-top: 3px; }
@media (max-width: 620px) { .decision-state { align-self: flex-start; } }
</style>
