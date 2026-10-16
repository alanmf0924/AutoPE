<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import { createTarget, listTargets } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import type { DifyTarget, DifyTargetCreatePayload } from '@/types'

type TargetFormState = Omit<DifyTargetCreatePayload, 'input_mapping'> & {
  input_mapping_text: string
}

const targets = ref<DifyTarget[]>([])
const canManageTargets = computed(() => activeSession.value?.allowed_actions.includes('targets.manage') ?? false)
const errorMessage = ref('')
const successMessage = ref('')
const mappingErrorMessage = ref('')
const isSubmitting = ref(false)
const targetForm = reactive<TargetFormState>({
  name: '',
  base_url: 'http://127.0.0.1:3000',
  runtime_path: '/v1/workflows/run',
  target_kind: 'workflow',
  application_id: '',
  input_mapping_text: '{}',
  dify_user: 'autope-evaluator',
  api_key: '',
  version_fingerprint: 'local-unversioned',
  published_workflow_id: '',
})

async function loadTargets(): Promise<void> {
  try {
    targets.value = await listTargets()
  } catch {
    errorMessage.value = '无法读取 Dify 目标。请检查后端服务。'
  }
}

function resetTargetForm(): void {
  targetForm.name = ''
  targetForm.application_id = ''
  targetForm.api_key = ''
  targetForm.version_fingerprint = 'local-unversioned'
  targetForm.published_workflow_id = ''
}

function parseInputMapping(value: string): Record<string, string> {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error('输入变量映射必须是合法的 JSON 对象。')
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('输入变量映射必须是对象，例如 {"skin_type":"skin_type"}。')
  }
  const mapping: Record<string, string> = {}
  for (const [caseField, difyField] of Object.entries(parsed as Record<string, unknown>)) {
    if (!caseField.trim() || typeof difyField !== 'string' || !difyField.trim()) {
      throw new Error('每条输入映射都需要非空的用例字段与 Dify 变量名。')
    }
    mapping[caseField.trim()] = difyField.trim()
  }
  return mapping
}

async function submitTarget(): Promise<void> {
  if (isSubmitting.value) {
    return
  }

  errorMessage.value = ''
  successMessage.value = ''
  mappingErrorMessage.value = ''
  let inputMapping: Record<string, string>
  try {
    inputMapping = parseInputMapping(targetForm.input_mapping_text)
  } catch (error) {
    mappingErrorMessage.value = error instanceof Error ? error.message : '输入变量映射格式不正确。'
    return
  }
  isSubmitting.value = true
  try {
    const target = await createTarget({
      name: targetForm.name,
      base_url: targetForm.base_url,
      runtime_path: targetForm.runtime_path,
      target_kind: targetForm.target_kind,
      application_id: targetForm.application_id,
      input_mapping: inputMapping,
      dify_user: targetForm.dify_user,
      api_key: targetForm.api_key,
      version_fingerprint: targetForm.version_fingerprint,
      published_workflow_id: targetForm.published_workflow_id,
    })
    targets.value = [target, ...targets.value]
    notifyPlatformDataChanged('targets', 'dashboard', 'runs')
    successMessage.value = 'Dify 应用配置已登记，API Key 已加密保存，后续页面不会回显。'
    resetTargetForm()
  } catch {
    errorMessage.value = '无法登记 Dify 应用。请检查地址、运行配置、API Key 和版本指纹。'
  } finally {
    isSubmitting.value = false
  }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['targets'], refresh: loadTargets })
</script>

