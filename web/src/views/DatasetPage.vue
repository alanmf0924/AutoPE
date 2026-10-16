<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import { createDataset, listDatasets } from '@/services/api'
import { notifyPlatformDataChanged, usePlatformDataRefresh } from '@/services/dataRefresh'
import { activeSession } from '@/services/sessionState'
import type { Dataset } from '@/types'

const datasets = ref<Dataset[]>([])
const canManageDatasets = computed(() => activeSession.value?.allowed_actions.includes('datasets.manage') ?? false)
const errorMessage = ref('')
const successMessage = ref('')
const isSubmitting = ref(false)
const form = reactive({ name: '', description: '', caseCount: 20 })

async function loadDatasets(): Promise<void> {
  datasets.value = await listDatasets()
}

async function submitDataset(): Promise<void> {
  if (isSubmitting.value) {
    return
  }
  errorMessage.value = ''
  successMessage.value = ''
  isSubmitting.value = true
  try {
    const dataset = await createDataset({
      name: form.name,
      description: form.description,
      case_count: form.caseCount,
    })
    datasets.value = [dataset, ...datasets.value]
    notifyPlatformDataChanged('datasets', 'dashboard', 'runs')
    form.name = ''
    form.description = ''
    successMessage.value = '草稿测试集已创建。请进入详情上传用例、冻结并完成批准后再评测。'
  } catch {
    errorMessage.value = '无法创建测试集。名称和用例数量需要满足校验规则。'
  } finally {
    isSubmitting.value = false
  }
}

async function refreshDatasets(): Promise<void> {
  try {
    await loadDatasets()
  } catch {
    errorMessage.value = '无法读取测试集。请检查后端服务。'
  }
}

const dataRefresh = usePlatformDataRefresh({ resources: ['datasets'], refresh: refreshDatasets })
</script>

<template>
  <PageHeader
    title="上传与冻结测试用例"
    description="此页面只处理数据集版本，不提供评分、评测运行或 Prompt 编辑。"
    :is-refreshing="dataRefresh.isRefreshing"
    @refresh="dataRefresh.refresh"
  />
  <section class="split-layout" :class="{ 'read-only-layout': !canManageDatasets }">
    <form v-if="canManageDatasets" class="form-card" @submit.prevent="submitDataset">
      <h2>创建测试集草稿</h2>
      <label>名称<input v-model.trim="form.name" required maxlength="100" placeholder="例如：售前咨询回归集 v2" /></label>
      <label>说明<textarea v-model.trim="form.description" maxlength="500" placeholder="说明覆盖范围与脱敏来源" /></label>
      <label>用例数量<input v-model.number="form.caseCount" type="number" min="1" max="500" required /></label>
      <van-button native-type="submit" type="primary" block :loading="isSubmitting" :disabled="isSubmitting">创建草稿</van-button>
      <p v-if="successMessage" class="success-message">{{ successMessage }}</p>
      <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
    </form>
    <section class="data-list">
      <article v-for="dataset in datasets" :key="dataset.id" class="row-card">
        <div><h2>{{ dataset.name }}</h2><p>{{ dataset.description || '未填写说明' }}</p></div>
        <div class="row-meta"><strong>{{ dataset.case_count }} 条用例 · v{{ dataset.version }}</strong><span>{{ dataset.is_frozen ? '已冻结' : dataset.review_status }}</span><router-link class="detail-link" :to="{ name: 'dataset-detail', params: { datasetId: dataset.id } }">{{ dataset.is_frozen ? '查看详情' : '查看草稿详情' }}</router-link></div>
      </article>
    </section>
  </section>
</template>

