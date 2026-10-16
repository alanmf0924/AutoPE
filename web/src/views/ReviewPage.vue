<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import { getSession, listDatasets, listReviewCases, listRuns } from '@/services/api'
import { usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import { formatBackendTime } from '@/services/time'
import type { Dataset, EvaluationRun, ReviewCase } from '@/types'

const route = useRoute()
const reviewCases = ref<ReviewCase[]>([])
const runs = ref<EvaluationRun[]>([])
const datasets = ref<Dataset[]>([])
const errorMessage = ref('')
const searchText = ref('')
const activeTab = ref<'pending' | 'labeled'>(route.query.status === 'labeled' ? 'labeled' : 'pending')
const selectedRunId = ref(typeof route.query.runId === 'string' ? route.query.runId : '')
const canAnnotate = computed(() => activeSession.value?.allowed_actions.includes('reviews.annotate') ?? false)
const pendingCount = computed(() => reviewCases.value.filter((item) => item.review_status === 'pending').length)
const labeledCount = computed(() => reviewCases.value.length - pendingCount.value)
const groupedCases = computed(() => {
  const needle = searchText.value.trim().toLocaleLowerCase()
  const visible = reviewCases.value.filter((item) => {
    const statusMatches = activeTab.value === 'pending' ? item.review_status === 'pending' : item.review_status !== 'pending'
    const runMatches = !selectedRunId.value || item.source_run_id === selectedRunId.value
    const textMatches = !needle || `${item.title} ${item.source_run_id}`.toLocaleLowerCase().includes(needle)
    return statusMatches && runMatches && textMatches
  })
  const byRun = new Map<string, ReviewCase[]>()
  for (const item of visible) {
    const group = byRun.get(item.source_run_id) ?? []
    group.push(item)
    byRun.set(item.source_run_id, group)
  }
  return [...byRun.entries()].map(([runId, items]) => {
    const run = runs.value.find((entry) => entry.id === runId)
    const dataset = datasets.value.find((entry) => entry.id === run?.dataset_id)
    const allForRun = reviewCases.value.filter((entry) => entry.source_run_id === runId)
    return { runId, run, dataset, items, pending: allForRun.filter((entry) => entry.review_status === 'pending').length, labeled: allForRun.filter((entry) => entry.review_status !== 'pending').length }
  }).sort((left, right) => (right.run?.created_at ?? '').localeCompare(left.run?.created_at ?? ''))
})

async function loadCases(): Promise<void> {
  try {
    const identity = await getSession()
    const canReadCatalogs = identity.roles.some((role) => role === 'operator' || role === 'auditor')
    const [caseItems, runItems, datasetItems] = await Promise.all([
      listReviewCases(),
      canReadCatalogs ? listRuns() : Promise.resolve([]),
      canReadCatalogs ? listDatasets() : Promise.resolve([]),
    ])
    reviewCases.value = caseItems
    runs.value = runItems
    datasets.value = datasetItems
    errorMessage.value = ''
  } catch {
    errorMessage.value = '暂时无法读取逐条判断记录。请检查服务后重试。'
  }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['reviews', 'runs', 'reports'], refresh: loadCases, pollIntervalMs: 4_000 })
</script>

