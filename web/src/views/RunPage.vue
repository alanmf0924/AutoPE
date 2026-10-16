<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { showConfirmDialog } from 'vant'

import PageHeader from '@/components/PageHeader.vue'
import { cancelRun, createRun, deleteRun, getDataset, listCandidates, listDatasets, listRuns, listTargets } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import { formatBackendTime, parseBackendTime } from '@/services/time'
import type { Dataset, DatasetDetail, DifyTarget, EvaluationRun, PromptCandidate } from '@/types'

const targets = ref<DifyTarget[]>([])
const canManageRuns = computed(() => activeSession.value?.allowed_actions.includes('evaluations.run') ?? false)
const datasets = ref<Dataset[]>([])
const selectedDatasetDetail = ref<DatasetDetail>()
const candidates = ref<PromptCandidate[]>([])
const runs = ref<EvaluationRun[]>([])
const errorMessage = ref('')
const successMessage = ref('')
const isSubmitting = ref(false)
const actioningRunId = ref<string | null>(null)
const pendingIdempotencyKey = ref('')
const nowMs = ref(Date.now())
let clockTimer: number | undefined
const form = reactive({ mode: 'baseline' as 'baseline' | 'comparison', datasetId: '', baselineTargetId: '', candidateId: '', baselineRunId: '', rerunScope: 'all' as 'all' | 'failed_only', retrySourceRunId: '' })
const readyCandidates = computed(() => candidates.value.filter((candidate) => candidate.status === 'ready_for_evaluation' && candidate.bound_target_id && candidate.binding_attested))
const reusableBaselineRuns = computed(() => runs.value.filter((run) =>
  run.mode === 'baseline' && run.status === 'completed'
  && run.dataset_id === form.datasetId && run.baseline_target_id === form.baselineTargetId,
))
const selectedBaseline = computed(() => targets.value.find((target) => target.id === form.baselineTargetId))
const selectedCandidateTarget = computed(() => {
  const candidate = readyCandidates.value.find((item) => item.id === form.candidateId)
  return targets.value.find((target) => target.id === candidate?.bound_target_id)
})
const reusableRetryRuns = computed(() => runs.value.filter((run) =>
  run.status === 'completed' && run.mode === form.mode
  && run.dataset_id === form.datasetId && run.baseline_target_id === form.baselineTargetId
  && run.candidate_id === (form.mode === 'comparison' ? form.candidateId : null)
  && run.baseline_run_id === (form.mode === 'comparison' ? form.baselineRunId : null),
))
const canRetryFailedOnly = computed(() => Boolean(
  selectedBaseline.value?.published_workflow_id
  && (form.mode === 'baseline' || selectedCandidateTarget.value?.published_workflow_id)
  && reusableRetryRuns.value.length,
))
async function refreshSelectedDataset(): Promise<void> {
  const selectedId = form.datasetId
  if (!selectedId) {
    selectedDatasetDetail.value = undefined
    return
  }
  try {
    const detail = await getDataset(selectedId)
    if (form.datasetId === selectedId) {
      selectedDatasetDetail.value = detail
    }
  } catch {
    if (form.datasetId === selectedId) {
      selectedDatasetDetail.value = undefined
    }
  }
}
watch(() => form.datasetId, () => { void refreshSelectedDataset() })
watch(reusableRetryRuns, (availableRuns) => {
  if (!availableRuns.some((run) => run.id === form.retrySourceRunId)) {
    form.retrySourceRunId = availableRuns[0]?.id ?? ''
  }
})

async function loadData(): Promise<void> {
  const [targetItems, datasetItems, candidateItems, runItems] = await Promise.all([
    listTargets(),
    listDatasets(),
    listCandidates(),
    listRuns(),
  ])
  targets.value = targetItems
  datasets.value = datasetItems
  candidates.value = candidateItems
  runs.value = runItems
  const hasSelectedDataset = datasetItems.some((dataset) => dataset.id === form.datasetId)
  const hasSelectedBaseline = targetItems.some((target) => target.id === form.baselineTargetId)
  const hasSelectedCandidate = readyCandidates.value.some((candidate) => candidate.id === form.candidateId)
  if (!hasSelectedDataset) {
    form.datasetId = datasetItems[0]?.id ?? ''
  }
  if (!hasSelectedBaseline) {
    form.baselineTargetId = targetItems.find((target) => !target.version_fingerprint.includes('@candidate'))?.id ?? ''
  }
  if (!hasSelectedCandidate) {
    form.candidateId = readyCandidates.value[0]?.id ?? ''
  }
  if (!reusableBaselineRuns.value.some((run) => run.id === form.baselineRunId)) {
    form.baselineRunId = reusableBaselineRuns.value[0]?.id ?? ''
  }
  if (!reusableRetryRuns.value.some((run) => run.id === form.retrySourceRunId)) {
    form.retrySourceRunId = reusableRetryRuns.value[0]?.id ?? ''
  }
  await refreshSelectedDataset()
}

