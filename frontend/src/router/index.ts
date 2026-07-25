import { createRouter, createWebHistory, type RouteRecordRaw, type Router, type RouterHistory } from 'vue-router'
import { UNAUTHORIZED_EVENT } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import LoginView from '@/views/LoginView.vue'
import RegisterView from '@/views/RegisterView.vue'
import WorkspaceView from '@/views/WorkspaceView.vue'

const routes: RouteRecordRaw[] = [
    { path: '/', redirect: '/workspace' },
    { path: '/login', name: 'login', component: LoginView, meta: { guestOnly: true } },
    { path: '/register', name: 'register', component: RegisterView, meta: { guestOnly: true } },
    { path: '/workspace', name: 'workspace', component: WorkspaceView, meta: { requiresAuth: true } },
    { path: '/:pathMatch(.*)*', redirect: '/workspace' },
]

export function createAppRouter(history: RouterHistory = createWebHistory()): Router {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    const auth = useAuthStore()
    await auth.initialize()
    if (to.meta.requiresAuth && !auth.isAuthenticated) return { name: 'login', query: { redirect: to.fullPath } }
    if (to.meta.guestOnly && auth.isAuthenticated) return { name: 'workspace' }
    return true
  })

  return router
}

export function installUnauthorizedRedirect(router: Router): () => void {
  const handleUnauthorized = () => {
    const auth = useAuthStore()
    const wasInitializing = auth.initializing
    const current = router.currentRoute.value
    const redirect = current.fullPath
    auth.logout()

    // /auth/me 的 401 由当前导航守卫处理；登录/注册请求失败也不需要再次导航。
    if (wasInitializing || current.meta.guestOnly) return
    void router.replace({
      name: 'login',
      query: redirect && redirect !== '/' ? { redirect } : {},
    })
  }
  window.addEventListener(UNAUTHORIZED_EVENT, handleUnauthorized)
  return () => window.removeEventListener(UNAUTHORIZED_EVENT, handleUnauthorized)
}

const router = createAppRouter()

export default router
