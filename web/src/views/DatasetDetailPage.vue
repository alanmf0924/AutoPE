<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { showConfirmDialog } from 'vant'

import PageHeader from '@/components/PageHeader.vue'
import { ApiError, approveDataset, freezeDataset, getDataset, uploadDatasetCases } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import {
  riskLevels,
  type DatasetCaseInput,
  type DatasetDetail,
  type EvaluationDimensionRules,
  type EvaluationRules,
  type RiskLevel,
} from '@/types'

const route = useRoute()
const dataset = ref<DatasetDetail>()
const canManageDatasets = computed(() => activeSession.value?.allowed_actions.includes('datasets.manage') ?? false)
const canApprove = computed(() => activeSession.value?.allowed_actions.includes('approvals.manage') ?? false)
const previewCases = ref<DatasetCaseInput[]>([])
const selectedFileName = ref('')
const errorMessage = ref('')
const successMessage = ref('')
const isUploading = ref(false)
const isFreezing = ref(false)
const isApproving = ref(false)
const evidenceReconfirmRequired = ref(false)
const fileInput = ref<HTMLInputElement>()

function datasetId(): string {
  return typeof route.params.datasetId === 'string' ? route.params.datasetId : ''
}

function isRiskLevel(value: unknown): value is RiskLevel {
  return typeof value === 'string' && riskLevels.includes(value as RiskLevel)
}

const evaluationRuleKeys = ['business_understanding', 'clarification', 'tool_use', 'safety'] as const

function parseRuleTerms(value: unknown, index: number, field: string): string[] {
  if (!Array.isArray(value) || !value.every((term) => typeof term === 'string' && term.trim())) {
    throw new Error(`第 ${index + 1} 条的 ${field} 必须是非空字符串数组。`)
  }
  return value.map((term) => term.trim())
}

function parseEvaluationRules(value: unknown, index: number): EvaluationRules {
  if (value === undefined) {
    return {}
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`第 ${index + 1} 条的 evaluation_rules 必须是对象。`)
  }
  const source = value as Record<string, unknown>
  const rules: EvaluationRules = {}
  for (const key of evaluationRuleKeys) {
    const rawRule = source[key]
    if (rawRule === undefined) {
      continue
    }
    if (typeof rawRule !== 'object' || rawRule === null || Array.isArray(rawRule)) {
      throw new Error(`第 ${index + 1} 条的 ${key} 规则必须是对象。`)
    }
    const rule = rawRule as Record<string, unknown>
    rules[key] = {
      required_terms: parseRuleTerms(rule.required_terms, index, `${key}.required_terms`),
      prohibited_terms: rule.prohibited_terms === undefined
        ? []
        : parseRuleTerms(rule.prohibited_terms, index, `${key}.prohibited_terms`),
    } satisfies EvaluationDimensionRules
  }
  return rules
}

function parseExpectedContract(value: unknown, index: number): Record<string, unknown> {
  if (value === undefined) {
    return {}
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`第 ${index + 1} 条的 expected_contract 必须是对象。`)
  }
  return { ...(value as Record<string, unknown>) }
}

function caseFromUnknown(value: unknown, index: number): DatasetCaseInput {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`第 ${index + 1} 条不是有效对象。`)
  }
  const item = value as Record<string, unknown>
  const title = typeof item.title === 'string' ? item.title.trim() : ''
  const prompt = typeof item.prompt === 'string' ? item.prompt.trim() : ''
  const expectedBehavior = typeof item.expected_behavior === 'string' ? item.expected_behavior.trim() : ''
  const expectedContract = parseExpectedContract(item.expected_contract, index)
  const evaluationRules = parseEvaluationRules(item.evaluation_rules, index)
  const riskLevel = item.risk_level ?? 'normal'
  if (title.length < 2 || prompt.length === 0) {
    throw new Error(`第 ${index + 1} 条需要 title 和 prompt。`)
  }
  if (!isRiskLevel(riskLevel)) {
    throw new Error(`第 ${index + 1} 条的 risk_level 必须为 normal、high_risk 或 red_line。`)
  }
  return {
    title,
    prompt,
    expected_behavior: expectedBehavior,
    expected_contract: expectedContract,
    evaluation_rules: evaluationRules,
    risk_level: riskLevel,
  }
}

