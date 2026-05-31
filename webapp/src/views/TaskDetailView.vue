<template>
  <div class="task-detail">
    <el-container>
      <el-header class="app-header">
        <div class="header-left">
          <el-button text @click="router.push('/')">
            <el-icon><ArrowLeft /></el-icon> 返回
          </el-button>
          <h2>{{ task?.taskName || '加载中...' }}</h2>
          <el-tag v-if="task" :type="statusMap[task.status]?.type" size="small">
            {{ statusMap[task.status]?.label }}
          </el-tag>
        </div>
        <div class="header-right">
          <el-button v-if="task?.status === 'RUNNING'" type="danger" size="small" @click="handleStop">
            停止
          </el-button>
          <el-popconfirm title="确定删除此任务？Staging目录将一并删除" @confirm="handleDelete">
            <template #reference>
              <el-button type="danger" text size="small">删除任务</el-button>
            </template>
          </el-popconfirm>
        </div>
      </el-header>

      <el-main v-loading="loading">
        <el-tabs v-model="activeTab" type="border-card">
          <el-tab-pane label="配置编辑" name="config">
            <div class="config-toolbar">
              <el-select v-model="currentConfig" style="width: 300px" @change="handleConfigChange">
                <el-option v-for="(label, key) in configLabels" :key="key" :label="label" :value="key" />
              </el-select>
              <div class="config-actions">
                <el-radio-group v-model="editMode" size="small">
                  <el-radio-button value="form">表单模式</el-radio-button>
                  <el-radio-button value="source">源码模式</el-radio-button>
                </el-radio-group>
                <el-button size="small" @click="handleResetConfig">重置为默认</el-button>
                <el-button type="primary" size="small" :loading="saving" @click="handleSaveConfig">保存</el-button>
              </div>
            </div>

            <div v-if="editMode === 'source'" class="editor-container">
              <div ref="editorRef" class="codemirror-editor"></div>
            </div>
            <div v-else class="form-container">
              <el-form label-width="180px" label-position="top">
                <el-form-item v-for="(value, key) in configForm" :key="String(key)" :label="String(key)">
                  <el-switch v-if="typeof value === 'boolean'" v-model="configForm[String(key)]" />
                  <el-input-number v-else-if="typeof value === 'number'" v-model="configForm[String(key)]"
                    :precision="4" :step="0.01" />
                  <el-input v-else v-model="configForm[String(key)]" />
                </el-form-item>
              </el-form>
            </div>
          </el-tab-pane>

          <el-tab-pane label="执行控制" name="execution">
            <div class="execution-panel">
              <el-card shadow="never">
                <template #header>启动任务</template>
                <el-form :inline="true">
                  <el-form-item label="选择 Step">
                    <el-select v-model="selectedStep" style="width: 240px">
                      <el-option v-for="(label, key) in stepLabels" :key="key" :label="label" :value="key" />
                    </el-select>
                  </el-form-item>
                  <el-form-item>
                    <el-button type="primary" :loading="starting" :disabled="task?.status === 'RUNNING'"
                      @click="handleStart">
                      启动执行
                    </el-button>
                  </el-form-item>
                </el-form>
              </el-card>

              <el-card shadow="never" style="margin-top: 16px">
                <template #header>任务状态</template>
                <el-descriptions :column="3" border>
                  <el-descriptions-item label="状态">
                    <el-tag :type="statusMap[task?.status || 'IDLE']?.type">
                      {{ statusMap[task?.status || 'IDLE']?.label }}
                    </el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="当前 Step">
                    {{ task?.currentStep ? `Step${task.currentStep}` : '-' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="进程 PID">
                    {{ task?.pid || '-' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="Staging 路径" :span="3">
                    {{ task?.stagingPath || '-' }}
                  </el-descriptions-item>
                </el-descriptions>
              </el-card>
            </div>
          </el-tab-pane>

          <el-tab-pane label="实时日志" name="logs">
            <div class="log-panel">
              <div class="log-toolbar">
                <el-button size="small" @click="fetchLogs">刷新</el-button>
                <el-button size="small" @click="clearLogDisplay">清屏</el-button>
                <el-switch v-model="autoScroll" active-text="自动滚动" style="margin-left: 12px" />
                <el-switch v-model="wsConnected" active-text="WebSocket" active-color="#67c23a"
                  inactive-color="#909399" style="margin-left: 12px" disabled />
              </div>
              <div ref="logContainerRef" class="log-container">
                <div v-for="(line, idx) in logLines" :key="idx" class="log-line">{{ line }}</div>
                <div v-if="logLines.length === 0" class="log-empty">暂无日志</div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </el-main>
    </el-container>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import {
  getTask, startTask, stopTask, getTaskLogs, deleteTask, type TaskResponse,
} from '../api/task'
import {
  getConfig, updateConfig, resetConfig, type ConfigResponse,
} from '../api/config'
import {
  formatDateTime, STATUS_MAP, STEP_LABELS, CONFIG_LABELS, getWebSocketUrl,
} from '../utils/constants'

const props = defineProps<{ taskId: number }>()
const router = useRouter()
const authStore = useAuthStore()

const task = ref<TaskResponse | null>(null)
const loading = ref(false)
const activeTab = ref('config')
const statusMap = STATUS_MAP
const stepLabels = STEP_LABELS
const configLabels = CONFIG_LABELS

const currentConfig = ref('env')
const editMode = ref<'form' | 'source'>('source')
const configContent = ref('')
const configForm = ref<Record<string, any>>({})
const saving = ref(false)

const selectedStep = ref('10')
const starting = ref(false)

const logLines = ref<string[]>([])
const logContainerRef = ref<HTMLDivElement>()
const autoScroll = ref(true)
const wsConnected = ref(false)
let ws: WebSocket | null = null

const editorRef = ref<HTMLDivElement>()
let editorView: any = null

async function fetchTask() {
  loading.value = true
  try {
    const res = await getTask(props.taskId)
    task.value = res.data
  } finally {
    loading.value = false
  }
}

async function handleConfigChange() {
  const res = await getConfig(props.taskId, currentConfig.value)
  configContent.value = res.data.content
  if (editMode.value === 'source') {
    updateEditorContent(configContent.value)
  } else {
    parseYamlToForm(configContent.value)
  }
}

async function handleSaveConfig() {
  saving.value = true
  try {
    let content = configContent.value
    if (editMode.value === 'form') {
      content = formToYaml(configForm.value)
    }
    await updateConfig(props.taskId, currentConfig.value, content)
    ElMessage.success('配置已保存')
  } finally {
    saving.value = false
  }
}

async function handleResetConfig() {
  await resetConfig(props.taskId, currentConfig.value)
  ElMessage.success('配置已重置')
  await handleConfigChange()
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
  } catch {
  }
}

async function handleDelete() {
  try {
    await deleteTask(props.taskId, true)
    ElMessage.success('任务已删除')
    router.push('/')
  } catch {
  }
}

async function fetchLogs() {
  try {
    const res = await getTaskLogs(props.taskId, 500)
    logLines.value = res.data
    await nextTick()
    scrollToBottom()
  } catch {
  }
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
    ws.onopen = () => {
      wsConnected.value = true
    }
    ws.onmessage = (event) => {
      logLines.value.push(event.data)
      if (logLines.value.length > 5000) {
        logLines.value = logLines.value.slice(-3000)
      }
      nextTick(scrollToBottom)
    }
    ws.onclose = () => {
      wsConnected.value = false
    }
    ws.onerror = () => {
      wsConnected.value = false
    }
  } catch {
    wsConnected.value = false
  }
}

function disconnectWebSocket() {
  if (ws) {
    ws.close()
    ws = null
  }
  wsConnected.value = false
}

function parseYamlToForm(yamlStr: string) {
  try {
    const lines = yamlStr.split('\n')
    const result: Record<string, any> = {}
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const colonIdx = trimmed.indexOf(':')
      if (colonIdx < 0) continue
      const key = trimmed.substring(0, colonIdx).trim()
      const val = trimmed.substring(colonIdx + 1).trim()
      if (!val || val.includes('\n')) continue
      if (val === 'true') {
        result[key] = true
      } else if (val === 'false') {
        result[key] = false
      } else if (!isNaN(Number(val)) && val !== '') {
        result[key] = Number(val)
      } else {
        result[key] = val
      }
    }
    configForm.value = result
  } catch {
    configForm.value = {}
  }
}

function formToYaml(form: Record<string, any>): string {
  const lines: string[] = []
  for (const [key, value] of Object.entries(form)) {
    if (typeof value === 'boolean') {
      lines.push(`${key}: ${value}`)
    } else if (typeof value === 'number') {
      lines.push(`${key}: ${value}`)
    } else {
      lines.push(`${key}: ${value}`)
    }
  }
  return lines.join('\n')
}

async function initEditor() {
  const { EditorState } = await import('@codemirror/state')
  const { EditorView, keymap } = await import('@codemirror/view')
  const { yaml } = await import('@codemirror/lang-yaml')
  const { oneDark } = await import('@codemirror/theme-one-dark')
  const { defaultKeymap } = await import('@codemirror/commands')

  if (editorRef.value && !editorView) {
    editorView = new EditorView({
      state: EditorState.create({
        doc: configContent.value,
        extensions: [
          yaml(),
          oneDark,
          keymap.of(defaultKeymap),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) {
              configContent.value = update.state.doc.toString()
            }
          }),
        ],
      }),
      parent: editorRef.value,
    })
  }
}

