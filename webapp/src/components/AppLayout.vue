<template>
  <div class="app-layout">
    <!-- Sidebar -->
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="brand-logo">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
            <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" stroke="#E5484D" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <span class="brand-text">因子工厂</span>
      </div>

      <nav class="sidebar-nav">
        <div class="nav-section">
          <span class="nav-section-title">工作区</span>
          <router-link to="/" class="nav-item" :class="{ active: route.path === '/' }">
            <el-icon size="18"><Grid /></el-icon>
            <span>任务看板</span>
          </router-link>
          <router-link to="/scheduled-tasks" class="nav-item" :class="{ active: route.path === '/scheduled-tasks' }">
            <el-icon size="18"><Clock /></el-icon>
            <span>计划任务</span>
          </router-link>
        </div>

        <div class="nav-section">
          <span class="nav-section-title">配置</span>
          <router-link to="/settings" class="nav-item" :class="{ active: route.path === '/settings' }">
            <el-icon size="18"><Setting /></el-icon>
            <span>系统设置</span>
          </router-link>
        </div>
      </nav>

      <div class="sidebar-footer">
        <div class="user-info">
          <el-icon size="16"><UserFilled /></el-icon>
          <span class="user-name">{{ authStore.username || '用户' }}</span>
        </div>
        <el-button text type="info" size="small" @click="handleLogout">
          <el-icon><SwitchButton /></el-icon>
        </el-button>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="main-content">
      <slot />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { Grid, Clock, Setting, UserFilled, SwitchButton } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.sidebar {
  width: var(--sb-sidebar-width);
  background: var(--sb-sidebar);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  overflow-y: auto;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.brand-logo {
  width: 36px;
  height: 36px;
  background: rgba(229, 72, 77, 0.15);
  border-radius: var(--sb-radius);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.brand-text {
  color: #ffffff;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.3px;
}

.sidebar-nav {
  flex: 1;
  padding: 16px 12px;
}

.nav-section {
  margin-bottom: 24px;
}

.nav-section-title {
  display: block;
  padding: 0 12px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: rgba(255, 255, 255, 0.35);
  margin-bottom: 6px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  margin: 2px 0;
  border-radius: var(--sb-radius);
  color: rgba(255, 255, 255, 0.6);
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  transition: var(--sb-transition);
  position: relative;
}

.nav-item:hover {
  background: var(--sb-sidebar-hover);
  color: rgba(255, 255, 255, 0.9);
}

.nav-item.active {
  background: var(--sb-sidebar-hover);
  color: #ffffff;
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background: var(--sb-primary);
  border-radius: 0 2px 2px 0;
}

.sidebar-footer {
  padding: 16px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(255, 255, 255, 0.6);
  font-size: 13px;
}

.user-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.main-content {
  flex: 1;
  overflow-y: auto;
  background: var(--sb-bg);
}
</style>