function parseCsvRows(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let value = ''
  let isQuoted = false
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]
    const nextCharacter = text[index + 1]
    if (character === '"' && isQuoted && nextCharacter === '"') {
      value += '"'
      index += 1
    } else if (character === '"') {
      isQuoted = !isQuoted
    } else if (character === ',' && !isQuoted) {
      row.push(value)
      value = ''
    } else if ((character === '\n' || character === '\r') && !isQuoted) {
      if (character === '\r' && nextCharacter === '\n') {
        index += 1
      }
      row.push(value)
      if (row.some((cell) => cell.trim() !== '')) {
        rows.push(row)
      }
      row = []
      value = ''
    } else {
      value += character
    }
  }
  if (isQuoted) {
    throw new Error('CSV 存在未闭合的引号。')
  }
  row.push(value)
  if (row.some((cell) => cell.trim() !== '')) {
    rows.push(row)
  }
  return rows
}

function parseCsvCases(text: string): DatasetCaseInput[] {
  const [header, ...rows] = parseCsvRows(text)
  if (!header) {
    throw new Error('CSV 文件为空。')
  }
  const headers = header.map((item) => item.trim())
  const titleIndex = headers.indexOf('title')
  const promptIndex = headers.indexOf('prompt')
  const expectedBehaviorIndex = headers.indexOf('expected_behavior')
  const riskLevelIndex = headers.indexOf('risk_level')
  if (titleIndex === -1 || promptIndex === -1) {
    throw new Error('CSV 表头必须包含 title 与 prompt。')
  }
  return rows.map((row, index) =>
    caseFromUnknown(
      {
        title: row[titleIndex] ?? '',
        prompt: row[promptIndex] ?? '',
        expected_behavior: expectedBehaviorIndex === -1 ? '' : row[expectedBehaviorIndex] ?? '',
        expected_contract: {},
        evaluation_rules: {},
        risk_level: riskLevelIndex === -1 ? 'normal' : row[riskLevelIndex] ?? 'normal',
      },
      index,
    ),
  )
}

function parseUpload(text: string): DatasetCaseInput[] {
  const trimmed = text.trim()
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
    const parsed: unknown = JSON.parse(trimmed)
    const cases = Array.isArray(parsed)
      ? parsed
      : typeof parsed === 'object' && parsed !== null && Array.isArray((parsed as Record<string, unknown>).cases)
        ? ((parsed as Record<string, unknown>).cases as unknown[])
        : undefined
    if (!cases || cases.length === 0) {
      throw new Error('JSON 需为用例数组，或包含非空 cases 数组。')
    }
    return cases.map(caseFromUnknown)
  }
  return parseCsvCases(trimmed)
}

async function loadDataset(): Promise<void> {
  errorMessage.value = ''
  try {
    dataset.value = await getDataset(datasetId())
  } catch {
    errorMessage.value = '无法读取测试集详情。它可能已被删除，或后端服务不可用。'
  }
}

async function acknowledgeEvidence(): Promise<void> {
  try {
    dataset.value = await getDataset(datasetId())
    evidenceReconfirmRequired.value = false
    errorMessage.value = ''
  } catch {
    errorMessage.value = '无法重新读取测试集证据；旧版本不能用于批准。'
  }
}

function onSessionRestored(): void {
  evidenceReconfirmRequired.value = true
  void loadDataset()
}

async function selectFile(event: Event): Promise<void> {
  errorMessage.value = ''
  successMessage.value = ''
  const source = event.target
  if (!(source instanceof HTMLInputElement) || !source.files?.[0]) {
    return
  }
  const file = source.files[0]
  try {
    const parsedCases = parseUpload(await file.text())
    if (parsedCases.length > 500) {
      throw new Error('一次最多上传 500 条用例。')
    }
    previewCases.value = parsedCases
    selectedFileName.value = file.name
  } catch (error) {
    previewCases.value = []
    selectedFileName.value = ''
    errorMessage.value = error instanceof Error ? error.message : '无法解析上传文件。'
  }
}

