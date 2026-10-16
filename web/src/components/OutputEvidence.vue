<script setup lang="ts">
import { computed } from 'vue'

import { presentModelOutput } from '@/services/outputPresentation'

const props = defineProps<{ title: string; rawText: string; emptyMessage: string }>()
const presentation = computed(() => presentModelOutput(props.rawText))
</script>

<template>
  <article class="output-evidence">
    <h3>{{ title }}</h3>
    <p v-if="presentation.hasThinkLeak" class="output-warning">原始输出含 &lt;think&gt; 推理内容；已从默认阅读区移开，但安全问题仍保留在评测证据中。</p>
    <div v-if="presentation.finalAnswer" class="answer-block">
      <span class="answer-label">最终回答</span>
      <p>{{ presentation.finalAnswer }}</p>
    </div>
    <p v-else-if="presentation.needsRawInspection" class="output-warning">无法安全提取最终回答。请展开原始输出核对后再标注。</p>
    <p v-else class="empty-answer">{{ emptyMessage }}</p>
    <p v-if="presentation.isStructuredAnswer" class="extraction-note">已从结构化输出的 answer 字段提取；评分仍以完整原始证据为准。</p>
    <details v-if="rawText.trim()" class="raw-evidence">
      <summary>{{ presentation.hasThinkLeak ? '查看完整原始输出（含推理片段）' : '查看完整原始输出（评测证据）' }}</summary>
      <pre>{{ rawText }}</pre>
    </details>
  </article>
</template>

<style scoped>
.output-evidence { min-width: 0; }
.output-evidence h3 { margin: 0; color: var(--orchid-deep); font-size: 13px; }
.answer-block { display: grid; gap: 7px; }
.answer-label { color: var(--muted); font-size: 12px; font-weight: 700; }
.answer-block p { margin: 0; color: var(--ink); line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.output-warning { margin: 0; padding: 10px 12px; border-radius: 9px; color: #64421b; background: #fff0d5; font-size: 13px; line-height: 1.65; }
.empty-answer, .extraction-note { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.raw-evidence { min-width: 0; margin-top: 4px; }
.raw-evidence summary { width: fit-content; color: var(--orchid-deep); cursor: pointer; font-size: 13px; font-weight: 700; line-height: 1.6; }
.raw-evidence summary:focus-visible { outline: 3px solid var(--orchid); outline-offset: 3px; border-radius: 3px; }
.raw-evidence pre { max-height: 440px; overflow: auto; margin: 10px 0 0; padding: 14px; border-radius: 9px; background: rgba(247, 245, 255, .9); color: #504865; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