async function submitRun(): Promise<void> {
  if (isSubmitting.value) {
    return
  }
  isSubmitting.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const idempotencyKey = pendingIdempotencyKey.value || crypto.randomUUID()
  pendingIdempotencyKey.value = idempotencyKey
  try {
    const run = await createRun({
      dataset_id: form.datasetId,
      baseline_target_id: form.baselineTargetId,
      mode: form.mode,
      candidate_id: form.mode === 'comparison' ? form.candidateId : null,
      baseline_run_id: form.mode === 'comparison' ? form.baselineRunId : null,
      rerun_scope: form.rerunScope,
      retry_source_run_id: form.rerunScope === 'failed_only' ? form.retrySourceRunId : null,
      idempotency_key: idempotencyKey,
    })
    runs.value = [run, ...runs.value]
    notifyPlatformDataChanged('runs', 'reports', 'reviews', 'dashboard')
    pendingIdempotencyKey.value = ''
    successMessage.value = run.rerun_scope === 'failed_only'
      ? `复测 ${run.id} 已排队：沿用 ${run.reused_cases} 条已通过证据，实际重测 ${run.rerun_cases} 条。`
      : run.mode === 'baseline' ? `基线评测 ${run.id} 已排队。` : `DSPy 候选对比 ${run.id} 已排队。`
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '无法创建评测，请检查测试集、目标版本与来源运行。'
  } finally {
    isSubmitting.value = false
  }
}

async function refreshRuns(): Promise<void> {
  try {
    await loadData()
  } catch {
    errorMessage.value = '无法读取评测运行所需数据。请检查后端服务。'
  }
}

function statusLabel(status: EvaluationRun['status']): string {
  const labels: Record<EvaluationRun['status'], string> = {
    queued: '排队中',
    running: '运行中',
    completed: '已完成',
    failed: '执行失败',
    cancelled: '已中断',
  }
  return labels[status]
}

function progressLabel(run: EvaluationRun): string {
  const total = run.total_cases
  const completed = run.completed_cases
  if (run.status === 'queued') {
    return `等待 Worker · ${completed}/${total}`
  }
  return `已完成 ${completed}/${total} 条${total > 0 ? ` · ${Math.round(completed / total * 100)}%` : ''}`
}