async function addCases(): Promise<void> {
  if (!dataset.value || previewCases.value.length === 0 || isUploading.value) {
    return
  }
  isUploading.value = true
  errorMessage.value = ''
  try {
    dataset.value = await uploadDatasetCases(dataset.value.id, previewCases.value)
    notifyPlatformDataChanged('datasets', 'runs', 'dashboard')
    successMessage.value = `已将 ${previewCases.value.length} 条用例加入草稿。请检查详情后再冻结。`
    previewCases.value = []
    selectedFileName.value = ''
    if (fileInput.value) {
      fileInput.value.value = ''
    }
  } catch {
    errorMessage.value = '无法写入用例。冻结后的测试集不能再修改，请创建新草稿。'
  } finally {
    isUploading.value = false
  }
}

async function confirmFreeze(): Promise<void> {
  if (!dataset.value?.can_freeze || isFreezing.value) {
    return
  }
  try {
    await showConfirmDialog({
      title: '确认冻结测试集？',
      message: '冻结后用例内容不能再修改；还需要有审批权限的人员确认后才能用于评测。',
      confirmButtonText: '确认冻结',
    })
  } catch {
    return
  }
  isFreezing.value = true
  errorMessage.value = ''
  try {
    dataset.value = await freezeDataset(dataset.value.id)
    notifyPlatformDataChanged('datasets', 'runs', 'dashboard')
    successMessage.value = '测试集已冻结，等待批准后方可用于评测。'
  } catch {
    errorMessage.value = '无法冻结测试集。请确认草稿中至少有一条已上传用例。'
  } finally {
    isFreezing.value = false
  }
}

async function confirmApprove(): Promise<void> {
  if (!dataset.value || dataset.value.review_status !== 'in_review' || isApproving.value) return
  if (evidenceReconfirmRequired.value || !dataset.value.evidence_digest) {
    errorMessage.value = '请重新读取当前测试集证据并核对后再批准。'
    return
  }
  const datasetIdToApprove = dataset.value.id
  const reviewedDigest = dataset.value.evidence_digest
  try {
    await showConfirmDialog({ title: '批准该冻结测试集？', message: '请确认用例及预期契约已完成业务核对；批准后可用于评测。', confirmButtonText: '批准测试集' })
  } catch { return }
  isApproving.value = true
  errorMessage.value = ''
  try {
    dataset.value = await approveDataset(datasetIdToApprove, reviewedDigest)
    notifyPlatformDataChanged('datasets', 'runs', 'dashboard')
    successMessage.value = '已批准冻结版本，可以创建评测运行。'
  } catch (error) {
    if (error instanceof ApiError && [412, 428].includes(error.status)) evidenceReconfirmRequired.value = true
    errorMessage.value = error instanceof Error ? error.message : '批准失败，请检查权限和测试集状态。'
  } finally { isApproving.value = false }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['datasets', 'runs'], refresh: loadDataset })
onMounted(() => window.addEventListener('autope:session-restored', onSessionRestored))
onBeforeUnmount(() => window.removeEventListener('autope:session-restored', onSessionRestored))
</script>

