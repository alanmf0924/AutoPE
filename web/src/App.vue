<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import { ApiError, changeOwnPassword, getSession, getSetupStatus, logoutLocal } from '@/services/api'
import { canVisitRoute, homeRoute } from '@/services/routeAccess'
import { activeSession } from '@/services/sessionState'
import LoginPage from '@/views/LoginPage.vue'
import SetupPage from '@/views/SetupPage.vue'

const session = activeSession
const route = useRoute()
const router = useRouter()
const sessionError = ref('')
const isCheckingSession = ref(true)
const needsSetup = ref(false)
const needsLogin = ref(false)
const showPasswordChange = ref(false)
const currentPassword = ref('')
const newPassword = ref('')
const passwordError = ref('')
const isChangingPassword = ref(false)
const hasAuthenticatedOnce = ref(false)
const routeViewVersion = ref(0)
let lastAuthenticatedSubject = ''
const canOpenRoute = computed(() => session.value !== undefined && canVisitRoute(session.value, route.name))

async function refreshSession(): Promise<void> {
  isCheckingSession.value = true
  try {
    const identity = await getSession()
    if (lastAuthenticatedSubject && identity.subject !== lastAuthenticatedSubject) {
      routeViewVersion.value += 1
    }
    const restoring = hasAuthenticatedOnce.value && session.value === undefined && identity.subject === lastAuthenticatedSubject
    session.value = identity
    hasAuthenticatedOnce.value = true
    lastAuthenticatedSubject = identity.subject
    sessionError.value = ''
    needsSetup.value = false
    needsLogin.value = false
    if (restoring) window.dispatchEvent(new Event('autope:session-restored'))
  } catch (error: unknown) {
    session.value = undefined
    needsLogin.value = false
    try {
      const setupStatus = await getSetupStatus()
      needsSetup.value = !setupStatus.initialized
    } catch {
      needsSetup.value = false
    }
    needsLogin.value = !needsSetup.value && error instanceof ApiError && error.status === 401
    sessionError.value = needsSetup.value || needsLogin.value
      ? '' : '身份服务暂不可用，平台操作已锁定。请稍后重试。'
  } finally {
    isCheckingSession.value = false
  }
}

async function signOut(): Promise<void> {
  try {
    await logoutLocal()
    closePasswordChange()
    session.value = undefined
    hasAuthenticatedOnce.value = false
    lastAuthenticatedSubject = ''
    routeViewVersion.value += 1
    needsLogin.value = true
  } catch {
    sessionError.value = '登出失败，请重试。'
  }
}

function closePasswordChange(): void {
  showPasswordChange.value = false
  currentPassword.value = ''
  newPassword.value = ''
  passwordError.value = ''
}

async function submitPasswordChange(): Promise<void> {
  if (isChangingPassword.value) return
  passwordError.value = ''
  if (newPassword.value.length < 12) {
    passwordError.value = '新密码至少需要 12 位。'
    return
  }
  isChangingPassword.value = true
  try {
    await changeOwnPassword(currentPassword.value, newPassword.value)
    closePasswordChange()
    session.value = undefined
    hasAuthenticatedOnce.value = false
    lastAuthenticatedSubject = ''
    routeViewVersion.value += 1
    needsLogin.value = true
  } catch (error: unknown) {
    passwordError.value = error instanceof Error ? error.message : '修改密码失败。'
  } finally {
    isChangingPassword.value = false
  }
}

function onInitialized(): void {
  needsSetup.value = false
  needsLogin.value = true
}

function onIdentityChanged(): void {
  void refreshSession()
}

watch([session, () => route.name], ([identity, routeName]) => {
  if (identity && routeName === 'dashboard' && !canVisitRoute(identity, routeName)) {
    const destination = homeRoute(identity)
    if (destination) {
      void router.replace({ name: destination })
    }
  }
}, { immediate: true })

onMounted(() => {
  window.addEventListener('autope:identity-changed', onIdentityChanged)
  void refreshSession()
})
onBeforeUnmount(() => window.removeEventListener('autope:identity-changed', onIdentityChanged))

const navigation = [
  { to: '/', name: 'dashboard', label: '总览', note: '风险与待办', icon: 'apps-o' },
  { to: '/targets', name: 'targets', label: 'Dify 目标', note: '被测工作流', icon: 'cluster-o' },
  { to: '/datasets', name: 'datasets', label: '测试集', note: '上传与冻结', icon: 'notes-o' },
  { to: '/reviews', name: 'reviews', label: '逐条审核', note: '查看回答与判断', icon: 'todo-list-o' },
  { to: '/runs', name: 'runs', label: '评测运行', note: '创建与状态', icon: 'play-circle-o' },
  { to: '/reports', name: 'reports', label: '评测报告', note: '结果与逐条依据', icon: 'chart-trending-o' },
  { to: '/optimizer', name: 'optimizer', label: 'Prompt 优化', note: '生成与绑定', icon: 'edit' },
  { to: '/releases', name: 'releases', label: '发布与回滚', note: '人工交接', icon: 'shield-o' },
  { to: '/accounts', name: 'accounts', label: '账号管理', note: '邀请与角色', icon: 'friends-o' },
]
</script>