<template>
  <PageHeader :title="canAnnotate ? '逐条确认回答' : '逐条判断记录'" :description="canAnnotate ? '按每次评测分组。系统无法判断或发现问题的回答在这里处理；已记录的判断可以再次修改。' : '按每次评测分组，只读查看待处理与已记录的人工判断。'" :is-refreshing="dataRefresh.isRefreshing" @refresh="dataRefresh.refresh" />
  <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
  <section class="review-toolbar" aria-label="逐条判断筛选">
    <div class="review-tabs" role="group" aria-label="判断状态">
      <button type="button" :class="{ active: activeTab === 'pending' }" :aria-pressed="activeTab === 'pending'" @click="activeTab = 'pending'">{{ canAnnotate ? '等你判断' : '待人工判断' }} <span>{{ pendingCount }}</span></button>
      <button type="button" :class="{ active: activeTab === 'labeled' }" :aria-pressed="activeTab === 'labeled'" @click="activeTab = 'labeled'">已有判断 <span>{{ labeledCount }}</span></button>
    </div>
    <select v-model="selectedRunId" aria-label="筛选评测运行"><option value="">全部评测运行</option><option v-for="run in runs.filter((entry) => reviewCases.some((item) => item.source_run_id === entry.id))" :key="run.id" :value="run.id">{{ run.mode === 'comparison' ? '候选对比' : '基线回归' }} · {{ run.id }}</option></select>
    <input v-model.trim="searchText" type="search" aria-label="搜索用例或运行编号" placeholder="搜索用例或运行编号" />
  </section>
  <section v-if="groupedCases.length" class="review-groups" aria-label="按评测运行分组的逐条判断">
    <details v-for="(group, index) in groupedCases" :key="group.runId" class="review-group" :open="index === 0">
      <summary><span class="group-heading"><strong>{{ group.run?.mode === 'comparison' ? '新旧版本对比' : '原版本测试' }} · {{ group.dataset?.name ?? group.run?.dataset_id ?? '未知测试集' }}</strong><small>评测 {{ group.runId }} · {{ group.run ? formatBackendTime(group.run.created_at) : '时间未知' }}</small></span><span class="group-counts">等你判断 {{ group.pending }} · 已有判断 {{ group.labeled }}</span></summary>
      <div class="data-list group-items"><article v-for="item in group.items" :key="item.id" class="row-card"><div><h2>{{ item.title }}</h2><p>{{ item.risk_level === 'normal' ? '普通' : item.risk_level === 'high_risk' ? '高风险' : '红线' }} · {{ item.review_status === 'pending' ? '尚无人工判断' : item.review_status === 'approved' ? '已有判断：通过' : '已有判断：未通过' }} · {{ item.suggestion_status === 'completed' && item.review_status === 'pending' ? `AI 建议 ${item.suggested_score}/100 分，待你核对 · ` : item.suggestion_status === 'running' || item.suggestion_status === 'queued' ? 'AI 正在整理 · ' : '' }}更新于 {{ formatBackendTime(item.updated_at) }}</p></div><div class="row-meta"><strong>{{ item.review_status === 'pending' ? '待处理' : `${item.score}/100 分` }}</strong><router-link class="detail-link" :to="{ name: 'review-detail', params: { caseId: item.id }, query: { status: activeTab, runId: group.runId } }">{{ !canAnnotate ? '查看判断证据' : item.review_status === 'pending' && item.suggestion_status === 'completed' ? '核对 AI 建议' : item.review_status === 'pending' ? '查看并判断' : '查看并修改判断' }}</router-link></div></article></div>
    </details>
  </section>
  <section v-else-if="!errorMessage" class="empty-panel"><h2>{{ activeTab === 'pending' ? '当前没有等你判断的回答' : '当前没有已记录的判断' }}</h2><p>可以切换状态、评测运行或清除搜索条件。</p></section>
</template>

<style scoped>
.review-toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 18px; }
.review-toolbar select, .review-toolbar input { width: min(100%, 260px); }
.review-tabs { display: flex; gap: 5px; padding: 4px; background: rgba(237, 232, 255, .8); border-radius: 12px; }
.review-tabs button { padding: 9px 14px; border: 0; border-radius: 9px; color: #544a70; background: transparent; cursor: pointer; font-weight: 700; }
.review-tabs button.active { color: #fff; background: var(--orchid-deep); }
.review-tabs span { margin-left: 5px; font-variant-numeric: tabular-nums; }
.review-groups { display: grid; gap: 14px; }
.review-group { overflow: hidden; border: 1px solid var(--line); border-radius: 16px; background: var(--panel); box-shadow: 0 10px 24px rgba(81, 64, 137, .06); }
.review-group summary { display: flex; justify-content: space-between; gap: 14px; align-items: center; padding: 18px 20px; cursor: pointer; }
.review-group summary::marker { color: var(--orchid-deep); }
.group-heading { display: inline-grid; gap: 5px; min-width: 0; }
.group-heading strong { color: var(--ink); font-size: 16px; }
.group-heading small { color: var(--muted); overflow-wrap: anywhere; }
.group-counts { flex: 0 0 auto; color: var(--orchid-deep); font-size: 12px; font-weight: 700; }
.group-items { padding: 0 14px 14px; }
.group-items .row-card { box-shadow: none; background: rgba(247, 245, 255, .8); }
@media (max-width: 620px) { .review-group summary { align-items: flex-start; flex-direction: column; }.group-counts { padding-left: 4px; }.review-toolbar select, .review-toolbar input { width: 100%; } }
</style>