function durationLabel(run: EvaluationRun): string {
  const start = parseBackendTime(run.started_at ?? run.created_at)
  const end = run.completed_at ? parseBackendTime(run.completed_at) : nowMs.value
  if (!Number.isFinite(start) || !Number.isFinite(end)) return '耗时未知'
  const seconds = Math.max(0, Math.floor((end - start) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor(seconds % 3600 / 60)
  const remain = seconds % 60
  const elapsed = `${hours ? `${hours} 小时 ` : ''}${minutes ? `${minutes} 分 ` : ''}${remain} 秒`
  if (!run.started_at) return `排队等待 ${elapsed}`
  return `${run.completed_at ? '运行耗时' : '已运行'} ${elapsed}`
}

onMounted(() => { clockTimer = window.setInterval(() => { nowMs.value = Date.now() }, 1_000) })
onBeforeUnmount(() => { if (clockTimer !== undefined) window.clearInterval(clockTimer) })

function replaceRun(updatedRun: EvaluationRun): void {
  runs.value = runs.value.map((run) => run.id === updatedRun.id ? updatedRun : run)
}

async function cancelEvaluation(run: EvaluationRun): Promise<void> {
  if (!run.can_cancel || actioningRunId.value) {
    return
  }
  actioningRunId.value = run.id
  errorMessage.value = ''
  try {
    const updatedRun = await cancelRun(run.id)
    replaceRun(updatedRun)
    successMessage.value = `已中断 ${run.id}；正在执行的当前一条 Dify 调用结束后不会继续下一条。`
    notifyPlatformDataChanged('runs', 'reports', 'reviews', 'dashboard')
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '无法中断评测，请稍后重试。'
  } finally {
    actioningRunId.value = null
  }
}

async function removeEvaluation(run: EvaluationRun): Promise<void> {
  if (!run.can_delete || actioningRunId.value) {
    return
  }
  try {
    await showConfirmDialog({
      title: '删除已中断评测？',
      message: '将删除该运行产生的结果、报告和未标注审核项。已完成或已人工标注的证据不能删除。',
      confirmButtonText: '删除运行',
      confirmButtonColor: 'var(--error)',
    })
  } catch {
    return
  }
  actioningRunId.value = run.id
  errorMessage.value = ''
  try {
    await deleteRun(run.id)
    runs.value = runs.value.filter((item) => item.id !== run.id)
    successMessage.value = `已删除 ${run.id}。`
    notifyPlatformDataChanged('runs', 'reports', 'reviews', 'dashboard')
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '无法删除评测记录，请稍后重试。'
  } finally {
    actioningRunId.value = null
  }
}

const dataRefresh = usePlatformDataRefresh({
  resources: ['runs', 'targets', 'datasets', 'candidates'],
  refresh: refreshRuns,
  pollIntervalMs: 3_000,
})
</script>

<template>
  <PageHeader
    title="创建可复现的评测"
    description="基线回归只测一个 Dify 目标；候选对比使用独立克隆应用中已发布的测试版本，基线应用不会因此改动。"
    :is-refreshing="dataRefresh.isRefreshing"
    @refresh="dataRefresh.refresh"
  />
  <section class="split-layout" :class="{ 'read-only-layout': !canManageRuns }">
    <form v-if="canManageRuns" class="form-card" @submit.prevent="submitRun">
      <h2>新建评测运行</h2>
      <fieldset class="mode-picker">
        <legend>评测类型</legend>
        <div class="mode-options">
          <label class="mode-option"><input v-model="form.mode" type="radio" value="baseline" /><span>基线回归</span></label>
          <label class="mode-option"><input v-model="form.mode" type="radio" value="comparison" /><span>DSPy 候选对比</span></label>
        </div>
      </fieldset>
      <label>测试集<select v-model="form.datasetId"><option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }}</option></select></label>
      <p v-if="selectedDatasetDetail" class="readiness-note" :class="{ 'needs-review': selectedDatasetDetail.automation_readiness.eligible_cases < selectedDatasetDetail.automation_readiness.total_cases }">自动判定预检：{{ selectedDatasetDetail.automation_readiness.eligible_cases }}/{{ selectedDatasetDetail.automation_readiness.total_cases }} 条具备完整静态规则。其余结果将按实际证据进入人工复核；这不是质量通过率。<router-link :to="{ name: 'dataset-detail', params: { datasetId: selectedDatasetDetail.id } }">查看缺口</router-link></p>
      <label>Baseline 目标<select v-model="form.baselineTargetId"><option v-for="target in targets" :key="target.id" :value="target.id">{{ target.name }}</option></select></label>
      <p v-if="selectedBaseline" class="helper-copy">基线执行版本：{{ selectedBaseline.published_workflow_id || '未锁定，将使用 Dify 当前已发布版本；建议先到目标详情填写版本 UUID。' }}</p>
      <template v-if="form.mode === 'comparison'"><label>已核对发布版本的 DSPy 候选<select v-model="form.candidateId" required><option v-for="candidate in readyCandidates" :key="candidate.id" :value="candidate.id">{{ candidate.name }} · 已人工核对</option></select></label><p v-if="!readyCandidates.length" class="readiness-note needs-review">暂无可对比候选。请到 <router-link :to="{ name: 'optimizer' }">Prompt 优化</router-link> 打开候选详情，先完成隔离应用发布版本与 Prompt 哈希的人工核对。</p><p class="helper-copy">候选 Dify 应用在候选版本中绑定，不能在这里随意替换。</p><label>复用已完成的基线评测<select v-model="form.baselineRunId" required><option v-for="run in reusableBaselineRuns" :key="run.id" :value="run.id">{{ run.id }} · {{ formatBackendTime(run.completed_at || run.created_at) }}</option></select></label><p class="helper-copy">本次只调用候选 {{ datasets.find((item) => item.id === form.datasetId)?.case_count ?? 0 }} 条；基线回答沿用选中的同一冻结集和目标版本。没有可选基线时，请先完成基线回归。</p></template>
      <p v-if="form.mode === 'comparison' && selectedCandidateTarget" class="helper-copy">候选执行版本：{{ selectedCandidateTarget.published_workflow_id || '未锁定，将使用 Dify 当前已发布版本；建议先到目标详情填写版本 UUID。' }}</p>
      <p v-else-if="form.mode === 'baseline'" class="helper-copy">此模式只调用 Baseline 目标，不创建也不要求候选 Prompt。</p>
      <fieldset class="mode-picker">
        <legend>本次测试范围</legend>
        <div class="mode-options">
          <label class="mode-option"><input v-model="form.rerunScope" type="radio" value="all" /><span>全部用例</span></label>
          <label class="mode-option"><input v-model="form.rerunScope" type="radio" value="failed_only" /><span>仅未通过 / 待判定</span></label>
        </div>
      </fieldset>
      <template v-if="form.rerunScope === 'failed_only'">
        <label>依据哪次评测复测<select v-model="form.retrySourceRunId" required><option v-for="run in reusableRetryRuns" :key="run.id" :value="run.id">{{ run.id }} · {{ formatBackendTime(run.completed_at || run.created_at) }}</option></select></label>
        <p class="helper-copy">只沿用同一冻结集、同一目标与候选版本的已通过结果，人工已通过的标注证据也会保留；真实失败、调用错误和待判定用例重新调用。新版本候选必须先做全量评测。</p>
        <p v-if="!canRetryFailedOnly" class="error-message">当前没有兼容的已完成运行，或 Dify 目标尚未锁定已发布版本。请先全量评测并填写版本 UUID。</p>
      </template>
      <van-button native-type="submit" type="primary" block :loading="isSubmitting" :disabled="isSubmitting || (form.mode === 'comparison' && !form.baselineRunId) || (form.rerunScope === 'failed_only' && !canRetryFailedOnly)">{{ form.rerunScope === 'failed_only' ? '创建定向复测' : form.mode === 'baseline' ? '创建基线评测' : '创建候选对比' }}</van-button>
      <p v-if="successMessage" class="success-message">{{ successMessage }}</p>
      <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
    </form>
    <section class="data-list">
      <article v-for="run in runs" :key="run.id" class="row-card">
        <div><h2>{{ run.id }}</h2><p>{{ run.mode === 'baseline' ? '基线回归' : 'DSPy 候选对比' }} · 数据集：{{ run.dataset_id }}</p><p v-if="run.baseline_run_id" class="helper-copy">复用基线：{{ run.baseline_run_id }} · 未重复调用基线</p><p v-if="run.rerun_scope === 'failed_only'" class="helper-copy">仅未通过 / 待判定复测 · 来源 {{ run.retry_source_run_id }} · 沿用 {{ run.reused_cases }} 条 / 重测 {{ run.rerun_cases }} 条</p></div>
        <div class="row-meta"><strong>{{ statusLabel(run.status) }}</strong><small class="run-progress">{{ progressLabel(run) }}</small><progress v-if="run.total_cases > 0" :value="run.completed_cases" :max="run.total_cases" :aria-label="`${run.id} 已完成 ${run.completed_cases} / ${run.total_cases} 条`" /><small>{{ durationLabel(run) }}</small><small>已尝试 {{ run.attempt_count }} 次 · {{ formatBackendTime(run.created_at) }}</small><small v-if="run.last_error" class="run-error">执行异常：{{ run.last_error }}</small><router-link v-if="run.status === 'completed'" class="detail-link" :to="{ name: 'report-detail', params: { runId: run.id } }">查看报告详情</router-link><div v-if="canManageRuns && (run.can_cancel || run.can_delete)" class="run-actions"><van-button v-if="run.can_cancel" size="small" plain type="warning" :loading="actioningRunId === run.id" :disabled="actioningRunId !== null" @click="cancelEvaluation(run)">中断</van-button><van-button v-if="run.can_delete" size="small" plain type="danger" :loading="actioningRunId !== null" @click="removeEvaluation(run)">删除</van-button></div></div>
      </article>
    </section>
  </section>
</template>

<style scoped>
.run-error { color: var(--error); max-width: 32ch; }
.run-progress { color: var(--orchid-deep); font-weight: 700; }
.row-meta progress { width: 100%; height: 8px; accent-color: var(--orchid); }
.run-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 3px; }
.mode-picker { display: grid; gap: 10px; min-inline-size: 0; margin: 0; border: 0; padding: 0; }
.mode-picker legend { padding: 0; color: #4e4665; font-size: 13px; font-weight: 700; }
.mode-options { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 28px; }
.mode-option { display: inline-flex !important; flex: 0 0 auto; align-items: center; gap: 8px; width: max-content; margin: 0; color: #4e4665; font-size: 14px; font-weight: 700; line-height: 1.4; white-space: nowrap; cursor: pointer; }
.mode-option input { flex: 0 0 auto; margin: 0; }
.helper-copy { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.65; }
.readiness-note { margin: 0; color: var(--orchid-deep); font-size: 13px; line-height: 1.65; }.readiness-note.needs-review { padding: 10px 12px; border-radius: 10px; color: #64421b; background: #fff0d5; }.readiness-note a { color: inherit; font-weight: 700; }
</style>

