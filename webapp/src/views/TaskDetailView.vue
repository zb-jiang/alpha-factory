<template>
  <AppLayout>
    <div class="task-detail-page">
      <!-- Page Header -->
      <div class="sb-page-header">
        <div class="header-row">
          <div class="header-left">
            <el-button text @click="router.push('/')" class="back-btn">
              <el-icon><ArrowLeft /></el-icon>
            </el-button>
            <div>
              <h1>{{ task?.taskName || '加载中...' }}</h1>
            </div>
          </div>
          <div class="header-actions">
            <el-button v-if="task?.status === 'NEW'" type="primary" size="large" @click="goToConfig">
              <el-icon><Setting /></el-icon> 配置任务
            </el-button>
            <template v-if="task?.status === 'RUNNING'">
              <el-button type="danger" size="large" @click="handleStop">
                <el-icon><VideoPause /></el-icon> 停止
              </el-button>
            </template>
            <template v-else-if="task?.status !== 'NEW'">
              <el-button type="primary" size="large" :loading="starting" @click="handleStart">
                <el-icon><VideoPlay /></el-icon> 启动
              </el-button>
            </template>
            <el-divider direction="vertical" class="action-divider" />
            <el-popconfirm title="确定删除此任务？Staging目录将一并删除" @confirm="handleDelete">
              <template #reference>
                <el-button size="large" text type="danger" class="delete-btn">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>
      </div>

      <!-- Content -->
      <div class="sb-content">
        <el-row :gutter="20">
          <!-- Left: Info Cards -->
          <el-col :span="16">
            <div class="sb-section" style="padding: 0; overflow: hidden;">
              <div class="detail-tabs">
                <div
                  v-for="tab in tabs"
                  :key="tab.key"
                  :class="['detail-tab', { active: activeTab === tab.key }]"
                  @click="activeTab = tab.key"
                >
                  {{ tab.label }}
                </div>
              </div>

              <!-- Overview -->
              <div v-if="activeTab === 'overview'" class="tab-content">
                <div class="info-grid">
                  <div class="info-item" style="grid-column: span 3;">
                    <span class="info-label">创建时间</span>
                    <span class="info-value">{{ formatDateTime(task?.createdAt || null) }}</span>
                  </div>
                  <div class="info-item" style="grid-column: span 3;">
                    <span class="info-label">Staging 路径</span>
                    <span class="info-value mono">{{ task?.stagingPath || '-' }}</span>
                  </div>
                </div>

                <div v-if="task?.status === 'NEW'" class="next-step-banner">
                  <el-icon size="20" color="var(--sb-primary)"><InfoFilled /></el-icon>
                  <div class="next-step-text">
                    <p>任务尚未配置，请先完成配置后再启动执行。</p>
                  </div>
                  <el-button type="primary" @click="goToConfig">前往配置</el-button>
                </div>

                <div v-if="task?.status === 'RUNNING' || task?.status === 'COMPLETED'" class="next-step-banner">
                  <el-icon size="20" color="var(--sb-primary)"><InfoFilled /></el-icon>
                  <div class="next-step-text">
                    <p>任务{{ task.status === 'RUNNING' ? '正在运行中' : '已完成' }}，可切换至「实时日志」查看详细输出。</p>
                  </div>
                </div>
              </div>

              <!-- Execution -->
              <div v-if="activeTab === 'execution'" class="tab-content">
                <div class="exec-form">
                  <div class="form-group-header">
                    <h4>启动参数</h4>
                    <p class="group-desc">选择要执行的 Step 并启动任务</p>
                  </div>
                  <div class="exec-row">
                    <el-select v-model="selectedStep" size="large" style="width: 280px">
                      <el-option v-for="(label, key) in stepLabels" :key="key" :label="label" :value="key" />
                    </el-select>
                    <el-button type="primary" size="large" :loading="starting" :disabled="task?.status === 'RUNNING'" @click="handleStart">
                      <el-icon><VideoPlay /></el-icon> 启动执行
                    </el-button>
                  </div>
                </div>
              </div>

              <!-- Logs -->
              <div v-if="activeTab === 'logs'" class="tab-content logs-tab">
                <div class="log-toolbar">
                  <el-button size="small" text @click="fetchLogs">
                    <el-icon><Refresh /></el-icon> 刷新
                  </el-button>
                  <el-button size="small" text @click="clearLogDisplay">
                    <el-icon><Delete /></el-icon> 清屏
                  </el-button>
                  <el-switch v-model="autoScroll" active-text="自动滚动" size="small" style="margin-left: auto" />
                  <el-tag size="small" :type="wsConnected ? 'success' : 'info'" style="margin-left: 12px">
                    {{ wsConnected ? 'WebSocket 已连接' : 'WebSocket 未连接' }}
                  </el-tag>
                </div>
                <div ref="logContainerRef" class="log-container">
                  <div v-for="(line, idx) in logLines" :key="idx" class="log-line">{{ line }}</div>
                  <div v-if="logLines.length === 0" class="log-empty">暂无日志</div>
                </div>
              </div>
            </div>
          </el-col>

          <!-- Right: Quick Info -->
          <el-col :span="8">
            <div class="sb-section">
              <div class="sb-section-header">
                <el-icon size="20" color="var(--sb-primary)"><InfoFilled /></el-icon>
                <div>
                  <h3>任务信息</h3>
                  <p>当前任务的简要概况</p>
                </div>
              </div>
              <div class="quick-info-list">
                <div class="quick-info-item">
                  <span class="quick-label">状态</span>
                  <el-tag :class="statusClass" size="small" effect="light">{{ statusLabel }}</el-tag>
                </div>
                <div class="quick-info-item">
                  <span class="quick-label">Step</span>
                  <span class="quick-value">{{ task?.currentStep || '-' }}</span>
                </div>
                <div class="quick-info-item">
                  <span class="quick-label">PID</span>
                  <span class="quick-value">{{ task?.pid || '-' }}</span>
                </div>
                <div class="quick-info-item">
                  <span class="quick-label">创建时间</span>
                  <span class="quick-value">{{ formatDateTime(task?.createdAt || null) }}</span>
                </div>
              </div>
            </div>
          </el-col>
        </el-row>
      </div>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, watch, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Setting, VideoPlay, VideoPause, Delete, InfoFilled, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getTask, startTask, stopTask, getTaskLogs, deleteTask, type TaskResponse } from '../api/task'