<template>
  <PageHeader title="测试集详情" description="先查看草稿用例，再确认冻结；冻结后的数据集会成为评测运行的可复现快照。" :is-refreshing="dataRefresh.isRefreshing" @refresh="dataRefresh.refresh" />
  <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
  <template v-else-if="dataset">
    <p v-if="canApprove && evidenceReconfirmRequired" class="error-message" role="status">会话或证据版本已变化。请核对下方用例和当前版本，旧摘要不可直接批准。<button type="button" class="plain-action" @click="acknowledgeEvidence">已核对，读取当前版本</button></p>
    <section class="detail-summary">
      <div><span>状态</span><strong>{{ dataset.review_status === 'approved' ? '已批准' : dataset.review_status === 'in_review' ? '已冻结 · 待批准' : '草稿可编辑' }}</strong></div>
      <div><span>用例数量</span><strong>{{ dataset.case_count }} 条</strong></div>
      <div><span>版本</span><strong>v{{ dataset.version }}</strong></div>
    </section>
    <p class="helper-copy">当前证据摘要：<code>{{ dataset.evidence_digest.slice(0, 16) }}…</code>。批准请求会锁定本次读取的完整摘要，版本变化后需重新核对。</p>
    <section class="chart-panel readiness-panel" aria-label="自动判定覆盖预检">
      <h2>自动判定覆盖预检</h2>
      <p>当前有 {{ dataset.automation_readiness.eligible_cases }}/{{ dataset.automation_readiness.total_cases }} 条具备自动判定所需的静态规则。此处只检查契约可执行性，不预测模型是否合格；缺少规则的用例评测后仍需人工复核。</p>
      <details v-if="dataset.automation_readiness.eligible_cases < dataset.automation_readiness.total_cases">
        <summary>查看需补规则的用例与原因</summary>
        <ul><li v-for="item in dataset.automation_readiness.cases.filter((caseItem) => !caseItem.eligible)" :key="item.case_id"><strong>{{ item.title }}</strong><span v-if="item.missing_dimensions.length">缺四维断言：{{ item.missing_dimensions.join('、') }}</span><span v-if="item.manual_contract_fields.length">语义仍需复核：{{ item.manual_contract_fields.join('、') }}</span></li></ul>
      </details>
      <p v-if="dataset.is_frozen && dataset.automation_readiness.eligible_cases < dataset.automation_readiness.total_cases" class="helper-copy">该版本已冻结，不能原地补规则；请创建新草稿、补充经业务确认的断言后重新冻结与审批。</p>
    </section>
    <section class="split-layout" :class="{ 'read-only-layout': !canManageDatasets && !canApprove }">
      <section v-if="canManageDatasets || canApprove" class="form-card">
        <h2>{{ canManageDatasets ? '上传用例文件' : '测试集审批' }}</h2>
        <template v-if="canManageDatasets">
          <p class="helper-copy">支持 JSON 数组、<code>{ "cases": [] }</code> 或 CSV。CSV 字段：<code>title,prompt,expected_behavior,risk_level</code>；JSON 可额外配置 <code>expected_contract</code> 与 <code>evaluation_rules</code>。风险级别默认为 normal。</p>
          <p class="helper-copy">四维自动门禁只读取 JSON 的显式断言；缺少任一维会进入人工标注。工具使用需让 Dify 工作流输出可审计的工具证据，不能仅凭自然语言猜测。</p>
          <input v-if="dataset.can_edit" ref="fileInput" type="file" accept=".json,.csv,application/json,text/csv" @change="selectFile" />
          <p v-else class="helper-copy">当前版本已冻结，不能再上传或修改用例。请从测试集列表创建一个新草稿。</p>
          <section v-if="previewCases.length" class="upload-preview" aria-live="polite">
            <strong>{{ selectedFileName }} · 已解析 {{ previewCases.length }} 条</strong>
            <span>首条：{{ previewCases[0]?.title }}</span>
            <van-button type="primary" block :loading="isUploading" :disabled="isUploading || !dataset.can_edit" @click="addCases">加入测试集草稿</van-button>
          </section>
          <p v-else class="helper-copy">文件只会在点击“加入测试集草稿”后提交；选择文件不会立即上传。</p>
          <van-button type="primary" plain block :loading="isFreezing" :disabled="!dataset.can_freeze || isFreezing" @click="confirmFreeze">确认用例并冻结</van-button>
        </template>
        <van-button v-if="canApprove && dataset.review_status === 'in_review'" type="primary" block :loading="isApproving" :disabled="isApproving" @click="confirmApprove">批准冻结测试集</van-button>
        <p v-if="successMessage" class="success-message">{{ successMessage }}</p>
      </section>
      <section class="detail-aside">
        <h2>{{ dataset.name }}</h2>
        <p>{{ dataset.description || '未填写覆盖范围说明。' }}</p>
        <p v-if="dataset.is_frozen">该版本已冻结。若需增删用例，请创建新的测试集草稿。</p>
        <router-link class="detail-link" :to="{ name: 'datasets' }">返回测试集列表</router-link>
      </section>
    </section>
    <section class="chart-panel case-panel">
      <div class="chart-heading"><h2>用例明细</h2><span>共 {{ dataset.cases.length }} 条已上传用例</span></div>
      <div v-if="dataset.cases.length" class="table-wrap">
        <table class="accessible-table"><caption>测试集用例列表</caption><thead><tr><th>#</th><th>标题与输入</th><th>预期行为与契约</th><th>自动断言</th><th>风险</th></tr></thead><tbody><tr v-for="item in dataset.cases" :key="item.id"><td>{{ item.position }}</td><td><strong>{{ item.title }}</strong><small>{{ item.prompt }}</small></td><td>{{ item.expected_behavior || '未填写' }}<small v-if="Object.keys(item.expected_contract).length">结构化契约：{{ Object.keys(item.expected_contract).join('、') }}</small></td><td>{{ Object.keys(item.evaluation_rules).length ? `${Object.keys(item.evaluation_rules).length} / 4 维` : '未配置，人工复核' }}</td><td>{{ item.risk_level }}</td></tr></tbody></table>
      </div>
      <p v-else class="helper-copy">{{ dataset.is_frozen ? `该历史冻结版本仅保留了 ${dataset.case_count} 条用例的汇总与哈希，没有逐条内容可回填；请创建新草稿导入后获得可查看详情。` : '当前草稿还没有可查看的用例。请选择 JSON 或 CSV 文件导入。' }}</p>
    </section>
  </template>
