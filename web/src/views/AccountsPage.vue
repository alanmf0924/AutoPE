<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  createPlatformAccount,
  issuePlatformResetToken,
  listPlatformAccounts,
  updatePlatformAccount,
  type PlatformAccount,
} from '@/services/api'

const roleOptions = [
  { value: 'operator', label: '运营' },
  { value: 'annotator', label: '标注' },
  { value: 'prompt_owner', label: 'Prompt 负责人' },
  { value: 'approver', label: '审批' },
  { value: 'auditor', label: '审计' },
]

const accounts = ref<PlatformAccount[]>([])
const draftRoles = ref<Record<string, string[]>>({})
const loginName = ref('')
const displayName = ref('')
const newRoles = ref<string[]>([])
const newPlatformAdmin = ref(false)
const oneTimeToken = ref('')
const oneTimePurpose = ref('')
const errorMessage = ref('')
const notice = ref('')
const isBusy = ref(false)

async function loadAccounts(): Promise<void> {
  try {
    accounts.value = await listPlatformAccounts()
    draftRoles.value = Object.fromEntries(accounts.value.map((account) => [account.id, [...account.roles]]))
    errorMessage.value = ''
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '账号列表暂不可用。'
  }
}

async function createAccount(): Promise<void> {
  if (isBusy.value) return
  isBusy.value = true
  errorMessage.value = ''
  oneTimeToken.value = ''
  try {
    const created = await createPlatformAccount({
      login_name: loginName.value.trim().toLowerCase(),
      display_name: displayName.value.trim(),
      roles: newRoles.value,
      is_platform_admin: newPlatformAdmin.value,
    })
    oneTimeToken.value = created.invite_token
    oneTimePurpose.value = `邀请 ${loginName.value.trim()} 设置密码`
    notice.value = '账号已创建。请通过安全渠道将一次性凭据交给本人；页面关闭后不会再显示。'
    loginName.value = ''
    displayName.value = ''
    newRoles.value = []
    newPlatformAdmin.value = false
    await loadAccounts()
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '创建账号失败。'
  } finally {
    isBusy.value = false
  }
}

async function saveRoles(account: PlatformAccount): Promise<void> {
  if (isBusy.value) return
  isBusy.value = true
  errorMessage.value = ''
  try {
    await updatePlatformAccount(account.id, { roles: draftRoles.value[account.id] ?? [] })
    notice.value = `已更新 ${account.display_name} 的业务角色；旧会话下次请求会使用新权限。`
    await loadAccounts()
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '角色更新失败。'
  } finally {
    isBusy.value = false
  }
}

async function toggleStatus(account: PlatformAccount): Promise<void> {
  if (isBusy.value) return
  isBusy.value = true
  errorMessage.value = ''
  try {
    const nextStatus = account.status === 'active' ? 'disabled' : 'active'
    await updatePlatformAccount(account.id, { status: nextStatus })
    notice.value = nextStatus === 'disabled' ? '账号已停用，既有会话已撤销。' : '账号已启用。'
    await loadAccounts()
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '账号状态更新失败。'
  } finally {
    isBusy.value = false
  }
}

async function issueReset(account: PlatformAccount): Promise<void> {
  if (isBusy.value) return
  isBusy.value = true
  errorMessage.value = ''
  oneTimeToken.value = ''
  try {
    const result = await issuePlatformResetToken(account.id)
    oneTimeToken.value = result.reset_token
    oneTimePurpose.value = `重置 ${account.login_name} 的密码`
    notice.value = '一次性凭据有效 15 分钟；签发新凭据会使旧凭据失效。'
  } catch (error: unknown) {
    errorMessage.value = error instanceof Error ? error.message : '签发重置凭据失败。'
  } finally {
    isBusy.value = false
  }
}

onMounted(() => { void loadAccounts() })
</script>

