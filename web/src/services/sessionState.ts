import { shallowRef } from 'vue'

import type { SessionIdentity } from '@/types'

// Presentation only. Every API request is independently authorized by the server.
export const activeSession = shallowRef<SessionIdentity>()
