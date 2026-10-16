<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { showConfirmDialog } from 'vant'

import PageHeader from '@/components/PageHeader.vue'
import ReportChart from '@/components/ReportChart.vue'
import { getReportDetail, getReviewSuggestionConfig, getSession, queueReviewSuggestions, retryFailedReviewSuggestions } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { explainCheckDetail } from '@/services/evaluationCopy'
import { parseBackendTime } from '@/services/time'
import type { CaseAssessment, EvaluationReportDetail } from '@/types'

type CaseFilter = 'all' | 'failed' | 'pending' | 'passed' | 'error'

const route = useRoute()
const report = ref<EvaluationReportDetail>()
const focusedReviewId = computed(() => typeof route.query.reviewId === 'string' ? route.query.reviewId : '')
let lastFocusedReviewId = ''
const errorMessage = ref('')
const aiConfig = ref<{ model: string; endpoint: string; available: boolean }>()
const canQueueAi = ref(false)
const isQueuingAi = ref(false)
const isRetryingAi = ref(false)
const aiQueueError = ref('')
const nowMs = ref(Date.now())
let clockTimer: number | undefined
const selectedFilter = ref<CaseFilter>('all')
const caseResultsSection = ref<HTMLElement>()
const hasCompleteScores = computed(() => Boolean(report.value && report.value.score_coverage.total > 0 && report.value.score_coverage.baseline === report.value.score_coverage.total && (report.value.mode === 'baseline' || report.value.score_coverage.candidate === report.value.score_coverage.total)))
const reviewsById = computed(() => new Map((report.value?.review_cases ?? []).map((item) => [item.id, item])))
const aiProgress = computed(() => {
  const cases = report.value?.review_cases ?? []
  const suggested = cases.filter((item) => item.suggestion_status !== null)
  const completed = suggested.filter((item) => item.suggestion_status === 'completed').length
  const failed = suggested.filter((item) => item.suggestion_status === 'failed' || item.suggestion_status === 'uncertain').length
  const retryable = suggested.filter((item) => item.suggestion_status === 'failed').length
  return {
    total: suggested.length,
    completed,
    settled: completed + failed,
    queued: suggested.filter((item) => item.suggestion_status === 'queued').length,
    running: suggested.filter((item) => item.suggestion_status === 'running').length,
    failed,
    retryable,
    uncertain: failed - retryable,
    attempts: suggested.reduce((total, item) => total + item.suggestion_attempt_count, 0),
    requestedAt: Math.min(...suggested.map((item) => parseBackendTime(item.suggestion_requested_at ?? '')).filter(Number.isFinite)),
    completedAt: Math.max(...suggested.map((item) => parseBackendTime(item.suggestion_completed_at ?? '')).filter(Number.isFinite)),
  }
})
const aiIsActive = computed(() => aiProgress.value.queued + aiProgress.value.running > 0)
const canRetryAi = computed(() => aiProgress.value.retryable > 0 && aiProgress.value.uncertain === 0
  && !aiIsActive.value && aiProgress.value.attempts + aiProgress.value.retryable <= 20)