function updateEditorContent(content: string) {
  if (editorView) {
    const { EditorState } = require('@codemirror/state')
    editorView.setState(EditorState.create({
      doc: content,
      extensions: editorView.state.extensions,
    }))
  }
}

watch(editMode, (mode) => {
  if (mode === 'form') {
    parseYamlToForm(configContent.value)
  }
})

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
  await handleConfigChange()
  await initEditor()

  pollTimer = setInterval(async () => {
    if (task.value?.status === 'RUNNING') {
      await fetchTask()
    }
  }, 5000)
})

onUnmounted(() => {
  disconnectWebSocket()
  if (pollTimer) clearInterval(pollTimer)
  if (editorView) editorView.destroy()
})
</script>

<style scoped>
.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 24px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-left h2 {
  margin: 0;
  font-size: 18px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.config-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.config-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.editor-container {
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;
}

.codemirror-editor {
  height: 600px;
}

.codemirror-editor :deep(.cm-editor) {
  height: 600px;
}

.form-container {
  max-height: 600px;
  overflow-y: auto;
  padding: 16px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
}

.execution-panel {
  max-width: 800px;
}

.log-panel {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 240px);
}

.log-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
  gap: 8px;
}

.log-container {
  flex: 1;
  background: #1e1e1e;
  color: #d4d4d4;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.6;
  padding: 12px;
  border-radius: 4px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-line {
  min-height: 1.6em;
}

.log-empty {
  color: #666;
  text-align: center;
  padding: 40px;
}
</style>
