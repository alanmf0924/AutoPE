<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import { attestCandidateBinding, getCandidate } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import { formatBackendTime } from '@/services/time'
import type { PromptCandidateDetail } from '@/types'

const route = useRoute()
const candidate = ref<PromptCandidateDetail>()
const canManageCandidates = computed(() => activeSession.value?.allowed_actions.includes('candidates.manage') ?? false)
const errorMessage = ref('')
const actionError = ref('')
const isLoading = ref(true)
const isSubmitting = ref(false)
const attestation = reactive({ published_workflow_id: '', published_prompt_block: '', review_note: '', human_confirmed: false })
const candidateId = computed(() => typeof route.params.candidateId === 'string' ? route.params.candidateId : '')

async function loadCandidate(): Promise<void> {
  if (!candidateId.value) {
    errorMessage.value = '候选编号无效，请返回候选列表。'
    isLoading.value = false
    return
  }
  try {
    candidate.value = await getCandidate(candidateId.value)
    errorMessage.value = ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取候选详情，请检查后端连接。'
  } finally {
    isLoading.value = false
  }
}

async function submitAttestation(): Promise<void> {
  if (!candidate.value || isSubmitting.value) return
  isSubmitting.value = true
  actionError.value = ''
  try {
    candidate.value = await attestCandidateBinding(candidate.value.id, attestation)
    attestation.published_prompt_block = ''
    attestation.review_note = ''
    attestation.human_confirmed = false
    notifyPlatformDataChanged('candidates', 'runs', 'dashboard')
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '绑定核对未保存。'
  } finally {
    isSubmitting.value = false
  }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['candidates', 'runs'], refresh: loadCandidate, pollIntervalMs: 8_000 })
</script>

<template>
  <PageHeader title="候选详情" description="核对完整 Prompt、生成来源、隔离候选应用和每次评测；候选提案并不代表基线应用已发布。" :is-refreshing="dataRefresh.isRefreshing" @refresh="dataRefresh.refresh" />
  <p v-if="isLoading" class="helper-copy" role="status">正在读取候选详情…</p>
  <p v-else-if="errorMessage" class="error-message" role="alert">{{ errorMessage }} <router-link :to="{ name: 'optimizer' }">返回候选列表</router-link></p>
  <template v-else-if="candidate">
    <section class="detail-summary" aria-label="候选摘要">
      <div><span>候选状态</span><strong>{{ candidate.binding_attested ? '人工核对已记录' : candidate.status === 'ready_for_evaluation' ? '已绑定，待版本核对' : candidate.status === 'rejected' ? '已驳回' : '待绑定隔离应用' }}</strong></div>
      <div><span>生成来源</span><strong>{{ candidate.generation_job ? 'DSPy 生成任务' : '未关联生成任务' }}</strong></div>
      <div><span>关联评测</span><strong>{{ candidate.evaluation_run_count }} 次</strong></div>
    </section>
    <section v-if="candidate.bound_target_id" class="chart-panel">
      <h2>候选与 Dify 已发布版本核对</h2>
      <p class="helper-copy">由 Prompt 负责人从隔离 Dify 应用复制已发布版本 ID 与对应的候选 Prompt 块。系统核对版本 ID 和 SHA-256，并保留人员、时间及备注；这是人工声明，未通过 Dify 控制台 API 独立验证。</p>
      <p v-if="candidate.binding_attested" class="helper-copy">已记录：{{ candidate.binding_attestation.actor_id }} · {{ formatBackendTime(candidate.binding_attestation.attested_at || '') }} · {{ candidate.binding_attestation.published_workflow_id }}。{{ candidate.binding_attestation.review_note }}</p>
      <form v-else-if="canManageCandidates" class="attestation-form" @submit.prevent="submitAttestation">
        <label>已发布工作流版本 ID<input v-model.trim="attestation.published_workflow_id" required placeholder="Dify 已发布 workflow UUID" /></label>
        <label>从已发布版本复制的候选 Prompt 块<textarea v-model="attestation.published_prompt_block" required rows="7" placeholder="粘贴对应候选 Prompt 块，系统仅保存哈希" /></label>
        <label>核对备注<input v-model.trim="attestation.review_note" required minlength="8" placeholder="说明核对的应用、版本与操作" /></label>
        <label><input v-model="attestation.human_confirmed" type="checkbox" required /> 我已在隔离 Dify 应用核对上述已发布版本</label>
        <button class="primary-button" type="submit" :disabled="isSubmitting">{{ isSubmitting ? '核对中…' : '保存人工核对记录' }}</button>
        <p v-if="actionError" class="error-message" role="alert">{{ actionError }}</p>
      </form>
    </section>
    <section class="chart-panel candidate-identity">
      <div class="heading-row"><div><h2>{{ candidate.name }}</h2><p>{{ candidate.id }} · {{ formatBackendTime(candidate.created_at) }}</p></div><router-link class="detail-link" :to="{ name: 'optimizer' }">返回候选列表</router-link></div>
      <dl>
        <div><dt>基线版本</dt><dd>{{ candidate.base_version_fingerprint }}</dd></div>
        <div><dt>完整 Prompt SHA-256</dt><dd class="digest">{{ candidate.prompt_sha256 }}</dd></div>
        <div><dt>隔离候选 Dify 应用</dt><dd v-if="candidate.bound_target_id"><router-link :to="{ name: 'target-detail', params: { targetId: candidate.bound_target_id } }">{{ candidate.bound_target_name || candidate.bound_target_id }}</router-link><small>{{ candidate.bound_target_version_fingerprint }}</small></dd><dd v-else>尚未绑定，不能创建候选对比评测。</dd></div>
        <div v-if="candidate.generation_job"><dt>生成任务</dt><dd>{{ candidate.generation_job.id }} · 黄金集 {{ candidate.generation_job.dataset_id }} · {{ candidate.generation_job.status }}</dd></div>
      </dl>
    </section>
    <section class="chart-panel prompt-panel"><div class="heading-row"><div><h2>完整候选 Prompt</h2><p>以下为保存时的正文，可按上方哈希核对版本；修改需重新登记候选。</p></div></div><pre>{{ candidate.prompt_block }}</pre></section>
    <section class="chart-panel"><h2>关联评测</h2><p v-if="candidate.evaluation_run_count > candidate.evaluation_runs.length" class="helper-copy">仅显示最近 100 次；共 {{ candidate.evaluation_run_count }} 次。</p><div v-if="candidate.evaluation_runs.length" class="data-list"><article v-for="run in candidate.evaluation_runs" :key="run.id" class="row-card"><div><h3>{{ run.id }}</h3><p>黄金集 {{ run.dataset_id }} · {{ run.status }} · {{ formatBackendTime(run.created_at) }}</p></div><router-link class="detail-link" :to="run.status === 'completed' ? { name: 'report-detail', params: { runId: run.id } } : { name: 'runs' }">{{ run.status === 'completed' ? '查看评测报告' : '查看运行状态' }}</router-link></article></div><p v-else class="helper-copy">尚未评测。绑定隔离应用并完成已发布版本与 Prompt 哈希核对后，才能在“评测运行”创建对比。</p></section>
  </template>