const aiDuration = computed(() => {
  const progress = aiProgress.value
  const end = aiIsActive.value ? nowMs.value : progress.completedAt
  if (!Number.isFinite(progress.requestedAt) || !Number.isFinite(end)) return '耗时待更新'
  const seconds = Math.max(0, Math.floor((end - progress.requestedAt) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor(seconds % 3600 / 60)
  return `${hours ? `${hours} 小时 ` : ''}${minutes ? `${minutes} 分 ` : ''}${seconds % 60} 秒`
})
const pendingAiCount = computed(() => (report.value?.review_cases ?? []).filter((item) => item.review_status === 'pending').length)
const visibleCases = computed(() => (report.value?.case_assessments ?? []).filter((item) => selectedFilter.value === 'all' || caseCategory(item) === selectedFilter.value))
const unscoredCases = computed(() => {
  const current = report.value
  if (!current) return 0
  const scored = current.mode === 'comparison' ? current.score_coverage.candidate : current.score_coverage.baseline
  return Math.max(0, current.score_coverage.total - scored)
})

function caseCategory(item: CaseAssessment): CaseFilter {
  if (item.error_code) return 'error'
  if (item.passed) return 'passed'
  if (item.assessment.verdict === 'failed' || item.result_status !== 'manual_review') return 'failed'
  return 'pending'
}

function caseStatusLabel(item: CaseAssessment): string {
  const category = caseCategory(item)
  if (category === 'error') return '调用出错'
  if (category === 'passed') return '已判通过'
  if (category === 'failed') return '未通过'
  return '等你判断'
}

function reviewActionLabel(item: CaseAssessment): string {
  const review = reviewsById.value.get(item.review_id)
  if (!review || review.review_status === 'pending') return review?.suggestion_status === 'completed' ? '核对 AI 建议并判断' : '查看回答并给出判断'
  return '查看并修改已有判断'
}

function riskLabel(risk: CaseAssessment['risk_level']): string {
  return risk === 'normal' ? '普通' : risk === 'high_risk' ? '高风险' : '红线'
}

function filterLabel(filter: CaseFilter): string {
  if (filter === 'failed') return '未通过'
  if (filter === 'pending') return '等你判断'
  if (filter === 'passed') return '已判通过'
  if (filter === 'error') return '调用出错'
  return '全部'
}

async function selectFilter(filter: CaseFilter): Promise<void> {
  selectedFilter.value = filter
  await nextTick()
  caseResultsSection.value?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
}

function assessmentLabel(verdict: string): string {
  if (verdict === 'passed') return '系统检查通过'
  if (verdict === 'failed') return '系统检查发现问题'
  if (verdict === 'execution_error') return '系统调用出错'
  if (verdict === 'needs_review') return '系统无法独自判断'
  return '当时未记录系统检查'
}

function rateLabel(risk: 'normal' | 'high_risk' | 'red_line'): string {
  const current = report.value
  if (!current) return '待审核'
  const coverage = current.risk_coverage[risk]
  if (coverage.total === 0) return '无用例'
  return coverage.assessed === coverage.total ? `${(current.pass_rates[risk] * 100).toFixed(0)}%` : '待审核'
}

function runId(): string {
  return typeof route.params.runId === 'string' ? route.params.runId : ''
}

async function loadReport(): Promise<void> {
  errorMessage.value = ''
  try {
    const loadedReport = await getReportDetail(runId())
    report.value = loadedReport
    const reviewId = focusedReviewId.value
    if (reviewId && reviewId !== lastFocusedReviewId && loadedReport.case_assessments.some((item) => item.review_id === reviewId)) {
      selectedFilter.value = 'all'
      await nextTick()
      const focusedCase = document.getElementById(`review-result-${reviewId}`)
      if (focusedCase instanceof HTMLDetailsElement) {
        focusedCase.open = true
        focusedCase.scrollIntoView({ behavior: 'auto', block: 'center' })
        lastFocusedReviewId = reviewId
      }
    }
  } catch {
    errorMessage.value = '无法读取这份报告。只有已完成运行才会生成可查看的报告详情。'
  }
}

async function loadAiAccess(): Promise<void> {
  try {
    const session = await getSession()
    canQueueAi.value = session.roles.includes('operator')
    aiConfig.value = session.roles.some((role) => role === 'operator' || role === 'auditor')
      ? await getReviewSuggestionConfig()
      : undefined
  } catch {
    canQueueAi.value = false
  }
}

async function queueAiDrafts(): Promise<void> {
  const config = aiConfig.value
  if (!config || isQueuingAi.value || aiProgress.value.total > 0 || pendingAiCount.value < 1 || pendingAiCount.value > 10) return
  isQueuingAi.value = true
  try {
    await showConfirmDialog({
      title: '生成 AI 辅助建议？',
      message: `将本次 ${pendingAiCount.value} 条待判断用例的用户问题、两版回答、预期规则和系统检查证据发送至 ${config.endpoint}（${config.model}）。每条最多调用一次，可能产生费用；AI 建议不会自动成为人工结论。`,
      confirmButtonText: '授权并生成',
    })
  } catch {
    isQueuingAi.value = false
    return
  }
  aiQueueError.value = ''
  try {
    await queueReviewSuggestions(runId(), config.model, config.endpoint)
    await loadReport()
    notifyPlatformDataChanged('reviews', 'reports')
  } catch (error) {
    await loadReport()
    aiQueueError.value = aiProgress.value.total > 0
      ? '这份报告已提交过 AI 建议任务；状态已更新，不能重复生成。'
      : error instanceof Error ? error.message : '无法启动 AI 辅助建议。'
  } finally { isQueuingAi.value = false }
}

async function retryFailedAiDrafts(): Promise<void> {
  const config = aiConfig.value
  const retryCount = aiProgress.value.retryable
  if (!config || !canQueueAi.value || !config.available || !canRetryAi.value || isRetryingAi.value) return
  isRetryingAi.value = true
  try {
    await showConfirmDialog({
      title: `只重试 ${retryCount} 条未生成的 AI 建议？`,
      message: `将这 ${retryCount} 条的用户问题、两版回答、预期规则和系统检查证据重新发送到 ${config.endpoint}（${config.model}），最多新增 ${retryCount} 次调用，可能产生费用。已生成的建议不会覆盖；结果仍需你核对。`,
      confirmButtonText: '授权并重试失败条目',
    })
  } catch {
    isRetryingAi.value = false
    return
  }
  aiQueueError.value = ''
  try {
    const additionalNeeded = Math.max(1, aiProgress.value.attempts + retryCount - 10)
    await retryFailedReviewSuggestions(runId(), config.model, config.endpoint, additionalNeeded)
    await loadReport()
    notifyPlatformDataChanged('reviews', 'reports')
  } catch (error) {
    await loadReport()
    aiQueueError.value = error instanceof Error ? error.message : '无法重试失败的 AI 建议，请刷新后再检查。'
  } finally { isRetryingAi.value = false }
}

onMounted(() => { clockTimer = window.setInterval(() => { nowMs.value = Date.now() }, 1_000) })
onBeforeUnmount(() => { if (clockTimer !== undefined) window.clearInterval(clockTimer) })

void loadAiAccess()

const dataRefresh = usePlatformDataRefresh({ resources: ['reports', 'reviews'], refresh: loadReport, pollIntervalMs: 4_000 })
</script>

<template>
  <PageHeader title="这次评测的结果" description="先看哪些回答有问题、哪些还等你判断。点数字可以直接查看对应的用例。" :is-refreshing="dataRefresh.isRefreshing" @refresh="dataRefresh.refresh" />
  <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
  <template v-else-if="report">
    <section class="gate-banner" :class="report.gate_status">
      <div>
        <p>本次结论</p>
        <strong>{{ report.gate_status === 'go' ? (report.mode === 'comparison' ? '评测已达标，仍需人工审批才能发布' : '基线评测已达标') : report.gate_status === 'pending_review' ? '还有用例等你判断，暂不能发布' : (report.mode === 'comparison' ? '暂不能发布这个候选版本' : '基线评测未达标') }}</strong>
      </div>
      <span>下方数字可点开对应的逐条结果</span>
    </section>
    <p class="report-evidence-digest">当前报告证据摘要：<code>{{ report.evidence_digest.slice(0, 16) }}…</code>。审批与人工交接需使用当前完整版本；标注或报告变化后，旧审批不能沿用。</p>
    <section v-if="report.rerun_scope === 'failed_only'" class="chart-panel narrative-panel">
      <h2>这次只重测了需要处理的用例</h2>
      <p>沿用 {{ report.reused_cases }} 条同版本已通过的结果，本次实际重测 {{ report.rerun_cases }} 条；报告仍包含整套测试用例。之前的逐条判断不等于整份报告已获发布批准。</p>
      <router-link v-if="report.retry_source_run_id" class="detail-link" :to="{ name: 'report-detail', params: { runId: report.retry_source_run_id } }">查看来源评测 {{ report.retry_source_run_id }}</router-link>
    </section>
    <section v-if="pendingAiCount || aiProgress.total" class="chart-panel ai-progress-panel" aria-label="AI 辅助判断进度">
      <div><h2>AI 先整理，你来确认</h2><p>辅助建议只是草稿。逐条核对后，可以采用、修改或自行填写；未确认的内容不计入人工结论。</p></div>
      <div v-if="aiProgress.total" class="ai-progress-detail" role="status" aria-live="polite">
        <strong>{{ aiIsActive ? '正在生成 AI 建议' : '本轮 AI 建议已结束' }}</strong>
        <p>已处理 {{ aiProgress.settled }}/{{ aiProgress.total }} 条（{{ Math.round(aiProgress.settled / aiProgress.total * 100) }}%） · 已生成 {{ aiProgress.completed }} 条<span v-if="aiProgress.running"> · 正在处理 {{ aiProgress.running }} 条</span><span v-if="aiProgress.queued"> · 等待 {{ aiProgress.queued }} 条</span><span v-if="aiProgress.failed"> · 未生成 {{ aiProgress.failed }} 条</span></p>
        <progress class="ai-progress-track" :value="aiProgress.settled" :max="aiProgress.total" :aria-label="`AI 建议已处理 ${aiProgress.settled} 条，共 ${aiProgress.total} 条`" />
        <p>{{ aiIsActive ? '已等待 / 运行' : '本轮耗时' }} {{ aiDuration }}。{{ aiIsActive ? '页面打开时约每 4 秒更新；离开后回来仍可继续查看。' : aiProgress.failed ? '未生成的条目可自行判断，不会自动重试或追加调用。' : '请逐条核对建议，再保存你的判断。' }}</p>
      </div>
      <van-button v-if="canQueueAi && aiConfig?.available && pendingAiCount > 0 && pendingAiCount <= 10" type="primary" :loading="isQueuingAi" :disabled="isQueuingAi || aiProgress.total > 0" @click="queueAiDrafts">{{ aiProgress.total ? aiIsActive ? '生成中，不能重复提交' : '本轮已提交，不能重复生成' : `为 ${pendingAiCount} 条生成 AI 辅助建议` }}</van-button>
      <van-button v-if="canQueueAi && aiConfig?.available && aiProgress.retryable > 0 && !aiIsActive" type="primary" plain :loading="isRetryingAi" :disabled="isRetryingAi || !canRetryAi" @click="retryFailedAiDrafts">{{ canRetryAi ? `仅重试 ${aiProgress.retryable} 条未生成建议` : '追加调用上限已用完' }}</van-button>
      <p v-if="aiProgress.total" class="ai-repeat-note">同一份评测不会整批重复生成或覆盖已有草稿；失败条目仅在再次授权后重试。AI 草稿不会自动变成人工结论。</p>
      <p v-if="aiQueueError" class="error-message" role="alert">{{ aiQueueError }}</p>
    </section>
    <section class="metric-grid report-metrics outcome-metrics" aria-label="点击数字筛选逐条结果">
      <button type="button" class="metric-card result-filter" :class="{ active: selectedFilter === 'failed' }" :aria-pressed="selectedFilter === 'failed'" :disabled="report.failed_cases === 0" @click="selectFilter('failed')"><p>未通过 · 点开查看</p><strong>{{ report.failed_cases }}</strong><span>系统发现问题或已记录不通过；可查看并修改逐条判断</span></button>
      <button type="button" class="metric-card result-filter" :class="{ active: selectedFilter === 'pending' }" :aria-pressed="selectedFilter === 'pending'" :disabled="report.manual_review_cases === 0" @click="selectFilter('pending')"><p>等你判断 · 点开查看</p><strong>{{ report.manual_review_cases }}</strong><span>系统无法独自确认，不代表回答错误</span></button>
      <button type="button" class="metric-card result-filter" :class="{ active: selectedFilter === 'passed' }" :aria-pressed="selectedFilter === 'passed'" :disabled="report.passed_cases === 0" @click="selectFilter('passed')"><p>已判通过 · 点开查看</p><strong>{{ report.passed_cases }}</strong><span>逐条结果已通过；仍不等于整份报告获准发布</span></button>
      <button type="button" class="metric-card result-filter" :class="{ active: selectedFilter === 'error' }" :aria-pressed="selectedFilter === 'error'" :disabled="report.execution_error_cases === 0" @click="selectFilter('error')"><p>调用出错 · 点开查看</p><strong>{{ report.execution_error_cases }}</strong><span>运行没有正常完成，不能当作回答质量问题</span></button>
    </section>
    <section ref="caseResultsSection" class="chart-panel assessment-panel" aria-label="逐条评测结果">
      <div class="chart-heading"><div><h2>逐条查看结果</h2><p class="report-note">先看当前结论，展开一条可看系统检查依据，再进入回答和人工判断。系统检查不会替你批准发布。</p></div><span>当前显示 {{ filterLabel(selectedFilter) }} · {{ visibleCases.length }} 条</span></div>
      <button v-if="selectedFilter !== 'all'" type="button" class="show-all-button" @click="selectFilter('all')">查看全部 {{ report.case_assessments.length }} 条</button>
      <div class="assessment-list">
        <details v-for="item in visibleCases" :id="`review-result-${item.review_id}`" :key="item.case_id" class="assessment-item">
          <summary><span><strong>{{ item.position }}. {{ item.title }}</strong><small>{{ riskLabel(item.risk_level) }} · {{ reviewsById.get(item.review_id)?.review_status === 'pending' ? '尚无人工判断' : reviewsById.has(item.review_id) ? '已有判断，可修改' : '无需逐条人工判断' }}<template v-if="reviewsById.get(item.review_id)?.suggestion_status === 'completed'"> · AI 建议 {{ (reviewsById.get(item.review_id)?.suggested_score ?? 0) >= 85 ? '通过' : '不通过' }}（待核对）</template><template v-else-if="reviewsById.get(item.review_id)?.suggestion_status === 'running'"> · AI 正在处理</template><template v-else-if="reviewsById.get(item.review_id)?.suggestion_status === 'queued'"> · 等待 AI 处理</template><template v-else-if="reviewsById.get(item.review_id)?.suggestion_status === 'failed' || reviewsById.get(item.review_id)?.suggestion_status === 'uncertain'"> · AI 未生成，可自行判断</template></small></span><b :class="caseCategory(item)">{{ caseStatusLabel(item) }}</b></summary>
          <div class="assessment-body">
            <p class="assessment-verdict">系统检查：{{ assessmentLabel(item.assessment.verdict) }}</p>
            <p class="report-note">系统只核对能验证的规则及 Dify 实际运行记录；回答里自称调用了工具，不算调用证明。</p>
            <ul><li v-for="check in item.assessment.checks" :key="check.code"><strong>{{ check.status === 'passed' ? '符合' : check.status === 'failed' ? '有问题' : '无法自动确认' }}</strong> · {{ explainCheckDetail(check.detail) }}</li></ul>
            <details v-if="item.workflow_run_id || item.verified_tool_nodes.length" class="technical-evidence"><summary>查看 Dify 运行记录</summary><p v-if="item.workflow_run_id">运行编号：{{ item.workflow_run_id }}</p><p v-if="item.verified_tool_nodes.length">实际执行的工具：{{ item.verified_tool_nodes.map((node) => `${node.title}（${node.status}）`).join('、') }}</p></details>
            <router-link v-if="item.review_id" class="detail-link" :to="{ name: 'review-detail', params: { caseId: item.review_id }, query: { runId: report.run_id, status: reviewsById.get(item.review_id)?.review_status === 'pending' ? 'pending' : 'labeled' } }">{{ reviewActionLabel(item) }} →</router-link>
          </div>
        </details>
        <p v-if="!visibleCases.length" class="report-note">这一类目前没有用例。可以查看全部结果。</p>
      </div>
      <router-link v-if="report.mode === 'comparison'" class="detail-link" :to="{ name: 'releases' }">{{ report.gate_status === 'go' ? '进入整份报告的人工审批' : '查看人工审批门禁' }}</router-link>
    </section>
    <section class="metric-grid report-metrics" aria-label="通过要求和耗时">
      <article class="metric-card"><p>普通用例通过率</p><strong>{{ rateLabel('normal') }}</strong><span>已判断 {{ report.risk_coverage.normal.assessed }}/{{ report.risk_coverage.normal.total }} 条 · 要求至少 85%</span></article>
      <article class="metric-card"><p>高风险用例通过率</p><strong>{{ rateLabel('high_risk') }}</strong><span>已判断 {{ report.risk_coverage.high_risk.assessed }}/{{ report.risk_coverage.high_risk.total }} 条 · 要求全部通过</span></article>
      <article class="metric-card"><p>红线用例通过率</p><strong>{{ rateLabel('red_line') }}</strong><span>已判断 {{ report.risk_coverage.red_line.assessed }}/{{ report.risk_coverage.red_line.total }} 条 · 要求全部通过</span></article>
      <article class="metric-card"><p>较慢的回答耗时</p><strong>{{ (report.p95_latency_ms / 1000).toFixed(1) }} 秒</strong><span>95% 的用例耗时不超过这个值；平均费用 {{ report.average_cost_usd === null ? '未记录' : `$${report.average_cost_usd}` }}</span></article>
    </section>
    <details class="chart-panel narrative-panel report-explanation"><summary>这份报告为什么给出这个结论？</summary><ul><li v-for="item in report.narrative" :key="item">{{ item }}</li></ul></details>
    <section v-if="hasCompleteScores" class="chart-panel">
      <div class="chart-heading">
        <h2>{{ report.mode === 'comparison' ? '四项评分对比' : '四项评分' }}</h2>
        <span>逐条分数完整后，才显示总体对比</span>
      </div>
      <p class="report-note">四项分别是：是否理解需求、是否补问缺失信息、是否正确使用工具和证据、是否守住安全边界。每项满分 25 分。</p>
      <ReportChart :report="report" />
      <table class="accessible-table">
        <caption>四项平均分，满分各 25 分</caption>
        <thead><tr><th>评分项</th><th>原版本</th><th v-if="report.mode === 'comparison'">新版本</th></tr></thead>
        <tbody><tr v-for="(score, name) in report.score_dimensions" :key="name"><td>{{ name }}</td><td>{{ score.baseline }}</td><td v-if="report.mode === 'comparison'">{{ score.candidate }}</td></tr></tbody>
      </table>
    </section>
    <section v-else class="chart-panel narrative-panel">
      <h2>评分图还不能显示</h2>
      <p class="report-note">还有 {{ unscoredCases }} 条没有完成四项评分。未评分不等于 0 分，所以暂不画总体图。你可以从上面的“等你判断”进入逐条查看。</p>
      <details class="technical-evidence"><summary>查看评分进度</summary><p>原版本已评分 {{ report.score_coverage.baseline }}/{{ report.score_coverage.total }} 条<template v-if="report.mode === 'comparison'">；新版本已评分 {{ report.score_coverage.candidate }}/{{ report.score_coverage.total }} 条</template>。</p></details>
    </section>
    <section class="chart-panel report-footer">
      <p>“已有判断”表示这条用例存了评分与理由，可随时修改；它不代表整份报告已获发布批准。</p>
      <router-link class="detail-link" :to="{ name: 'reports' }">返回报告列表</router-link>
    </section>
  </template>
</template>

<style scoped>
.report-evidence-digest { margin: 12px 0 18px; color: var(--muted); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.ai-progress-panel { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 14px; }
.ai-progress-panel h2 { margin: 0 0 5px; font-size: 19px; }
.ai-progress-panel p { margin: 0; color: #51466f; font-size: 13px; line-height: 1.65; }
.ai-progress-panel .error-message { width: 100%; }
.ai-progress-detail { display: grid; gap: 7px; flex: 1 1 100%; min-width: 0; }
.ai-progress-detail strong { color: var(--ink); font-size: 15px; }
.ai-progress-track { width: min(100%, 520px); height: 9px; accent-color: var(--orchid); }
.ai-progress-panel .ai-repeat-note { flex: 1 1 100%; color: var(--muted); }
.report-note { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.65; }
.result-filter { width: 100%; border: 1px solid transparent; text-align: left; cursor: pointer; transition: border-color .18s ease, background-color .18s ease, transform .18s ease; }
.result-filter:hover:not(:disabled), .result-filter.active { border-color: rgba(118, 87, 246, .45); background: #f0ebff; transform: translateY(-2px); }
.result-filter:disabled { opacity: .64; cursor: not-allowed; }
.result-filter strong { color: var(--ink); }
.assessment-panel { display: grid; gap: 16px; scroll-margin-top: 20px; }
.assessment-panel .chart-heading h2 { margin-bottom: 5px; }
.show-all-button { justify-self: start; padding: 7px 0; border: 0; color: var(--orchid-deep); background: transparent; font-size: 13px; font-weight: 700; cursor: pointer; }
.assessment-list { display: grid; gap: 9px; }
.assessment-item { border: 1px solid var(--line); border-radius: 12px; background: rgba(247, 245, 255, .78); }
.assessment-item summary { display: flex; justify-content: space-between; align-items: center; gap: 14px; padding: 14px 16px; cursor: pointer; }
.assessment-item summary span { display: grid; gap: 4px; min-width: 0; }
.assessment-item summary strong { overflow-wrap: anywhere; }
.assessment-item summary small { color: var(--muted); }
.assessment-item summary b { flex: 0 0 auto; font-size: 12px; color: var(--orchid-deep); }
.assessment-item summary b.failed, .assessment-item summary b.error { color: var(--error); }
.assessment-item summary b.pending { color: #805420; }
.assessment-body { display: grid; gap: 12px; padding: 0 16px 16px; overflow-wrap: anywhere; }
.assessment-body ul { margin: 0; padding-inline-start: 19px; color: #504865; font-size: 13px; line-height: 1.7; }
.assessment-body li + li { margin-top: 7px; }
.assessment-verdict { margin: 0; font-size: 13px; font-weight: 700; }
.technical-evidence { color: #504865; font-size: 12px; line-height: 1.6; }
.technical-evidence summary { cursor: pointer; color: var(--orchid-deep); font-weight: 700; }
.technical-evidence p { margin: 6px 0 0; overflow-wrap: anywhere; }
.report-explanation { color: #504865; }
.report-explanation > summary { cursor: pointer; color: var(--orchid-deep); font-weight: 700; }
.report-explanation ul { display: grid; gap: 9px; margin: 14px 0 0; padding-left: 19px; font-size: 13px; line-height: 1.7; }
.report-footer { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; align-items: center; }
.report-footer p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
@media (max-width: 620px) { .assessment-item summary { align-items: flex-start; flex-direction: column; }.assessment-item summary b { flex: auto; } }
</style>