import { formatDateTime, STEP_LABELS, getWebSocketUrl } from '../utils/constants'
import AppLayout from '../components/AppLayout.vue'

const props = defineProps<{ taskId: number }>()
const router = useRouter()

const task = ref<TaskResponse | null>(null)
const loading = ref(false)
const activeTab = ref('overview')
const stepLabels = STEP_LABELS

const tabs = [
  { key: 'overview', label: '任务概览' },
  { key: 'execution', label: '执行控制' },
  { key: 'logs', label: '实时日志' },
]

const statusClass = computed(() => {
  const map: Record<string, string> = { NEW: 'sb-status-new', RUNNING: 'sb-status-running', STOPPED: 'sb-status-stopped', COMPLETED: 'sb-status-completed', ERROR: 'sb-status-error' }
  return map[task.value?.status || 'NEW']
})
const statusLabel = computed(() => {
  const map: Record<string, string> = { NEW: '新建', RUNNING: '运行中', STOPPED: '已终止', COMPLETED: '已完成', ERROR: '错误' }
  return map[task.value?.status || 'NEW']
})

const selectedStep = ref('10')
const starting = ref(false)

const logLines = ref<string[]>([])
const logContainerRef = ref<HTMLDivElement>()
const autoScroll = ref(true)
const wsConnected = ref(false)
let ws: WebSocket | null = null

async function fetchTask() {
  loading.value = true
  try {
    const res = await getTask(props.taskId)
    task.value = res.data
  } finally {
    loading.value = false
  }
}

function goToConfig() {
  router.push({ name: 'TaskConfig', params: { taskId: props.taskId } })
}

async function handleStart() {
  starting.value = true
  try {
    await startTask(props.taskId, { step: selectedStep.value })
    ElMessage.success('任务已启动')
    await fetchTask()
    connectWebSocket()
  } finally {
    starting.value = false
  }
}

async function handleStop() {
  try {
    await stopTask(props.taskId)
    ElMessage.success('任务已停止')
    await fetchTask()
    disconnectWebSocket()
  } catch { /* ignore */ }
}

async function handleDelete() {
  try {
    await deleteTask(props.taskId, true)
    ElMessage.success('任务已删除')
    router.push('/')
  } catch { /* ignore */ }
}

