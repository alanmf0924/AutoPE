import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('@/views/DashboardPage.vue') },
    { path: '/targets', name: 'targets', component: () => import('@/views/TargetPage.vue') },
    { path: '/targets/:targetId', name: 'target-detail', component: () => import('@/views/TargetDetailPage.vue') },
    { path: '/datasets', name: 'datasets', component: () => import('@/views/DatasetPage.vue') },
    { path: '/datasets/:datasetId', name: 'dataset-detail', component: () => import('@/views/DatasetDetailPage.vue') },
    { path: '/reviews', name: 'reviews', component: () => import('@/views/ReviewPage.vue') },
    { path: '/reviews/:caseId', name: 'review-detail', component: () => import('@/views/ReviewDetailPage.vue') },
    { path: '/runs', name: 'runs', component: () => import('@/views/RunPage.vue') },
    { path: '/reports', name: 'reports', component: () => import('@/views/ReportPage.vue') },
    { path: '/reports/:runId', name: 'report-detail', component: () => import('@/views/ReportDetailPage.vue') },
    { path: '/optimizer', name: 'optimizer', component: () => import('@/views/OptimizerPage.vue') },
    { path: '/optimizer/candidates/:candidateId', name: 'candidate-detail', component: () => import('@/views/CandidateDetailPage.vue') },
    { path: '/releases', name: 'releases', component: () => import('@/views/ReleasePage.vue') },
    { path: '/accounts', name: 'accounts', component: () => import('@/views/AccountsPage.vue') },
  ],
})

