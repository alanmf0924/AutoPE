import type { SessionIdentity } from '@/types'

type BusinessRole = SessionIdentity['roles'][number]

const routeRoles: Record<string, readonly BusinessRole[]> = {
  dashboard: ['operator'],
  targets: ['operator', 'prompt_owner', 'approver', 'auditor'],
  'target-detail': ['operator', 'prompt_owner', 'approver', 'auditor'],
  datasets: ['operator', 'prompt_owner', 'approver', 'auditor'],
  'dataset-detail': ['operator', 'prompt_owner', 'approver', 'auditor'],
  reviews: ['operator', 'annotator', 'auditor'],
  'review-detail': ['operator', 'annotator', 'auditor'],
  runs: ['operator', 'prompt_owner', 'approver', 'auditor'],
  reports: ['operator', 'prompt_owner', 'approver', 'auditor'],
  'report-detail': ['operator', 'prompt_owner', 'approver', 'auditor'],
  optimizer: ['operator', 'prompt_owner', 'auditor'],
  'candidate-detail': ['operator', 'prompt_owner', 'approver', 'auditor'],
  releases: ['operator', 'approver', 'auditor'],
}

export function canVisitRoute(session: SessionIdentity, routeName: unknown): boolean {
  if (routeName === 'accounts') {
    return session.is_platform_admin
  }
  if (typeof routeName !== 'string') {
    return false
  }
  const allowedRoles = routeRoles[routeName]
  return Array.isArray(allowedRoles) && session.roles.some((role) => allowedRoles.includes(role))
}

export function homeRoute(session: SessionIdentity): string {
  for (const name of ['dashboard', 'reviews', 'optimizer', 'releases', 'reports', 'accounts']) {
    if (canVisitRoute(session, name)) {
      return name
    }
  }
  return ''
}
