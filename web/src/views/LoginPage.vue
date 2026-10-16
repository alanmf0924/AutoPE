<script setup lang="ts">
import { ref } from 'vue'

import AppIcon from '@/components/AppIcon.vue'
import { loginLocal, resetPassword } from '@/services/api'

const emit = defineEmits<{ 'logged-in': [] }>()
const loginName = ref('')
const password = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)
const isResetting = ref(false)
const resetToken = ref('')
const newPassword = ref('')
const notice = ref('')

async function signIn(): Promise<void> {
  if (isSubmitting.value) {
    return
  }
  errorMessage.value = ''
  notice.value = ''
  const normalizedLoginName = loginName.value.trim().toLowerCase()
  if (!normalizedLoginName || !password.value) {
    errorMessage.value = '请输入登录名和密码。'
    return
  }
  isSubmitting.value = true
  try {
    await loginLocal(normalizedLoginName, password.value)
    password.value = ''
    emit('logged-in')
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '登录失败，请稍后重试。'
  } finally {
    isSubmitting.value = false
  }
}

async function submitReset(): Promise<void> {
  if (isSubmitting.value) return
  errorMessage.value = ''
  notice.value = ''
  if (!resetToken.value || newPassword.value.length < 12) {
    errorMessage.value = '请输入一次性凭据和至少 12 位的新密码。'
    return
  }
  isSubmitting.value = true
  try {
    await resetPassword(resetToken.value.trim(), newPassword.value)
    resetToken.value = ''
    newPassword.value = ''
    isResetting.value = false
    notice.value = '密码已更新，请使用新密码登录。'
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '重置失败，请稍后重试。'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-intro">
      <div class="login-brand"><AppIcon /><strong>Dify AutoPE</strong></div>
      <h1>登录评测控制面</h1>
      <p>测试集、评测证据和发布审批留在这里。Dify 仅作为被测运行时。</p>
      <span>账号由平台管理员创建，无需连接 OA 或企业登录系统。</span>
    </section>
    <section class="login-form" aria-label="平台登录">
      <form v-if="!isResetting" @submit.prevent="signIn">
        <h2>欢迎回来</h2>
        <label>登录名<input v-model="loginName" type="text" autocomplete="username" required /></label>
        <label>密码<input v-model="password" type="password" autocomplete="current-password" required /></label>
        <p v-if="notice" class="success-message" role="status">{{ notice }}</p>
        <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
        <van-button native-type="submit" type="primary" block :loading="isSubmitting">登录平台</van-button>
        <button class="text-action" type="button" @click="isResetting = true; errorMessage = ''">使用一次性凭据重置密码</button>
      </form>
      <form v-else @submit.prevent="submitReset">
        <h2>重置密码</h2>
        <p class="form-note">请向平台管理员索取一次性凭据；管理员失去访问权时，由服务器负责人在终端执行恢复命令。</p>
        <label>一次性凭据<input v-model="resetToken" type="text" autocomplete="off" required /></label>
        <label>新密码<input v-model="newPassword" type="password" autocomplete="new-password" minlength="12" required /></label>
        <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
        <van-button native-type="submit" type="primary" block :loading="isSubmitting">更新密码</van-button>
        <button class="text-action" type="button" @click="isResetting = false; errorMessage = ''; resetToken = ''; newPassword = ''">返回登录</button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page { display: grid; grid-template-columns: minmax(300px, .9fr) minmax(320px, .8fr); gap: clamp(32px, 8vw, 120px); align-items: center; min-height: 100dvh; max-width: 1120px; margin: 0 auto; padding: 48px clamp(20px, 5vw, 64px); }
.login-brand { display: flex; gap: 12px; align-items: center; font-size: 17px; }
.login-brand :deep(.app-icon) { width: 48px; height: 48px; }
.login-intro h1 { max-width: 10ch; margin: 48px 0 18px; font-size: clamp(38px, 5vw, 62px); letter-spacing: -.055em; line-height: 1.1; }
.login-intro p { max-width: 32ch; margin: 0; color: var(--muted); font-size: 16px; line-height: 1.8; }
.login-intro span { display: block; max-width: 42ch; margin-top: 38px; padding-top: 19px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; line-height: 1.7; }
.login-form { padding: clamp(24px, 3vw, 42px); border: 1px solid var(--line); border-radius: 24px; background: rgba(255, 255, 255, .88); box-shadow: var(--shadow); }
.login-form form { display: grid; gap: 19px; }
.login-form h2 { margin: 0 0 9px; font-size: 23px; }
.login-form label { display: grid; gap: 8px; color: #4e4665; font-size: 13px; font-weight: 700; }
.login-form input { min-height: 44px; }
.text-action { width: fit-content; padding: 3px 0; border: 0; background: none; color: var(--primary); font: inherit; font-size: 13px; cursor: pointer; }
.form-note { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.7; }
.success-message { margin: 0; color: #206a4b; font-size: 13px; }
@media (max-width: 760px) { .login-page { grid-template-columns: 1fr; gap: 36px; padding-block: 32px; }.login-intro h1 { margin-top: 30px; }.login-intro span { margin-top: 24px; } }
</style>
