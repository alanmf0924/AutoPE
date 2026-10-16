<script setup lang="ts">
import { ref } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import { listRuns } from '@/services/api'
import { usePlatformDataRefresh } from '@/services/dataRefresh'
import { formatBackendTime } from '@/services/time'
import type { EvaluationRun } from '@/types'

const completedRuns = ref<EvaluationRun[]>([])
const errorMessage = ref('')

async function loadReports(): Promise<void> {
  errorMessage.value = ''
  try {
    const runs = await listRuns()
    completedRuns.value = runs.filter((run) => run.status === 'completed')
  } catch {
    errorMessage.value = '无法读取评测报告列表。请检查后端服务。'
  }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['reports', 'runs'], refresh: loadReports, pollIntervalMs: 4_000 })
</script>

<template>
  <PageHeader title="评测报告" description="每次评测单独一份报告。先看能否进入发布审批、哪些回答需要处理，再逐条查看依据。" :is-refreshing="dataRefresh.isRefreshing" @refresh="dataRefresh.refresh" />
  <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
  <section v-else-if="completedRuns.length" class="data-list">
    <article v-for="run in completedRuns" :key="run.id" class="row-card">
      <div><h2>{{ run.id }}</h2><p>数据集：{{ run.dataset_id }} · 候选：{{ run.candidate_id }}</p></div>
      <div class="row-meta"><strong>已完成</strong><small>{{ formatBackendTime(run.completed_at || run.created_at) }}</small><router-link class="detail-link" :to="{ name: 'report-detail', params: { runId: run.id } }">查看结果与逐条依据</router-link></div>
    </article>
  </section>
  <section v-else class="empty-panel"><h2>尚无已完成报告</h2><p>请前往“评测运行”创建任务；运行完成后会自动出现在这里。</p></section>
</template>