<template>
  <SetupPage v-if="needsSetup" @initialized="onInitialized" />
  <LoginPage v-if="needsLogin" @logged-in="refreshSession" />
  <div v-if="hasAuthenticatedOnce || (!isCheckingSession && !needsSetup && !needsLogin)" v-show="!needsSetup && !needsLogin" class="app-shell">
    <aside class="sidebar" aria-label="主导航">
      <div class="brand">
        <AppIcon />
        <div>
          <strong>Dify AutoPE</strong>
          <span>独立评测控制面</span>
        </div>
      </div>
      <nav>
        <RouterLink v-for="item in navigation.filter((entry) => session && canVisitRoute(session, entry.name))" :key="item.to" :to="item.to" class="nav-link">
          <van-icon :name="item.icon" />
          <span><strong>{{ item.label }}</strong><small>{{ item.note }}</small></span>
        </RouterLink>
      </nav>
      <div class="sidebar-note">Dify 仅作为被测目标；审批与门禁由平台持有。</div>
    </aside>
    <main class="workspace">
      <header class="top-strip">
        <div><span class="top-strip-label">Evaluation control room</span><strong>工作流质量看板</strong></div>
        <div class="top-strip-actions"><van-tag v-if="session" round type="primary">{{ session.authentication === 'disabled_local' ? '开发环境 · 未鉴权' : `${session.environment} · ${session.display_name || session.subject}` }}</van-tag><van-tag v-else round type="warning">身份待确认</van-tag><van-button v-if="session?.authentication === 'local_session'" size="small" plain @click="showPasswordChange ? closePasswordChange() : (showPasswordChange = true)">{{ showPasswordChange ? '取消改密' : '修改密码' }}</van-button><van-button v-if="session?.authentication === 'local_session'" size="small" plain @click="signOut">退出登录</van-button></div>
      </header>
      <p v-if="session?.authentication === 'disabled_local'" class="identity-notice" role="status">当前为本地全角色演练，标注与审批不具备生产身份隔离效力。</p>
      <section v-if="showPasswordChange && session?.authentication === 'local_session'" class="password-panel chart-panel" aria-label="修改密码">
        <h2>修改我的密码</h2>
        <p>提交后当前会话和其他设备会话均失效，请使用新密码重新登录。</p>
        <form @submit.prevent="submitPasswordChange"><label>当前密码<input v-model="currentPassword" type="password" autocomplete="current-password" required /></label><label>新密码<input v-model="newPassword" type="password" autocomplete="new-password" minlength="12" required /></label><p v-if="passwordError" class="error-message" role="alert">{{ passwordError }}</p><van-button native-type="submit" type="primary" :loading="isChangingPassword">确认修改</van-button></form>
      </section>
      <section v-if="isCheckingSession || sessionError" class="chart-panel identity-block" role="status">
        <h1>{{ isCheckingSession ? '正在确认操作身份' : '操作已锁定' }}</h1>
        <p>{{ isCheckingSession ? '请稍候，正在检查平台会话。' : sessionError }}</p>
        <van-button v-if="!isCheckingSession" type="primary" @click="refreshSession">重试身份检查</van-button>
      </section>
      <section v-else-if="!canOpenRoute" class="chart-panel identity-block" role="alert">
        <h1>无权访问此页面</h1>
        <p>当前账号没有此页面的查看权限。请从左侧选择可用工作台；如需调整权限，请联系平台管理员。</p>
      </section>
      <div v-if="hasAuthenticatedOnce && (canOpenRoute || (!session && !needsSetup))" v-show="canOpenRoute && !isCheckingSession && !sessionError" class="route-stage"><RouterView :key="routeViewVersion" /></div>
    </main>
  </div>
</template>

<style scoped>
.identity-notice { margin: 16px 0; padding: 10px 14px; border-radius: 10px; background: #fff0d5; color: #64421b; font-size: 13px; line-height: 1.6; }
.identity-block { display: grid; justify-items: start; gap: 12px; margin-top: 24px; }.identity-block h1, .identity-block p { margin: 0; }.identity-block h1 { font-size: 22px; }.identity-block p { color: var(--muted); line-height: 1.65; }
.password-panel { display: grid; gap: 12px; max-width: 540px; margin: 18px 0; padding: 22px; }.password-panel h2, .password-panel p { margin: 0; }.password-panel p { color: var(--muted); font-size: 13px; }.password-panel form { display: grid; gap: 12px; }.password-panel label { display: grid; gap: 6px; font-size: 13px; }.password-panel input { min-height: 42px; }.password-panel .error-message { color: #a33a2c; }
</style>

