import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteLocationRaw,
  type Router,
  type RouteRecordRaw,
  type RouterHistory,
} from 'vue-router'

import { setSessionLostHandler } from '../api/client'
import { useSessionStore } from '../stores/session'

// Each frontend task adds its routes here.
const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', redirect: { name: 'projects' } },
  { path: '/setup', name: 'setup', component: () => import('../views/SetupView.vue') },
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
  {
    path: '/password',
    name: 'password',
    component: () => import('../views/PasswordChangeView.vue'),
  },
  {
    path: '/projects',
    name: 'projects',
    component: () => import('../views/ProjectListView.vue'),
  },
  {
    path: '/projects/:id',
    name: 'project',
    component: () => import('../views/ProjectDetailView.vue'),
    props: (route) => ({ id: Number(route.params.id) }),
  },
  {
    path: '/projects/:id/scans',
    name: 'scans',
    component: () => import('../views/ScanListView.vue'),
    props: (route) => ({ id: Number(route.params.id) }),
  },
  {
    // The workspace of a project: its latest scan, a chosen scan (?scan=) or
    // the whole project (?view=project), with the host filters in the query.
    path: '/projects/:id/workspace',
    name: 'workspace',
    component: () => import('../views/ProjectWorkspaceView.vue'),
    props: (route) => ({ projectId: Number(route.params.id) }),
  },
  {
    // A link to one scan opens the workspace of its project on that scan.
    path: '/scans/:id',
    name: 'scan',
    component: () => import('../views/ProjectWorkspaceView.vue'),
    props: (route) => ({ scanId: Number(route.params.id) }),
  },
  {
    path: '/projects/:id/schedules',
    name: 'schedules',
    component: () => import('../views/SchedulesView.vue'),
    props: (route) => ({ id: Number(route.params.id) }),
  },
  {
    // Two scans of a project, chosen in the view (?base=&target=), compared now.
    path: '/projects/:id/compare',
    name: 'compare',
    component: () => import('../views/ComparisonView.vue'),
    props: (route) => ({ projectId: Number(route.params.id) }),
  },
  {
    // A stored automatic diff, as a change alert links to it.
    path: '/diffs/:id',
    name: 'diff',
    component: () => import('../views/ComparisonView.vue'),
    props: (route) => ({ diffId: Number(route.params.id) }),
  },
  { path: '/alerts', name: 'alerts', component: () => import('../views/AlertsView.vue') },
  { path: '/audit', name: 'audit', component: () => import('../views/AuditLogView.vue') },
  {
    path: '/settings/datasets',
    name: 'datasets',
    component: () => import('../views/DatasetSettingsView.vue'),
  },
]

/**
 * Decide where a navigation may go.
 *
 * Without a session every route except setup and login leads to the login
 * view. This only steers the interface; the server enforces authentication.
 */
export async function guard(to: RouteLocationNormalized): Promise<RouteLocationRaw | true> {
  const session = useSessionStore()
  if (!session.loaded) await session.load()

  if (session.setupRequired) return to.name === 'setup' ? true : { name: 'setup' }
  if (to.name === 'setup') return { name: 'login' }

  if (!session.isAuthenticated) {
    if (to.name === 'login') return true
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login') return { name: 'home' }
  return true
}

export function createAppRouter(history: RouterHistory): Router {
  const router = createRouter({ history, routes })
  router.beforeEach(guard)

  // A 401 from any call is handled like a navigation without a session.
  setSessionLostHandler(() => {
    const session = useSessionStore()
    const current = router.currentRoute.value
    session.expire()
    if (session.loaded && current.name !== 'login' && current.name !== 'setup') {
      void router.push({ name: 'login', query: { redirect: current.fullPath } })
    }
  })
  return router
}

export default createAppRouter(createWebHistory())