</template>

<style scoped>
.plain-action { margin-inline-start: 8px; padding: 0; border: 0; color: var(--orchid-deep); background: transparent; font: inherit; font-weight: 700; text-decoration: underline; cursor: pointer; }
.detail-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 15px; margin-bottom: 21px; }
.detail-summary div, .detail-aside { padding: 20px; background: var(--panel); box-shadow: 0 10px 24px rgba(81, 64, 137, .07); backdrop-filter: blur(14px); border-radius: 15px; }
.detail-summary span, .helper-copy, .detail-aside p, .upload-preview span, td small { display: block; color: var(--muted); font-size: 12px; line-height: 1.65; }
.detail-summary strong { display: block; margin-top: 8px; color: var(--orchid-deep); font-size: 17px; }
.detail-aside, .upload-preview { display: grid; gap: 13px; }
.detail-aside h2, .detail-aside p { margin: 0; }
.detail-aside h2 { font-size: 20px; }
.upload-preview { padding: 14px; background: rgba(238, 233, 255, .66); border-radius: 12px; }
.helper-copy { margin: 0; }
.helper-copy code { color: var(--orchid-deep); overflow-wrap: anywhere; }
.readiness-panel { display: grid; gap: 12px; margin-bottom: 21px; }.readiness-panel h2, .readiness-panel p { margin: 0; }.readiness-panel h2 { font-size: 21px; }.readiness-panel p { color: #504865; line-height: 1.65; }.readiness-panel details { border-top: 1px solid var(--line); padding-top: 12px; }.readiness-panel summary { color: var(--orchid-deep); cursor: pointer; font-weight: 700; }.readiness-panel ul { display: grid; gap: 9px; margin: 13px 0 0; padding-inline-start: 18px; }.readiness-panel li { overflow-wrap: anywhere; }.readiness-panel li span { display: block; color: var(--muted); font-size: 12px; line-height: 1.6; }
.case-panel { margin-top: 21px; }
.table-wrap { overflow-x: auto; }
td { vertical-align: top; } td strong { display: block; color: var(--ink); } td small { margin-top: 4px; min-width: 220px; }
@media (max-width: 620px) { .detail-summary { grid-template-columns: 1fr; } }
</style>
