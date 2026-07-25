import { createRouter, createWebHistory, type RouteRecordRaw, type Router, type RouterHistory } from 'vue-router'
import { UNAUTHORIZED_EVENT } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/workspaces'
import {
  revokedWorkspaceId,
  WORKSPACE_ACCESS_REVOKED_EVENT,
} from '@/features/workspace/accessRevocation'
import LoginView from '@/views/LoginView.vue'
import RegisterView from '@/views/RegisterView.vue'
import WorkspaceView from '@/views/WorkspaceView.vue'
import WorkspaceResolverView from '@/views/WorkspaceResolverView.vue'
import WorkspaceMembersView from '@/views/WorkspaceMembersView.vue'
import WorkspaceAuditView from '@/views/WorkspaceAuditView.vue'

const routes: RouteRecordRaw[] = [
    { path: '/', redirect: '/workspace' },
    { path: '/login', name: 'login', component: LoginView, meta: { guestOnly: true } },
    { path: '/register', name: 'register', component: RegisterView, meta: { guestOnly: true } },
    { path: '/workspace', name: 'workspace-resolve', component: WorkspaceResolverView, meta: { requiresAuth: true } },
    {
      path: '/w/:workspaceId',
      name: 'workspace-home',
      component: WorkspaceView,
      meta: { requiresAuth: true, workspaceScoped: true },
    },
    {
      path: '/w/:workspaceId/members',
      name: 'workspace-members',
      component: WorkspaceMembersView,
      meta: { requiresAuth: true, workspaceScoped: true, requiresManagement: true },
    },
    {
      path: '/w/:workspaceId/audit',
      name: 'workspace-audit',
      component: WorkspaceAuditView,
      meta: { requiresAuth: true, workspaceScoped: true, requiresManagement: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/workspace' },
]

export function createAppRouter(history: RouterHistory = createWebHistory()): Router {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    const auth = useAuthStore()
    await auth.initialize()
    if (to.meta.requiresAuth && !auth.isAuthenticated) return { name: 'login', query: { redirect: to.fullPath } }
    if (to.meta.guestOnly && auth.isAuthenticated) return { name: 'workspace-resolve' }
    if (to.meta.workspaceScoped && auth.user) {
      const workspaces = useWorkspaceStore()
      try {
        await workspaces.initialize(auth.user.id)
      } catch {
        return { name: 'workspace-resolve' }
      }
      const requestedId = typeof to.params.workspaceId === 'string' ? to.params.workspaceId : ''
      if (!requestedId || !workspaces.select(requestedId, auth.user.id)) {
        const fallback = workspaces.fallback(auth.user.id)
        return fallback
          ? { name: 'workspace-home', params: { workspaceId: fallback.id }, query: { unavailable: '1' } }
          : { name: 'workspace-resolve' }
      }
      if (to.meta.requiresManagement && !workspaces.canManage) {
        return {
          name: 'workspace-home',
          params: { workspaceId: requestedId },
          query: { permission: 'denied' },
        }
      }
    }
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

export function installWorkspaceAccessRecovery(router: Router): () => void {
  const handleRevocation = (event: Event) => {
    const revokedId = revokedWorkspaceId(event)
    if (!revokedId) return
    const auth = useAuthStore()
    const workspaces = useWorkspaceStore()
    if (!auth.user || workspaces.activeWorkspaceId !== revokedId) return
    const userId = auth.user.id

    void workspaces.recoverAccessRevocation(revokedId, userId)
      .then(async (fallback) => {
        if (useAuthStore().user?.id !== userId) return
        const currentWorkspaceId = router.currentRoute.value.params.workspaceId
        if (currentWorkspaceId !== revokedId) return
        if (fallback) {
          await router.replace({
            name: 'workspace-home',
            params: { workspaceId: fallback.id },
            query: { revoked: '1' },
          })
        } else {
          await router.replace({ name: 'workspace-resolve' })
        }
      })
      .catch(() => undefined)
  }
  window.addEventListener(WORKSPACE_ACCESS_REVOKED_EVENT, handleRevocation)
  return () => window.removeEventListener(WORKSPACE_ACCESS_REVOKED_EVENT, handleRevocation)
}

const router = createAppRouter()

export default router
