<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import { bindCandidate, cancelCandidateGenerationJob, createCandidate, generateCandidate, getCandidateGenerationConfig, listCandidateGenerationJobs, listCandidates, listDatasets, listTargets } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import { formatBackendTime, parseBackendTime } from '@/services/time'
import type { CandidateGenerationConfig, CandidateGenerationJob, Dataset, DifyTarget, PromptCandidate } from '@/types'

const candidates = ref<PromptCandidate[]>([])
const canManageCandidates = computed(() => activeSession.value?.allowed_actions.includes('candidates.manage') ?? false)
const targets = ref<DifyTarget[]>([])
const datasets = ref<Dataset[]>([])
const generationJobs = ref<CandidateGenerationJob[]>([])
const generationConfig = ref<CandidateGenerationConfig>({ model: '', endpoint: '', configured: false })
const externalTransferAuthorized = ref(false)
const nowMs = ref(Date.now())
let clockTimer: number | undefined
const errorMessage = ref('')
const successMessage = ref('')
const isGenerating = ref(false)
const isSavingCandidate = ref(false)
const bindingCandidateId = ref('')
const form = reactive({ name: '', baseVersionFingerprint: '', promptBlock: '', boundTargetId: '' })
const generationForm = reactive({ datasetId: '', baselineTargetId: '' })
const bindingTargetIds = reactive<Record<string, string>>({})
const claimedTargetIds = computed(() => new Set(candidates.value.map((candidate) => candidate.bound_target_id).filter((id): id is string => Boolean(id))))
const candidateDraftTargets = computed(() =>
  targets.value.filter((target) => target.version_fingerprint !== form.baseVersionFingerprint && !claimedTargetIds.value.has(target.id)),
)

watch(
  candidateDraftTargets,
  (availableTargets) => {
    if (!availableTargets.some((target) => target.id === form.boundTargetId)) {
      form.boundTargetId = ''
    }
  },
)

async function loadData(): Promise<void> {
  const [candidateItems, targetItems, datasetItems, jobItems, config] = await Promise.all([
    listCandidates(),
    listTargets(),
    listDatasets(),
    listCandidateGenerationJobs(),
    getCandidateGenerationConfig(),
  ])
  candidates.value = candidateItems
  targets.value = targetItems
  datasets.value = datasetItems
  generationJobs.value = jobItems
  if (generationConfig.value.model !== config.model || generationConfig.value.endpoint !== config.endpoint) externalTransferAuthorized.value = false
  generationConfig.value = config
  const isSelectedTargetAvailable = targetItems.some(
    (target) => target.version_fingerprint === form.baseVersionFingerprint,
  )
  if (!isSelectedTargetAvailable) {
    form.baseVersionFingerprint = targetItems.find((target) => !target.version_fingerprint.includes('@candidate'))?.version_fingerprint ?? targetItems[0]?.version_fingerprint ?? ''
  }
  if (!targetItems.some((target) => target.id === generationForm.baselineTargetId)) {
    generationForm.baselineTargetId = targetItems.find((target) => !target.version_fingerprint.includes('@candidate'))?.id ?? ''
  }
  if (!datasetItems.some((dataset) => dataset.id === generationForm.datasetId)) {
    generationForm.datasetId = datasetItems.find((dataset) => dataset.version === 2)?.id ?? datasetItems[0]?.id ?? ''
  }
}

async function submitCandidate(): Promise<void> {
  if (isSavingCandidate.value) {
    return
  }
  errorMessage.value = ''
  successMessage.value = ''
  isSavingCandidate.value = true
  try {
    const boundTargetId = form.boundTargetId
    const candidate = await createCandidate({
      name: form.name,
      base_version_fingerprint: form.baseVersionFingerprint,
      optimizable_prompt_block: form.promptBlock,
      bound_target_id: form.boundTargetId || null,
    })
    candidates.value = [candidate, ...candidates.value]
    notifyPlatformDataChanged('candidates', 'runs')
    form.name = ''
    form.promptBlock = ''
    successMessage.value = boundTargetId ? 'DSPy 候选已登记并绑定隔离 Dify 应用，可创建对比评测。' : 'DSPy 候选已保存；请应用到隔离克隆应用并在该应用发布测试版本后再绑定目标。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法登记候选，请检查输入后重试。'
  } finally {
    isSavingCandidate.value = false
  }
}