</template>

<style scoped>
.detail-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 15px; margin-bottom: 21px; }
.detail-summary div { padding: 20px; background: var(--panel); box-shadow: 0 10px 24px rgba(81, 64, 137, .07); border-radius: 15px; }
.detail-summary span { display: block; color: var(--muted); font-size: 12px; }
.detail-summary strong { display: block; margin-top: 8px; color: var(--orchid-deep); font-size: 17px; }
.chart-panel { margin-bottom: 21px; }
.heading-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.heading-row h2, .heading-row p { margin: 0; }
.heading-row p { margin-top: 6px; color: var(--muted); font-size: 13px; }
dl { display: grid; gap: 14px; margin: 20px 0 0; }
dl div { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 12px; }
dt { color: var(--muted); font-size: 13px; }
dd { margin: 0; overflow-wrap: anywhere; color: var(--ink); }
dd small { display: block; color: var(--muted); }
.digest { font-variant-numeric: tabular-nums; }
.attestation-form { display: grid; gap: 15px; margin-top: 18px; }
.attestation-form label { display: grid; gap: 7px; }
.attestation-form input:not([type='checkbox']), .attestation-form textarea { width: 100%; box-sizing: border-box; }
.prompt-panel pre { max-height: 560px; overflow: auto; margin: 18px 0 0; padding: 18px; border-radius: 12px; background: rgba(247, 245, 255, .9); color: var(--ink); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.row-card h3 { margin: 0; font-size: 15px; overflow-wrap: anywhere; }
@media (max-width: 620px) { .detail-summary { grid-template-columns: 1fr; } .heading-row, dl div { display: block; } dt { margin-bottom: 4px; } .heading-row .detail-link { display: inline-block; margin-top: 12px; } }
</style>
