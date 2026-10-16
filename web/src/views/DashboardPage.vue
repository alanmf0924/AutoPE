<script setup lang="ts">
import { computed, ref } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import { getDashboard, listCandidateGenerationJobs, listDatasets, listReviewCases, listRuns, listTargets } from '@/services/api'
import { usePlatformDataRefresh } from '@/services/dataRefresh'
import type { DashboardReport, EvaluationRun } from '@/types'

const targetCount = ref(0)
const datasetCount = ref(0)
const runCount = ref(0)
const pendingReviews = ref(0)
const pendingDatasets = ref(0)
const activeRuns = ref(0)
const activeCandidateJobs = ref(0)
const latestRun = ref<EvaluationRun>()
const latestReport = ref<DashboardReport>()
const isLoading = ref(true)
const errorMessage = ref('')
const updatedAt = ref<Date>()

const cards = computed(() => [
  { label: '待人工标注', value: pendingReviews.value, hint: '按评测运行查看，不计为模型失败', to: { name: 'reviews' } },
  { label: '运行中的任务', value: activeRuns.value + activeCandidateJobs.value, hint: `${activeRuns.value} 次评测 · ${activeCandidateJobs.value} 个候选生成`, to: { name: 'runs' } },
  { label: '待批准测试集', value: pendingDatasets.value, hint: '冻结与批准分开，未批准不可评测', to: { name: 'datasets' } },
])

async function loadDashboard(): Promise<void> {
  try {
    const [targets, datasets, runs, reviews, jobs] = await Promise.all([
      listTargets(), listDatasets(), listRuns(), listReviewCases(), listCandidateGenerationJobs(),
    ])
    targetCount.value = targets.length
    datasetCount.value = datasets.length
    runCount.value = runs.length
    pendingReviews.value = reviews.filter((item) => item.review_status === 'pending').length
    pendingDatasets.value = datasets.filter((item) => item.review_status === 'in_review').length
    activeRuns.value = runs.filter((item) => item.status === 'queued' || item.status === 'running').length
    activeCandidateJobs.value = jobs.filter((item) => item.status === 'queued' || item.status === 'running').length
    latestRun.value = runs.find((item) => item.status === 'completed')
    latestReport.value = latestRun.value ? await getDashboard(latestRun.value.id) : undefined
    updatedAt.value = new Date()
    errorMessage.value = ''
  } catch {
    errorMessage.value = '摘要刷新失败，以下可能是上次读取的数据；请检查 API 服务。'
  } finally {
    isLoading.value = false
  }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['dashboard', 'targets', 'datasets', 'runs', 'reviews', 'reports', 'candidates', 'releases'], refresh: loadDashboard, pollIntervalMs: 5_000 })
</script>

<template>
  <PageHeader title="先看风险，再进入对应工作台" description="待办、运行状态与最新门禁在这里汇总；具体判定仍以评测报告和业务标注为准。" :is-refreshing="dataRefresh.isRefreshing" @refresh="dataRefresh.refresh" />
  <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
  <p class="refresh-note">{{ updatedAt ? `最后更新 ${updatedAt.toLocaleTimeString()}` : '正在读取最新状态' }} · 页面可见时每 5 秒刷新</p>
  <section class="metric-grid" :aria-busy="isLoading">
    <router-link v-for="card in cards" :key="card.label" :to="card.to" class="metric-card dashboard-card"><p>{{ card.label }}</p><strong>{{ isLoading ? '—' : card.value }}</strong><span>{{ card.hint }}</span></router-link>
  </section>
  <section v-if="latestRun && latestReport" class="notice-panel latest-gate" :class="latestReport.gate_status">
    <div><h2>最近一次评测 · {{ latestRun.mode === 'comparison' ? '候选对比' : '基线回归' }}</h2><span>{{ latestRun.id }} · {{ latestReport.gate_status === 'go' ? '门禁通过，仍需人工发布评审' : latestReport.gate_status === 'pending_review' ? `待标注 ${latestReport.manual_review_cases} 条，不能进入发布审批` : `未通过：真实失败 ${latestReport.failed_cases} 条，调用错误 ${latestReport.execution_error_cases} 条` }}</span></div>
    <router-link class="gate-link" :to="{ name: 'report-detail', params: { runId: latestRun.id } }">查看报告详情 →</router-link>
  </section>
  <section class="chart-panel overview-foot"><h2>平台对象</h2><p>{{ targetCount }} 个 Dify 目标 · {{ datasetCount }} 个测试集 · {{ runCount }} 次评测运行</p><p>流程：测试集冻结与批准 → 基线评测 → 候选生成与隔离应用绑定 → 对比评测 → 按次标注 → 报告门禁 → 人工发布与回滚交接。</p></section>
</template>

<style scoped>
.refresh-note { margin: -13px 0 18px; color: var(--muted); font-size: 12px; }
.dashboard-card { color: inherit; text-decoration: none; transition: transform .18s ease, box-shadow .18s ease; }
.dashboard-card:hover { transform: translateY(-3px); box-shadow: var(--shadow); }
.latest-gate { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.latest-gate.pending_review { background: linear-gradient(112deg, #77502a, #ae7835); }
.latest-gate.no_go { background: linear-gradient(112deg, #a6315d, #cf5886); }
.gate-link { flex: 0 0 auto; color: #fff; font-weight: 700; font-size: 13px; }
.overview-foot p { margin: 8px 0 0; color: var(--muted); font-size: 13px; line-height: 1.7; }
@media (max-width: 620px) { .latest-gate { align-items: flex-start; flex-direction: column; } }
</style>
