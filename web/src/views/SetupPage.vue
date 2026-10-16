<script setup lang="ts">
import { ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { createInitialAdmin } from '@/services/api'

const emit = defineEmits<{ initialized: [] }>()
const bootstrapToken = ref('')
const loginName = ref('')
const displayName = ref('')
const password = ref('')
const confirmPassword = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)
const isCreated = ref(false)

async function createAdmin(): Promise<void> {
  if (isSubmitting.value || isCreated.value) {
    return
  }
  errorMessage.value = ''
  const normalizedLoginName = loginName.value.trim().toLowerCase()
  if (!/^[a-z0-9][a-z0-9._-]{2,63}$/.test(normalizedLoginName)) {
    errorMessage.value = '登录名需为 3–64 位小写字母、数字、点、下划线或连字符。'
    return
  }
  if (!displayName.value.trim() || bootstrapToken.value.trim().length < 32) {
    errorMessage.value = '请填写显示名称和服务器提供的一次性令牌。'
    return
  }
  if (password.value.length < 12 || password.value !== confirmPassword.value) {
    errorMessage.value = '密码至少 12 位，且两次输入必须一致。'
    return
  }
  isSubmitting.value = true
  try {
    await createInitialAdmin({
      bootstrap_token: bootstrapToken.value.trim(),
      login_name: normalizedLoginName,
      display_name: displayName.value.trim(),
      password: password.value,
    })
    bootstrapToken.value = ''
    password.value = ''
    confirmPassword.value = ''
    isCreated.value = true
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '初始化失败，请检查令牌后重试。'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <main class="setup-page">
    <section class="setup-intro" aria-labelledby="setup-title">
      <div class="setup-brand"><AppIcon /><strong>Dify AutoPE</strong></div>
      <h1 id="setup-title">初始化平台</h1>
      <p>创建第一位管理员，之后就能在平台内管理账号与权限。无需先安装 OA 或连接企业登录系统。</p>
      <div class="setup-instruction">
        <strong>从服务器获取一次性令牌</strong>
        <code>python -m app.local_identity bootstrap-token</code>
        <span>令牌 15 分钟内有效，使用后立即失效。请勿把它发到群聊或写入部署文件。</span>
      </div>
    </section>

    <section class="setup-form" aria-label="创建初始管理员">
      <div v-if="isCreated" class="setup-success" role="status">
        <h2>管理员已创建</h2>
        <p>一次性令牌已失效。请使用新账号登录；后续账号由管理员在平台内创建。</p>
        <van-button type="primary" @click="emit('initialized')">前往登录</van-button>
      </div>
      <form v-else @submit.prevent="createAdmin">
        <h2>管理员信息</h2>
        <label>一次性令牌<input v-model="bootstrapToken" type="password" autocomplete="off" required /></label>
        <label>登录名<input v-model="loginName" type="text" autocomplete="username" maxlength="64" required /></label>
        <label>显示名称<input v-model="displayName" type="text" autocomplete="name" maxlength="160" required /></label>
        <label>密码<input v-model="password" type="password" autocomplete="new-password" minlength="12" required /></label>
        <label>确认密码<input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="12" required /></label>
        <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
        <van-button native-type="submit" type="primary" block :loading="isSubmitting">创建管理员</van-button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.setup-page { display: grid; grid-template-columns: minmax(300px, .85fr) minmax(330px, 1fr); gap: clamp(32px, 7vw, 112px); align-items: center; min-height: 100dvh; max-width: 1180px; margin: 0 auto; padding: 48px clamp(20px, 5vw, 64px); }
.setup-brand { display: flex; gap: 12px; align-items: center; color: var(--ink); font-size: 17px; }
.setup-brand :deep(.app-icon) { width: 48px; height: 48px; }
.setup-intro h1 { margin: 48px 0 18px; color: var(--ink); font-size: clamp(38px, 5vw, 64px); letter-spacing: -.055em; line-height: 1.08; }
.setup-intro > p { max-width: 34ch; margin: 0; color: var(--muted); font-size: 16px; line-height: 1.8; }
.setup-instruction { display: grid; gap: 11px; max-width: 480px; margin-top: 46px; padding: 20px 0 0; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; line-height: 1.6; }
.setup-instruction strong { color: var(--ink); font-size: 15px; }
.setup-instruction code { overflow-wrap: anywhere; color: var(--orchid-deep); font-size: 12px; }
.setup-form { padding: clamp(24px, 3vw, 42px); border: 1px solid var(--line); border-radius: 24px; background: rgba(255, 255, 255, .88); box-shadow: var(--shadow); }
.setup-form form { display: grid; gap: 17px; }
.setup-form h2 { margin: 0 0 8px; color: var(--ink); font-size: 23px; }
.setup-form label { display: grid; gap: 8px; color: #4e4665; font-size: 13px; font-weight: 700; }
.setup-form input { min-height: 44px; }
.setup-success h2 { margin: 0 0 12px; color: #087a64; font-size: 24px; }
.setup-success p { margin: 0; color: var(--muted); line-height: 1.7; }
.setup-success .van-button { margin-top: 24px; }
@media (max-width: 760px) { .setup-page { grid-template-columns: 1fr; gap: 36px; padding-block: 32px; }.setup-intro h1 { margin-top: 30px; }.setup-instruction { margin-top: 28px; } }
</style>