async function fetchLogs() {
  try {
    const res = await getTaskLogs(props.taskId, 500)
    logLines.value = res.data
    await nextTick()
    scrollToBottom()
  } catch { /* ignore */ }
}

function clearLogDisplay() {
  logLines.value = []
}

function scrollToBottom() {
  if (autoScroll.value && logContainerRef.value) {
    logContainerRef.value.scrollTop = logContainerRef.value.scrollHeight
  }
}

function connectWebSocket() {
  disconnectWebSocket()
  try {
    ws = new WebSocket(getWebSocketUrl(props.taskId))
    ws.onopen = () => { wsConnected.value = true }
    ws.onmessage = (event) => {
      logLines.value.push(event.data)
      if (logLines.value.length > 5000) {
        logLines.value = logLines.value.slice(-3000)
      }
      nextTick(scrollToBottom)
    }
    ws.onclose = () => { wsConnected.value = false }
    ws.onerror = () => { wsConnected.value = false }
  } catch { wsConnected.value = false }
}

function disconnectWebSocket() {
  if (ws) { ws.close(); ws = null }
  wsConnected.value = false
}

watch(activeTab, (tab) => {
  if (tab === 'logs' && task.value?.status === 'RUNNING') {
    connectWebSocket()
  } else if (tab !== 'logs') {
    disconnectWebSocket()
  }
})

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  await fetchTask()
  pollTimer = setInterval(async () => {
    if (task.value?.status === 'RUNNING') {
      await fetchTask()
    }
  }, 5000)
})

onUnmounted(() => {
  disconnectWebSocket()
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.back-btn {
  padding: 8px;
  margin-right: 4px;
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
}

.header-desc {
  color: var(--sb-text-secondary);
  font-size: 13px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-actions .el-button {
  min-width: 88px;
}

.header-actions .el-button + .el-button {
  margin-left: 0;
}

.header-actions .action-divider {
  height: 24px;
  margin: 0 4px;
}

.header-actions .delete-btn {
  padding: 10px 14px;
  font-size: 16px;
}

.detail-tabs {
  display: flex;
  border-bottom: 1px solid var(--sb-border-light);
  background: var(--sb-surface);
}

.detail-tab {
  padding: 14px 24px;
  font-size: 14px;
  font-weight: 500;
  color: var(--sb-text-secondary);
  cursor: pointer;
  transition: var(--sb-transition);
  border-bottom: 2px solid transparent;
}

.detail-tab:hover {
  color: var(--sb-text);
}

.detail-tab.active {
  color: var(--sb-primary);
  border-bottom-color: var(--sb-primary);
}

.tab-content {
  padding: 24px;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.info-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--sb-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.info-value {
  font-size: 14px;
  color: var(--sb-text);
}

.info-value.mono {
  font-family: 'SF Mono', monospace;
  font-size: 12px;
  word-break: break-all;
}

.next-step-banner {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 24px;
  padding: 16px 20px;
  background: var(--sb-primary-light);
  border: 1px solid var(--sb-primary);
  border-radius: var(--sb-radius);
}

.next-step-text {
  flex: 1;
}

.next-step-text p {
  margin: 0;
  font-size: 14px;
  color: var(--sb-text);
}

.exec-form {
  max-width: 600px;
}

.form-group-header {
  margin-bottom: 20px;
}

.form-group-header h4 {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 4px;
}

.group-desc {
  color: var(--sb-text-secondary);
  font-size: 13px;
  margin: 0;
}

.exec-row {
  display: flex;
  gap: 12px;
}

.logs-tab {
  padding: 0;
}

.log-toolbar {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--sb-border-light);
  gap: 8px;
}

.log-container {
  height: 480px;
  background: #1a1a1a;
  color: #d4d4d4;
  font-family: 'Consolas', 'Monaco', 'SF Mono', monospace;
  font-size: 12px;
  line-height: 1.6;
  padding: 16px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-line {
  min-height: 1.6em;
}

.log-empty {
  color: #555;
  text-align: center;
  padding: 40px;
}

.quick-info-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.quick-info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--sb-border-light);
}

.quick-info-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.quick-label {
  font-size: 13px;
  color: var(--sb-text-secondary);
}

.quick-value {
  font-size: 13px;
  font-weight: 500;
  color: var(--sb-text);
  font-family: 'SF Mono', monospace;
}

@media (max-width: 768px) {
  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
