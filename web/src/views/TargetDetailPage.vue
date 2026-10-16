<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showConfirmDialog } from 'vant'

import PageHeader from '@/components/PageHeader.vue'
import { deleteTarget, getTarget, updateTarget } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import type { DifyTargetDetail } from '@/types'

const route = useRoute()
const router = useRouter()
const target = ref<DifyTargetDetail>()
const canManageTargets = computed(() => activeSession.value?.allowed_actions.includes('targets.manage') ?? false)
const errorMessage = ref('')
const successMessage = ref('')
const mappingErrorMessage = ref('')
const isSaving = ref(false)
const isDeleting = ref(false)
const form = reactive({
  name: '',
  baseUrl: '',
  runtimePath: '',
  targetKind: 'workflow' as 'workflow' | 'chatflow',
  applicationId: '',
  inputMappingText: '{}',
  difyUser: '',
  apiKey: '',
  versionFingerprint: '',
  publishedWorkflowId: '',
})

function targetId(): string {
  return typeof route.params.targetId === 'string' ? route.params.targetId : ''
}

function parseInputMapping(value: string): Record<string, string> {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error('输入变量映射必须是合法的 JSON 对象。')
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('输入变量映射必须是对象。')
  }
  const mapping: Record<string, string> = {}
  for (const [caseField, difyField] of Object.entries(parsed as Record<string, unknown>)) {
    if (!caseField.trim() || typeof difyField !== 'string' || !difyField.trim()) {
      throw new Error('每条映射都需要非空的用例字段与 Dify 变量名。')
    }
    mapping[caseField.trim()] = difyField.trim()
  }
  return mapping
}

function applyTarget(item: DifyTargetDetail): void {
  target.value = item
  form.name = item.name
  form.baseUrl = item.base_url
  form.runtimePath = item.runtime_path
  form.targetKind = item.target_kind
  form.applicationId = item.application_id
  form.inputMappingText = JSON.stringify(item.input_mapping, null, 2)
  form.difyUser = item.dify_user
  form.apiKey = ''
  form.versionFingerprint = item.version_fingerprint
  form.publishedWorkflowId = item.published_workflow_id
}

async function loadTarget(): Promise<void> {
  errorMessage.value = ''
  try {
    applyTarget(await getTarget(targetId()))
  } catch {
    errorMessage.value = '无法读取这个 Dify 目标。它可能已被删除，或后端服务不可用。'
  }
}

async function saveTarget(): Promise<void> {
  if (isSaving.value || (target.value?.usage_count ?? 0) > 0 || (target.value?.candidate_binding_count ?? 0) > 0) {
    return
  }
  errorMessage.value = ''
  successMessage.value = ''
  mappingErrorMessage.value = ''
  let inputMapping: Record<string, string>
  try {
    inputMapping = parseInputMapping(form.inputMappingText)
  } catch (error) {
    mappingErrorMessage.value = error instanceof Error ? error.message : '输入变量映射格式不正确。'
    return
  }
  isSaving.value = true
  try {
    await updateTarget(targetId(), {
      name: form.name,
      base_url: form.baseUrl,
      runtime_path: form.runtimePath,
      target_kind: form.targetKind,
      application_id: form.applicationId,
      input_mapping: inputMapping,
      dify_user: form.difyUser,
      api_key: form.apiKey,
      version_fingerprint: form.versionFingerprint,
      published_workflow_id: form.publishedWorkflowId,
    })
    await loadTarget()
    notifyPlatformDataChanged('targets', 'dashboard', 'runs')
    successMessage.value = 'Dify 目标已更新。未填写 API Key 时会保留原有凭据。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存失败。请检查运行参数。'
  } finally {
    isSaving.value = false
  }
}

async function removeTarget(): Promise<void> {
  if (!target.value?.can_delete || isDeleting.value) {
    return
  }
  try {
    await showConfirmDialog({
      title: '删除 Dify 目标？',
      message: '将删除此目标；只有没有其他目标共用时才会清理本机加密凭据。此操作无法恢复。',
      confirmButtonText: '删除目标',
      confirmButtonColor: '#b43268',
    })
  } catch {
    return
  }
  isDeleting.value = true
  errorMessage.value = ''
  try {
    await deleteTarget(targetId())
    notifyPlatformDataChanged('targets', 'dashboard', 'runs')
    await router.push({ name: 'targets' })
  } catch {
    errorMessage.value = '无法删除该目标。被评测运行或候选引用的版本必须保留，以确保证据可追溯。'
  } finally {
    isDeleting.value = false
  }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['targets', 'runs'], refresh: loadTarget })
