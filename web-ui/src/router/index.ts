import { watch } from 'vue'
import { createRouter, createWebHistory, type RouteLocationGeneric } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'
import { useAuth } from '@/composables/useAuth'
import { i18n, t } from '@/i18n'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginView.vue'),
    meta: { titleKey: 'routes.login' },
  },
  {
    path: '/',
    component: MainLayout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { titleKey: 'routes.dashboard', requiresAuth: true },
      },
      {
        path: 'tasks',
        name: 'Tasks',
        redirect: (to: RouteLocationGeneric) => ({ path: '/monitor', query: { ...to.query } }),
      },
      {
        path: 'results',
        name: 'Results',
        redirect: (to: RouteLocationGeneric) => ({ path: '/monitor', query: { ...to.query } }),
      },
      {
        path: 'accounts',
        name: 'Accounts',
        redirect: '/admin?tab=accounts',
      },
      {
        path: 'logs',
        name: 'Logs',
        redirect: '/admin?tab=logs',
      },
      {
        path: 'settings',
        name: 'Settings',
        redirect: (to: RouteLocationGeneric) => ({
          path: '/admin',
          query: { tab: typeof to.query.tab === 'string' ? to.query.tab : 'system' },
        }),
      },
      {
        path: 'monitor',
        name: 'Monitor',
        component: () => import('@/views/MonitorView.vue'),
        meta: { titleKey: 'routes.monitor', requiresAuth: true },
      },
      {
        path: 'admin',
        name: 'Admin',
        component: () => import('@/views/AdminView.vue'),
        meta: { titleKey: 'routes.admin', requiresAuth: true },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

function updateDocumentTitle() {
  const currentRoute = router.currentRoute.value
  const titleKey = typeof currentRoute.meta.titleKey === 'string'
    ? currentRoute.meta.titleKey
    : null
  const appName = t('app.name')
  document.title = titleKey ? `${t(titleKey)} - ${appName}` : appName
}

router.beforeEach((to, _from, next) => {
  const { isAuthenticated } = useAuth()

  if (to.meta.requiresAuth && !isAuthenticated.value) {
    next({ name: 'Login', query: { redirect: to.fullPath } })
  } else if (to.name === 'Login' && isAuthenticated.value) {
    next({ name: 'Dashboard' })
  } else {
    next()
  }
})

router.afterEach(() => {
  updateDocumentTitle()
})

watch(
  () => i18n.global.locale.value,
  () => {
    updateDocumentTitle()
  },
)

export default router