function draftTargetsFor(candidate: PromptCandidate): DifyTarget[] {
  return targets.value.filter((target) => target.version_fingerprint !== candidate.base_version_fingerprint && (!claimedTargetIds.value.has(target.id) || target.id === candidate.bound_target_id))
}

async function generateWithDspy(): Promise<void> {
  errorMessage.value = ''
  successMessage.value = ''
  if (!generationForm.datasetId || !generationForm.baselineTargetId || !externalTransferAuthorized.value || !generationConfig.value.configured) {
    errorMessage.value = '请选择已冻结的业务黄金集和对应基线目标。'
    return
  }
  isGenerating.value = true
  try {
    const job = await generateCandidate({
      dataset_id: generationForm.datasetId,
      baseline_target_id: generationForm.baselineTargetId,
      authorize_external_transfer: true,
      consented_model: generationConfig.value.model,
      consented_endpoint: generationConfig.value.endpoint,
    })
    generationJobs.value = [job, ...generationJobs.value]
    notifyPlatformDataChanged('candidates', 'runs')
    externalTransferAuthorized.value = false
    successMessage.value = `生成任务 ${job.id} 已排队。下方会持续显示状态与耗时；完成后再绑定隔离草稿。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'DSPy 候选生成失败，请检查模型配置。'
  } finally {
    isGenerating.value = false
  }
}

function jobDuration(job: CandidateGenerationJob): string {
  const from = parseBackendTime(job.started_at ?? job.created_at)
  const to = job.completed_at ? parseBackendTime(job.completed_at) : nowMs.value
  if (!Number.isFinite(from) || !Number.isFinite(to)) return '耗时未知'
  const seconds = Math.max(0, Math.floor((to - from) / 1000))
  const minutes = Math.floor(seconds / 60)
  return `${job.status === 'queued' ? '等待' : job.completed_at ? '运行耗时' : '已运行'} ${minutes ? `${minutes} 分 ` : ''}${seconds % 60} 秒`
}

function jobStatus(job: CandidateGenerationJob): string {
  if (job.status === 'queued') return '排队中 · 等待生成 Worker'
  if (job.status === 'running') return '正在调用模型 · 此阶段无法精确量化百分比'
  if (job.status === 'completed') return '已完成 · 待绑定隔离候选应用'
  return job.status === 'failed' ? '生成失败' : '已取消'
}

async function cancelQueuedJob(job: CandidateGenerationJob): Promise<void> {
  try {
    const updated = await cancelCandidateGenerationJob(job.id)
    generationJobs.value = generationJobs.value.map((item) => item.id === updated.id ? updated : item)
    notifyPlatformDataChanged('candidates', 'runs', 'dashboard')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法取消生成任务。'
  }
}

onMounted(() => { clockTimer = window.setInterval(() => { nowMs.value = Date.now() }, 1_000) })
onBeforeUnmount(() => { if (clockTimer !== undefined) window.clearInterval(clockTimer) })

async function bindDraft(candidate: PromptCandidate): Promise<void> {
  const targetId = bindingTargetIds[candidate.id] ?? ''
  if (!targetId) {
    errorMessage.value = '请选择已应用该候选 Prompt 的隔离 Dify 应用。'
    return
  }
  errorMessage.value = ''
  successMessage.value = ''
  bindingCandidateId.value = candidate.id
  try {
    const updated = await bindCandidate(candidate.id, targetId)
    candidates.value = candidates.value.map((item) => (item.id === updated.id ? updated : item))
    notifyPlatformDataChanged('candidates', 'runs')
    successMessage.value = '隔离候选应用已绑定，可在“评测运行”中创建真实对比评测。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '隔离候选应用绑定失败，请检查应用 ID 与版本标识。'
  } finally {
    bindingCandidateId.value = ''
  }
}

async function refreshOptimizer(): Promise<void> {
  try {
    await loadData()
  } catch {
    errorMessage.value = '无法读取候选或基线版本。请检查后端服务。'
  }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['candidates', 'targets'], refresh: refreshOptimizer, pollIntervalMs: 4_000 })
</script>

<template>
  <PageHeader
    title="生成候选，不在此处发布"
    description="生成或登记候选 Prompt，并绑定独立克隆的 Dify 应用；运行时 API 评测该应用已发布的测试版本，不会自动改写基线应用。"
    :is-refreshing="dataRefresh.isRefreshing"
    @refresh="dataRefresh.refresh"
  />
  <section v-if="canManageCandidates" class="form-card optimizer-generator" aria-label="DSPy 候选生成">
    <h2>直接生成 DSPy 候选</h2>
    <p class="helper-copy">仅使用已批准冻结的业务黄金集；候选先保存为提案，不会改写或发布 Dify 工作流。</p>
    <label>业务黄金集<select v-model="generationForm.datasetId" required><option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }} · v{{ dataset.version }}</option></select></label>
    <label>基线 Dify 目标<select v-model="generationForm.baselineTargetId" required><option v-for="target in targets" :key="target.id" :value="target.id">{{ target.name }} · {{ target.version_fingerprint }}</option></select></label>
    <div class="transfer-consent"><p>外部模型：{{ generationConfig.model || '未配置' }}<br />发送地址：{{ generationConfig.endpoint || '未配置' }}</p><label><input v-model="externalTransferAuthorized" type="checkbox" :disabled="!generationConfig.configured" />我确认将所选黄金集的用户输入、风险等级、预期契约发送到上述模型地址用于候选生成。</label></div>
    <van-button type="primary" :loading="isGenerating" :disabled="!generationForm.datasetId || !generationForm.baselineTargetId || !externalTransferAuthorized || !generationConfig.configured || isGenerating" @click="generateWithDspy">创建 DSPy 生成任务</van-button>
    <p v-if="!generationConfig.configured" class="helper-copy">模型、地址或密钥未配置完整，暂不可生成；已有候选仍可登记和绑定。</p>
    <p v-if="successMessage" class="success-message" aria-live="polite">{{ successMessage }}</p>
    <p v-if="errorMessage" class="error-message" aria-live="polite">{{ errorMessage }}</p>
  </section>
  <section v-if="generationJobs.length" class="chart-panel generation-history"><h2>候选生成进度</h2><div class="data-list"><article v-for="job in generationJobs" :key="job.id" class="row-card"><div><h3>{{ job.id }}</h3><p>{{ jobStatus(job) }} · {{ jobDuration(job) }}</p><p v-if="job.error_message" class="error-message">{{ job.error_message }}</p><progress v-if="job.status === 'running'" aria-label="模型正在生成，进度不可量化" /><router-link v-if="job.candidate_id" class="detail-link" :to="{ name: 'candidate-detail', params: { candidateId: job.candidate_id } }">查看生成候选详情</router-link></div><div class="row-meta"><small>{{ formatBackendTime(job.created_at) }}</small><van-button v-if="canManageCandidates && job.status === 'queued'" size="small" plain type="warning" @click="cancelQueuedJob(job)">取消排队</van-button></div></article></div></section>
  <section class="split-layout" :class="{ 'read-only-layout': !canManageCandidates }">
    <form v-if="canManageCandidates" class="form-card" @submit.prevent="submitCandidate">
      <h2>登记 DSPy 候选</h2>
      <label>候选名称<input v-model.trim="form.name" required maxlength="100" placeholder="例如：DSPy 澄清顺序优化 v1" /></label>
      <label>基线版本<select v-model="form.baseVersionFingerprint" required><option v-for="target in targets" :key="target.id" :value="target.version_fingerprint">{{ target.name }} · {{ target.version_fingerprint }}</option></select></label>
      <label>DSPy 生成的 Prompt<textarea v-model.trim="form.promptBlock" required minlength="20" maxlength="12000" placeholder="粘贴 DSPy 输出的候选 Prompt；平台会保存正文与版本哈希。" /></label>
      <label>隔离候选 Dify 应用（可选）<select v-model="form.boundTargetId"><option value="">先保存，稍后绑定</option><option v-for="target in candidateDraftTargets" :key="target.id" :value="target.id">{{ target.name }} · {{ target.version_fingerprint }}</option></select></label>
      <p class="helper-copy">请先克隆为独立 Dify 应用，在克隆应用中应用候选并发布测试版本；基线与候选的应用 ID 必须不同。未发布的草稿无法通过运行时 API 评测。</p>
      <van-button native-type="submit" type="primary" block :loading="isSavingCandidate" :disabled="isSavingCandidate">保存 DSPy 候选</van-button>
    </form>
    <section class="data-list">
      <article v-for="candidate in candidates" :key="candidate.id" class="row-card">
        <div><h2>{{ candidate.name }}</h2><p>{{ candidate.base_version_fingerprint }} · {{ candidate.prompt_sha256.slice(0, 12) }}</p><router-link class="detail-link" :to="{ name: 'candidate-detail', params: { candidateId: candidate.id } }">查看候选详情与完整 Prompt</router-link></div>
        <div class="row-meta">
          <span>{{ candidate.status === 'ready_for_evaluation' ? '已绑定隔离应用' : '待绑定隔离应用' }}</span>
          <small>{{ candidate.id }}</small>
          <template v-if="canManageCandidates && candidate.status !== 'ready_for_evaluation'">
            <select v-model="bindingTargetIds[candidate.id]" :aria-label="`${candidate.name} 的隔离应用`"><option value="">选择隔离应用</option><option v-for="target in draftTargetsFor(candidate)" :key="target.id" :value="target.id">{{ target.name }} · {{ target.version_fingerprint }}</option></select>
            <van-button size="small" type="primary" :loading="bindingCandidateId === candidate.id" :disabled="!bindingTargetIds[candidate.id]" @click="bindDraft(candidate)">绑定应用</van-button>
          </template>
        </div>
      </article>
    </section>
  </section>
</template>

<style scoped>
.optimizer-generator { margin-bottom: 21px; grid-template-columns: minmax(220px, .8fr) minmax(240px, 1fr) minmax(240px, 1fr) auto; align-items: end; }
.optimizer-generator h2, .optimizer-generator .helper-copy { grid-column: 1 / -1; }
.transfer-consent { grid-column: 1 / -1; padding: 13px 15px; border: 1px solid var(--line); border-radius: 12px; background: rgba(245, 242, 255, .9); }
.transfer-consent p { margin: 0 0 9px; overflow-wrap: anywhere; color: var(--ink); font-size: 12px; line-height: 1.65; }
.transfer-consent label { display: flex; gap: 9px; align-items: flex-start; font-weight: 500; line-height: 1.6; }
.transfer-consent input { flex: 0 0 auto; width: auto; margin-top: 3px; }
.generation-history { margin: 0 0 21px; }
.generation-history h3 { margin: 0; font-size: 15px; overflow-wrap: anywhere; }
.generation-history progress { width: min(100%, 260px); accent-color: var(--orchid); }
.row-meta { display: grid; justify-items: end; gap: 7px; }
.row-meta select { max-width: 280px; }
@media (max-width: 980px) { .optimizer-generator { grid-template-columns: 1fr; } }
</style>