</script>

<template>
  <PageHeader title="Dify 目标详情" description="未被候选或评测引用的目标可编辑；已引用版本保持不可变，便于复现历史结论。" :is-refreshing="dataRefresh.isRefreshing" @refresh="dataRefresh.refresh" />
  <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
  <template v-else-if="target">
    <section class="detail-summary">
      <div><span>版本引用</span><strong>{{ target.usage_count }} 次评测 · {{ target.candidate_binding_count }} 个候选</strong></div>
      <div><span>凭据状态</span><strong>{{ target.credential_status === 'stored' ? '本机已加密保存' : '外部密钥引用' }}</strong></div>
      <div><span>删除权限</span><strong>{{ target.can_delete ? '可删除' : '保留审计证据' }}</strong></div>
    </section>
    <section class="split-layout" :class="{ 'read-only-layout': !canManageTargets }">
      <form v-if="canManageTargets" class="form-card" @submit.prevent="saveTarget">
        <h2>连接配置</h2>
        <p v-if="target.usage_count || target.candidate_binding_count" class="error-message">该版本已被评测或候选引用，连接配置已锁定。请登记新的版本化目标。</p>
        <fieldset :disabled="target.usage_count > 0 || target.candidate_binding_count > 0" class="target-fields">
          <label>工作流名称<input v-model.trim="form.name" required maxlength="80" /></label>
          <label>本机 Dify 地址<input v-model.trim="form.baseUrl" required type="url" /></label>
          <div class="form-two-columns">
            <label>运行类型<select v-model="form.targetKind"><option value="workflow">Workflow</option><option value="chatflow">Chatflow</option></select></label>
            <label>Dify 用户标识<input v-model.trim="form.difyUser" required maxlength="160" /></label>
          </div>
          <label>运行时路径<input v-model.trim="form.runtimePath" required pattern="/.*" /></label>
          <label>输入变量映射<textarea v-model="form.inputMappingText" required spellcheck="false" /><small v-if="mappingErrorMessage" class="error-message">{{ mappingErrorMessage }}</small></label>
          <label>Dify 应用 ID（可选）<input v-model.trim="form.applicationId" maxlength="160" /></label>
          <label>版本标识<input v-model.trim="form.versionFingerprint" required maxlength="180" /></label>
          <label>Dify 已发布工作流版本 ID（建议填写）<input v-model.trim="form.publishedWorkflowId" pattern="[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}" placeholder="留空则运行当前已发布版本" /></label>
          <label>替换 API Key（可选）<input v-model="form.apiKey" type="password" autocomplete="off" maxlength="4096" placeholder="留空则保留已存凭据" /></label>
        </fieldset>
        <van-button native-type="submit" type="primary" block :loading="isSaving" :disabled="isSaving || target.usage_count > 0 || target.candidate_binding_count > 0">保存更改</van-button>
        <p v-if="successMessage" class="success-message">{{ successMessage }}</p>
      </form>
      <section class="detail-aside">
        <h2>删除与可追溯性</h2>
        <p v-if="target.can_delete">该目标尚未被评测或候选引用。删除时仅在没有其他目标共用的情况下清理本机加密凭据。</p>
        <p v-else>该目标已被评测或候选引用。为保证版本与历史证据可复查，平台禁止删除。</p>
        <van-button v-if="canManageTargets" type="danger" plain block :loading="isDeleting" :disabled="!target.can_delete || isDeleting" @click="removeTarget">删除此目标</van-button>
        <router-link class="detail-link" :to="{ name: 'targets' }">返回 Dify 目标列表</router-link>
      </section>
    </section>
  </template>
</template>

<style scoped>
.target-fields { display: grid; gap: 15px; margin: 0; padding: 0; border: 0; min-inline-size: 0; }
.detail-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 15px; margin-bottom: 21px; }
.detail-summary div, .detail-aside { padding: 20px; background: var(--panel); box-shadow: 0 10px 24px rgba(81, 64, 137, .07); backdrop-filter: blur(14px); border-radius: 15px; }
.detail-summary span { display: block; color: var(--muted); font-size: 12px; }
.detail-summary strong { display: block; margin-top: 8px; color: var(--orchid-deep); font-size: 17px; }
.detail-aside { display: grid; gap: 15px; }
.detail-aside h2, .detail-aside p { margin: 0; }
.detail-aside h2 { font-size: 20px; }
.detail-aside p { color: var(--muted); font-size: 13px; line-height: 1.7; }
.form-two-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 620px) { .detail-summary, .form-two-columns { grid-template-columns: 1fr; } }
</style>
