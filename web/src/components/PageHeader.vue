<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  description: string
  isRefreshing?: boolean
}>(), {
  isRefreshing: false,
})

defineEmits<{ refresh: [] }>()
</script>

<template>
  <header class="page-header">
    <div>
      <h1>{{ title }}</h1>
      <span>{{ description }}</span>
    </div>
    <button class="page-refresh" type="button" :disabled="isRefreshing" @click="$emit('refresh')">
      <van-icon name="replay" :class="{ 'page-refresh-icon--spinning': isRefreshing }" aria-hidden="true" />
      <span>{{ isRefreshing ? '刷新中…' : '刷新数据' }}</span>
    </button>
  </header>
</template>

