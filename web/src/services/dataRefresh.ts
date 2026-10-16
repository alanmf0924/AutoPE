import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

export const platformDataResources = [
  'dashboard',
  'targets',
  'datasets',
  'candidates',
  'runs',
  'reviews',
  'reports',
  'releases',
] as const

export type PlatformDataResource = (typeof platformDataResources)[number]

type PlatformRefreshOptions = {
  resources: readonly PlatformDataResource[]
  refresh: () => Promise<void>
  pollIntervalMs?: number
}

export type PlatformDataRefreshController = {
  refresh: () => Promise<void>
  readonly isRefreshing: boolean
}

type PlatformRefreshMessage = {
  type: 'platform-data-refresh'
  resources: PlatformDataResource[]
}

const refreshRevisions = reactive<Record<PlatformDataResource, number>>({
  dashboard: 0,
  targets: 0,
  datasets: 0,
  candidates: 0,
  runs: 0,
  reviews: 0,
  reports: 0,
  releases: 0,
})

const broadcastChannel = typeof BroadcastChannel === 'undefined'
  ? undefined
  : new BroadcastChannel('dify-autope-platform-data')

function isPlatformDataResource(value: unknown): value is PlatformDataResource {
  return typeof value === 'string' && platformDataResources.includes(value as PlatformDataResource)
}

function readRefreshResources(value: unknown): PlatformDataResource[] {
  if (typeof value !== 'object' || value === null || !('type' in value) || !('resources' in value)) {
    return []
  }
  const message = value as { type?: unknown; resources?: unknown }
  if (message.type !== 'platform-data-refresh' || !Array.isArray(message.resources)) {
    return []
  }
  return message.resources.filter(isPlatformDataResource)
}

function incrementRevisions(resources: readonly PlatformDataResource[]): void {
  for (const resource of new Set(resources)) {
    refreshRevisions[resource] += 1
  }
}

broadcastChannel?.addEventListener('message', (event: MessageEvent<unknown>) => {
  incrementRevisions(readRefreshResources(event.data))
})

export function notifyPlatformDataChanged(...resources: PlatformDataResource[]): void {
  const changedResources = [...new Set(resources)]
  if (changedResources.length === 0) {
    return
  }
  incrementRevisions(changedResources)
  const message: PlatformRefreshMessage = { type: 'platform-data-refresh', resources: changedResources }
  broadcastChannel?.postMessage(message)
}

export function usePlatformDataRefresh(options: PlatformRefreshOptions): PlatformDataRefreshController {
  let isDisposed = false
  let hasRefreshQueued = false
  let timerId: number | undefined
  const isRefreshing = ref(false)

  const refreshSignature = computed(() => options.resources.map((resource) => refreshRevisions[resource]).join(':'))

  async function refresh(): Promise<void> {
    if (isDisposed) {
      return
    }
    if (isRefreshing.value) {
      hasRefreshQueued = true
      return
    }
    isRefreshing.value = true
    try {
      await options.refresh()
    } finally {
      isRefreshing.value = false
      if (hasRefreshQueued && !isDisposed) {
        hasRefreshQueued = false
        void refresh()
      }
    }
  }

  function refreshWhenVisible(): void {
    if (typeof document === 'undefined' || document.visibilityState === 'visible') {
      void refresh()
    }
  }

  watch(refreshSignature, () => {
    void refresh()
  })

  onMounted(() => {
    refreshWhenVisible()
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', refreshWhenVisible)
    const pollIntervalMs = options.pollIntervalMs ?? 0
    if (pollIntervalMs >= 1000 && typeof window !== 'undefined') {
      timerId = window.setInterval(refreshWhenVisible, pollIntervalMs)
    }
  })

  onBeforeUnmount(() => {
    isDisposed = true
    if (timerId !== undefined) {
      window.clearInterval(timerId)
    }
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  })

  return {
    refresh,
    get isRefreshing(): boolean {
      return isRefreshing.value
    },
  }
}