<template>
  <PageHeader
    title="管理被测工作流，而不是管理评测规则"
    description="首次接入只需填写工作流名称和 API Key；其余字段已经使用本机 Dify 的默认配置，可按需展开。"
    :is-refreshing="dataRefresh.isRefreshing"
    @refresh="dataRefresh.refresh"
  />
  <section class="split-layout target-settings-layout" :class="{ 'read-only-layout': !canManageTargets }">
    <form v-if="canManageTargets" class="form-card target-config-form" @submit.prevent="submitTarget">
      <h2>登记本地 Dify 应用</h2>
      <label>
        工作流名称
        <input v-model.trim="targetForm.name" required maxlength="120" placeholder="例如：客服助手 Baseline" />
      </label>
      <label>
        Dify 应用 API Key
        <input
          v-model="targetForm.api_key"
          required
          type="password"
          autocomplete="off"
          maxlength="4096"
          placeholder="粘贴 Dify 应用的 API Key"
        />
        <small class="form-hint">只接受本机 Dify 地址；保存后 API Key 不会在页面、列表、接口响应或日志中显示。</small>
      </label>
      <p class="default-config">默认连接本机 Dify：<strong>127.0.0.1:3000</strong> · Workflow · <strong>/v1/workflows/run</strong></p>
      <details class="advanced-settings">
        <summary>高级配置（可选）</summary>
        <fieldset>
          <legend>仅在默认 Workflow 不适用时修改</legend>
          <label>
            本机 Dify 地址
            <input v-model.trim="targetForm.base_url" required type="url" placeholder="http://127.0.0.1:3000" />
          </label>
          <div class="form-two-columns">
            <label>
              运行类型
              <select v-model="targetForm.target_kind">
                <option value="workflow">Workflow</option>
                <option value="chatflow">Chatflow</option>
              </select>
            </label>
            <label>
              Dify 用户标识
              <input v-model.trim="targetForm.dify_user" required maxlength="160" placeholder="autope-evaluator" />
            </label>
          </div>
          <label>
            运行时路径
            <input v-model.trim="targetForm.runtime_path" required pattern="/.*" placeholder="/v1/workflows/run" />
          </label>
          <label>
            输入变量映射
            <textarea
              v-model="targetForm.input_mapping_text"
              required
              spellcheck="false"
              placeholder="例如：{&quot;skin_type&quot;:&quot;skin_type&quot;,&quot;budget&quot;:&quot;budget&quot;}"
              aria-describedby="input-mapping-hint"
            />
            <small id="input-mapping-hint" class="form-hint">键为评测用例字段，值为 Dify Workflow 的输入变量名；Chatflow 的 query 由用例主问题提供。</small>
            <small v-if="mappingErrorMessage" class="error-message" aria-live="polite">{{ mappingErrorMessage }}</small>
          </label>
          <label>
            Dify 应用 ID
            <input v-model.trim="targetForm.application_id" maxlength="160" placeholder="用于和 Dify 控制台核对，可留空" />
          </label>
          <label>
            版本标识
            <input v-model.trim="targetForm.version_fingerprint" required maxlength="160" placeholder="默认：local-unversioned" />
          </label>
          <label>
            Dify 已发布工作流版本 ID（建议填写）
            <input v-model.trim="targetForm.published_workflow_id" pattern="[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}" placeholder="从 Dify 版本历史复制 UUID；留空则运行当前发布版本" />
          </label>
        </fieldset>
      </details>
      <van-button native-type="submit" type="primary" block :loading="isSubmitting" :disabled="isSubmitting">
        加密保存并登记
      </van-button>
    </form>

    <section class="data-list" aria-label="已登记的 Dify 目标">
      <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
      <p v-if="successMessage" class="success-message">{{ successMessage }}</p>
      <article v-for="target in targets" :key="target.id" class="row-card">
        <div>
          <h2>{{ target.name }}</h2>
          <p>{{ target.base_url }}{{ target.runtime_path }}</p>
          <small>应用 ID：{{ target.application_id || '未填写' }} · 用户：{{ target.dify_user }}</small>
          <small>执行版本：{{ target.published_workflow_id || '未锁定，将运行当前已发布版本' }}</small>
        </div>
        <div class="row-meta">
          <span>{{ target.target_kind }}</span>
          <span>{{ target.credential_status === 'stored' ? '凭据已加密保存' : '使用外部密钥引用' }}</span>
          <strong>{{ target.version_fingerprint }}</strong>
          <small>映射：{{ Object.keys(target.input_mapping).join('、') || '未配置' }}</small>
          <router-link class="detail-link" :to="{ name: 'target-detail', params: { targetId: target.id } }">查看详情</router-link>
        </div>
      </article>
    </section>
  </section>
</template>

<style scoped>
.form-hint {
  color: var(--muted);
  font-size: 0.75rem;
  font-weight: 400;
  line-height: 1.5;
}

.default-config {
  margin: -2px 0 0;
  padding: 11px 12px;
  color: #5b5272;
  background: rgba(238, 233, 255, 0.66);
  border-radius: 10px;
  font-size: 0.79rem;
  line-height: 1.55;
}

.default-config strong {
  color: var(--orchid-deep);
  font-weight: 800;
}

.advanced-settings {
  padding-top: 2px;
}

.advanced-settings summary {
  color: var(--orchid-deep);
  cursor: pointer;
  font-size: 0.84rem;
  font-weight: 800;
}

.advanced-settings fieldset {
  margin-top: 14px;
}

fieldset {
  display: grid;
  gap: 13px;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

legend {
  padding: 0;
  color: var(--orchid-deep);
  font-size: 0.78rem;
  font-weight: 800;
}

.form-two-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 620px) {
  .form-two-columns {
    grid-template-columns: 1fr;
  }
}
</style>