<template>
  <div class="accounts-page">
    <header class="page-heading">
      <div><p>Platform identity</p><h1>账号与权限</h1><span>平台管理员负责发放账号；审批仍要求不同主体和当前证据版本。</span></div>
      <van-button plain @click="loadAccounts">刷新列表</van-button>
    </header>

    <p v-if="errorMessage" class="message error-message" role="alert">{{ errorMessage }}</p>
    <p v-if="notice" class="message success-message" role="status">{{ notice }}</p>

    <section v-if="oneTimeToken" class="token-panel" aria-label="一次性凭据">
      <h2>{{ oneTimePurpose }}</h2>
      <p>仅此处显示一次。不要放进工单、URL 或浏览器持久存储；请通过安全渠道交付本人。</p>
      <code>{{ oneTimeToken }}</code>
      <button type="button" @click="oneTimeToken = ''; oneTimePurpose = ''">已交付，清除页面凭据</button>
    </section>

    <section class="panel create-panel">
      <h2>邀请新成员</h2>
      <p>无需邮箱或 OA；创建后由本人用一次性凭据设置密码。</p>
      <form @submit.prevent="createAccount">
        <label>登录名<input v-model="loginName" autocomplete="off" minlength="3" maxlength="64" required /></label>
        <label>显示名称<input v-model="displayName" autocomplete="off" maxlength="160" required /></label>
        <fieldset><legend>业务角色</legend><label v-for="role in roleOptions" :key="role.value"><input v-model="newRoles" type="checkbox" :value="role.value" />{{ role.label }}</label></fieldset>
        <label class="check"><input v-model="newPlatformAdmin" type="checkbox" />同时授予平台账号管理权限</label>
        <van-button native-type="submit" type="primary" :loading="isBusy">创建并生成邀请凭据</van-button>
      </form>
    </section>

    <section class="panel">
      <h2>现有账号</h2>
      <p v-if="accounts.length === 0">暂无可展示账号，或账号列表尚未加载。</p>
      <div v-for="account in accounts" :key="account.id" class="account-row">
        <div class="account-title"><strong>{{ account.display_name }}</strong><span>{{ account.login_name }}</span><small>{{ account.status === 'active' ? '启用' : '停用' }} · {{ account.is_platform_admin ? '平台管理员' : '普通账号' }}</small></div>
        <fieldset><legend>业务角色</legend><label v-for="role in roleOptions" :key="role.value"><input v-model="draftRoles[account.id]" type="checkbox" :value="role.value" />{{ role.label }}</label></fieldset>
        <div class="row-actions"><van-button size="small" plain :disabled="isBusy" @click="saveRoles(account)">保存角色</van-button><van-button size="small" plain :disabled="isBusy || account.is_platform_admin" @click="toggleStatus(account)">{{ account.status === 'active' ? '停用账号' : '启用账号' }}</van-button><van-button size="small" plain :disabled="isBusy || account.status !== 'active'" @click="issueReset(account)">重置密码</van-button></div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.accounts-page { display: grid; gap: 20px; padding: 4px 0 42px; }
.page-heading { display: flex; justify-content: space-between; align-items: start; gap: 20px; }
.page-heading p { margin: 0 0 8px; color: #7560a3; font-size: 12px; letter-spacing: .12em; text-transform: uppercase; }
.page-heading h1 { margin: 0 0 10px; font-size: clamp(27px, 3vw, 38px); letter-spacing: -.04em; }
.page-heading span, .panel > p { color: var(--muted); font-size: 13px; line-height: 1.7; }
.panel, .token-panel { padding: clamp(20px, 3vw, 30px); border: 1px solid var(--line); border-radius: 18px; background: #fff; }
.panel h2, .token-panel h2 { margin: 0 0 12px; font-size: 20px; }
.create-panel form { display: grid; gap: 16px; max-width: 620px; }
.create-panel label:not(.check) { display: grid; gap: 7px; font-size: 13px; font-weight: 650; }
.create-panel input:not([type='checkbox']) { min-height: 42px; }
fieldset { display: flex; flex-wrap: wrap; gap: 9px 16px; padding: 0; border: 0; }
legend { width: 100%; margin-bottom: 8px; font-size: 13px; font-weight: 700; }
fieldset label, .check { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.account-row { display: grid; gap: 15px; padding: 19px 0; border-top: 1px solid var(--line); }
.account-title { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px 14px; }
.account-title strong { font-size: 17px; }.account-title span, .account-title small { color: var(--muted); }
.row-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.message { margin: 0; padding: 12px 14px; border-radius: 10px; font-size: 13px; }
.success-message { background: #e9f7ef; color: #216448; }.error-message { background: #fff1ee; color: #a33a2c; }
.token-panel { display: grid; gap: 10px; background: #f6f0ff; border-color: #d9c7f8; }.token-panel p { margin: 0; font-size: 13px; line-height: 1.6; }.token-panel code { overflow-wrap: anywhere; padding: 12px; border-radius: 8px; background: #fff; }.token-panel button { justify-self: start; border: 0; background: none; color: #573998; cursor: pointer; }
@media (max-width: 700px) { .page-heading { flex-direction: column; } }
</style>
