import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/LoginView.vue'),
    },
    {
      path: '/',
      name: 'Dashboard',
      component: () => import('../views/DashboardView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/scheduled-tasks',
      name: 'ScheduledTasks',
      component: () => import('../views/ScheduledTaskView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/task/:taskId',
      name: 'TaskDetail',
      component: () => import('../views/TaskDetailView.vue'),
      meta: { requiresAuth: true },
      props: true,
    },
    {
      path: '/task/:taskId/config',
      name: 'TaskConfig',
      component: () => import('../views/TaskConfigView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/settings',
      name: 'SystemSettings',
      component: () => import('../views/SystemSettingsView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to) => {
  const authStore = useAuthStore()
  if (to.meta.requiresAuth && !authStore.isLoggedIn()) {
    return { name: 'Login' }
  }
  if (to.name === 'Login' && authStore.isLoggedIn()) {
    return { name: 'Dashboard' }
  }
})

export default router
