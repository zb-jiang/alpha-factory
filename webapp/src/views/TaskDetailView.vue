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
          <div class="header-meta-strip">
            <div class="meta-item">
              <span class="meta-key">状态</span>
              <el-tag :class="statusClass" size="small" effect="light">{{ statusLabel }}</el-tag>
            </div>
            <div class="meta-divider"></div>
            <div class="meta-item phase-item">
              <span class="meta-key">阶段</span>
              <template v-if="phaseDisplay.kind === 'idle'">
                <span class="meta-value">—</span>
              </template>
              <template v-else-if="phaseDisplay.kind === 'prep'">
                <span class="phase-chip phase-prep">{{ phaseDisplay.text }}</span>
              </template>
              <template v-else-if="phaseDisplay.kind === 'discovery'">
                <span class="phase-window">{{ phaseDisplay.windowId }}</span>
                <span class="phase-sep">·</span>
                <span class="phase-chip phase-discovery">DISCOVERY</span>
                <span class="phase-sep">·</span>
                <span class="phase-iter">iter {{ phaseDisplay.iteration }}</span>
              </template>
              <template v-else-if="phaseDisplay.kind === 'validation'">
                <span class="phase-window">{{ phaseDisplay.windowId }}</span>
                <span class="phase-sep">·</span>
                <span class="phase-chip phase-validation">VALIDATION</span>
              </template>
              <template v-else-if="phaseDisplay.kind === 'oos'">
                <span class="phase-chip phase-oos">{{ phaseDisplay.text }}</span>
              </template>
              <template v-else>
                <span class="phase-chip phase-other">{{ phaseDisplay.text }}</span>
              </template>
            </div>
            <div class="meta-divider"></div>
            <div class="meta-item">
              <span class="meta-key">PID</span>
              <span class="meta-value mono">{{ task?.pid || '—' }}</span>
            </div>
            <div class="meta-divider"></div>
            <div class="meta-item">
              <span class="meta-key">创建</span>
              <span class="meta-value">{{ formatDateTime(task?.createdAt || null) }}</span>
            </div>
          </div>
          <div class="header-actions">
          </div>
        </div>
      </div>

      <!-- Content -->
      <div class="sb-content">
        <el-row :gutter="20">
          <!-- 单列布局：原右侧"任务信息"已上移到页头紧凑栏 -->
          <el-col :span="24">
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

                <div v-if="task?.status === 'NEW' && !isConfigured" class="next-step-banner">
                  <el-icon size="20" color="var(--sb-primary)"><InfoFilled /></el-icon>
                  <div class="next-step-text">
                    <p>任务尚未配置，请先完成配置后再启动执行。</p>
                  </div>
                  <el-button type="primary" @click="goToConfig">前往配置</el-button>
                </div>

                <div v-else-if="task?.status === 'NEW' && isConfigured" class="next-step-banner ready">
                  <el-icon size="20" color="#409EFF"><InfoFilled /></el-icon>
                  <div class="next-step-text">
                    <p>任务已配置完成，可前往“因子挖掘”启动。</p>
                  </div>
                  <el-button type="primary" plain @click="activeTab = 'training'">前往因子挖掘</el-button>
                </div>

                <div v-else-if="task?.status === 'RUNNING'" class="next-step-banner ready">
                  <el-icon size="20" color="#409EFF"><InfoFilled /></el-icon>
                  <div class="next-step-text">
                    <p>任务正在运行中（Step {{ task.currentStep || '-' }}），可切换到对应 Tab 查看实时日志。</p>
                  </div>
                </div>

                <div v-else-if="task?.status === 'TRAINING_FINISHED'" class="next-step-banner ready">
                  <el-icon size="20" color="#409EFF"><InfoFilled /></el-icon>
                  <div class="next-step-text">
                    <p>因子挖掘已完成，可前往“样本外回测”启动 step11。</p>
                  </div>
                  <el-button type="primary" plain @click="activeTab = 'testing'">前往样本外回测</el-button>
                </div>

                <div v-else-if="task?.status === 'TESTING_FINISHED'" class="next-step-banner ready">
                  <el-icon size="20" color="#409EFF"><InfoFilled /></el-icon>
                  <div class="next-step-text">
                    <p>样本外回测已完成，可查看 alphalens 报告或生成聚宽脚本。</p>
                  </div>
                </div>
              </div>

              <!-- 因子挖掘（step10） -->
              <div v-if="activeTab === 'training'" class="tab-content">
                <div class="exec-form">
                  <div class="form-group-header">
                    <h4>因子挖掘</h4>
                    <p class="group-desc">基于已配置的因子池、训练窗口运行因子挖掘流水线</p>
                  </div>
                  <div class="exec-row">
                    <el-button
                      v-if="task?.status === 'RUNNING' && task?.currentStep === '10'"
                      type="danger" size="large"
                      @click="handleStop"
                    >
                      <el-icon><VideoPause /></el-icon> 停止因子挖掘
                    </el-button>
                    <el-button
                      v-else
                      type="primary" size="large"
                      :loading="starting"
                      :disabled="task?.status === 'RUNNING' || !isConfigured"
                      @click="handleStartStep('10')"
                    >
                      <el-icon><VideoPlay /></el-icon>
                      {{ task?.status === 'TRAINING_FINISHED' || task?.status === 'TESTING_FINISHED' ? '重新挖掘' : '启动因子挖掘' }}
                    </el-button>
                    <span v-if="!isConfigured" class="step-hint">请先完成配置</span>
                  </div>
                </div>
                <div v-if="activeStep === '10' && (wsConnected || displayLogs.length)" class="progress-panel">
                  <div class="progress-title">后台执行进度</div>
                  <div class="progress-log-list">
                    <div v-for="(line, idx) in displayLogs" :key="idx" class="progress-log-line">{{ line }}</div>
                  </div>
                </div>

                <!-- step10 因子挖掘产物展示（editorial 风格） -->
                <TrainingArtifacts :artifacts="trainingArtifacts" />
              </div>

              <!-- 样本外回测（step11） -->
              <div v-if="activeTab === 'testing'" class="tab-content">
                <div class="exec-form">
                  <div class="form-group-header">
                    <h4>样本外回测</h4>
                    <p class="group-desc">基于因子挖掘产物在 OOS 区间运行回测</p>
                  </div>
                  <div class="exec-row">
                    <el-button
                      v-if="task?.status === 'RUNNING' && task?.currentStep === '11'"
                      type="danger" size="large"
                      @click="handleStop"
                    >
                      <el-icon><VideoPause /></el-icon> 停止样本外回测
                    </el-button>
                    <el-button
                      v-else
                      type="primary" size="large"
                      :loading="starting"
                      :disabled="!canRunStep('11')"
                      @click="handleStartStep('11')"
                    >
                      <el-icon><VideoPlay /></el-icon>
                      {{ task?.status === 'TESTING_FINISHED' ? '重新回测' : '启动样本外回测' }}
                    </el-button>
                  </div>
                  <div v-if="stepDisabledReason('11')" class="step-hint-row">
                    <div class="step-hint-line">
                      <span class="step-hint-tag">启动样本外回测</span>
                      <span class="step-hint">{{ stepDisabledReason('11') }}</span>
                    </div>
                  </div>
                </div>
                <div v-if="activeStep === '11' && (wsConnected || displayLogs.length)" class="progress-panel">
                  <div class="progress-title">后台执行进度</div>
                  <div class="progress-log-list">
                    <div v-for="(line, idx) in displayLogs" :key="idx" class="progress-log-line">{{ line }}</div>
                  </div>
                </div>

                <!-- step11 样本外回测产物展示（editorial 风格） -->
                <OosArtifacts :artifacts="oosArtifacts" />
              </div>

              <!-- alphalens 展示（step12 + step13 streamlit） -->
              <div v-if="activeTab === 'alphalens'" class="tab-content">
                <div class="exec-form">
                  <div class="form-group-header">
                    <h4>alphalens 展示</h4>
                    <p class="group-desc">生成 alphalens 报告，启动 Streamlit 交互式查看</p>
                  </div>
                  <div class="exec-row">
                    <el-button
                      v-if="task?.status === 'RUNNING' && task?.currentStep === '12'"
                      type="danger" size="large"
                      @click="handleStop"
                    >
                      <el-icon><VideoPause /></el-icon> 停止 step12
                    </el-button>
                    <el-button
                      v-else
                      type="primary" size="large"
                      :loading="starting"
                      :disabled="!canRunStep('12')"
                      @click="handleStartStep('12')"
                    >
                      <el-icon><VideoPlay /></el-icon> 生成 alphalens 报告
                    </el-button>
                    <el-button
                      v-if="task?.status === 'RUNNING' && task?.currentStep === '13'"
                      type="danger" size="large"
                      @click="handleStop"
                    >
                      <el-icon><VideoPause /></el-icon> 停止 Streamlit
                    </el-button>
                    <el-button
                      v-else
                      type="success" size="large" plain
                      :loading="starting"
                      :disabled="!canRunStep('13')"
                      @click="handleStartStep('13')"
                    >
                      <el-icon><VideoPlay /></el-icon> 启动 Streamlit
                    </el-button>
                  </div>
                  <div v-if="stepDisabledReason('12') || stepDisabledReason('13')" class="step-hint-row">
                    <div v-if="stepDisabledReason('12')" class="step-hint-line">
                      <span class="step-hint-tag">生成 alphalens 报告</span>
                      <span class="step-hint">{{ stepDisabledReason('12') }}</span>
                    </div>
                    <div v-if="stepDisabledReason('13')" class="step-hint-line">
                      <span class="step-hint-tag">启动 Streamlit</span>
                      <span class="step-hint">{{ stepDisabledReason('13') }}</span>
                    </div>
                  </div>
                </div>
                <div v-if="(activeStep === '12' || activeStep === '13') && (wsConnected || displayLogs.length)" class="progress-panel">
                  <div class="progress-title">后台执行进度</div>
                  <div class="progress-log-list">
                    <div v-for="(line, idx) in displayLogs" :key="idx" class="progress-log-line">{{ line }}</div>
                  </div>
                </div>
              </div>

              <!-- 聚宽脚本（step14） -->
              <div v-if="activeTab === 'joinquant'" class="tab-content">
                <div class="exec-form">
                  <div class="form-group-header">
                    <h4>聚宽脚本</h4>
                    <p class="group-desc">基于本地因子+回测配置生成聚宽脚本，可下载到聚宽平台运行</p>
                  </div>
                  <div class="exec-row">
                    <el-button
                      v-if="task?.status === 'RUNNING' && task?.currentStep === '14'"
                      type="danger" size="large"
                      @click="handleStop"
                    >
                      <el-icon><VideoPause /></el-icon> 停止 step14
                    </el-button>
                    <el-button
                      v-else
                      type="primary" size="large"
                      :loading="starting"
                      :disabled="!canRunStep('14')"
                      @click="handleStartStep('14')"
                    >
                      <el-icon><VideoPlay /></el-icon> 生成聚宽脚本
                    </el-button>
                  </div>
                  <div v-if="stepDisabledReason('14')" class="step-hint-row">
                    <div class="step-hint-line">
                      <span class="step-hint-tag">生成聚宽脚本</span>
                      <span class="step-hint">{{ stepDisabledReason('14') }}</span>
                    </div>
                  </div>
                </div>
                <div v-if="activeStep === '14' && (wsConnected || displayLogs.length)" class="progress-panel">
                  <div class="progress-title">后台执行进度</div>
                  <div class="progress-log-list">
                    <div v-for="(line, idx) in displayLogs" :key="idx" class="progress-log-line">{{ line }}</div>
                  </div>
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
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, VideoPlay, VideoPause, InfoFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getTask, startTask, stopTask, getStepProgress, getTrainingArtifacts, getOosArtifacts, type TaskResponse } from '../api/task'
import { getTaskSavedSections } from '../api/config'
import { formatDateTime } from '../utils/constants'
import AppLayout from '../components/AppLayout.vue'
import TrainingArtifacts from '../components/TrainingArtifacts.vue'
import OosArtifacts from '../components/OosArtifacts.vue'

const props = defineProps<{ taskId: number }>()
const router = useRouter()

const task = ref<TaskResponse | null>(null)
const loading = ref(false)
const activeTab = ref('overview')

// 必需的 10 个 section（selector 是可选的）。这 10 个 section 都存在于 task_config 表中，则视为配置完成
const REQUIRED_SECTIONS = [
  'feature_pool', 'stock_pool', 'tushare', 'market_context', 'label',
  'prescreen', 'score', 'backtest_rule', 'llm_params', 'analysis_rule',
]
const savedSections = ref<Set<string>>(new Set())

const isConfigured = computed(() => {
  return REQUIRED_SECTIONS.every(section => savedSections.value.has(section))
})

const tabs = [
  { key: 'overview', label: '任务概览' },
  { key: 'training', label: '因子挖掘' },
  { key: 'testing', label: '样本外回测' },
  { key: 'alphalens', label: 'alphalens 展示' },
  { key: 'joinquant', label: '聚宽脚本' },
]

const statusClass = computed(() => {
  const map: Record<string, string> = {
    NEW: 'sb-status-new',
    RUNNING: 'sb-status-running',
    TRAINING_FINISHED: 'sb-status-training-finished',
    TESTING_FINISHED: 'sb-status-testing-finished',
  }
  return map[task.value?.status || 'NEW']
})
const statusLabel = computed(() => {
  const map: Record<string, string> = {
    NEW: '新建',
    RUNNING: '运行中',
    TRAINING_FINISHED: '因子挖掘完成',
    TESTING_FINISHED: '回测完成',
  }
  return map[task.value?.status || 'NEW']
})

// step 的前置状态约束（与后端 ExecutionService 保持一致），同时结合实际产物（oosArtifacts.readiness）做更精细的判断
function canRunStep(step: string): boolean {
  const status = task.value?.status
  if (!status) return false
  if (status === 'RUNNING') return false
  if (step === '10') {
    return isConfigured.value
  }
  if (step === '11') {
    // 除了 status 要 >= TRAINING_FINISHED，还要求 step10 真的产出了任何可送 OOS 的 Top3 因子
    if (status !== 'TRAINING_FINISHED' && status !== 'TESTING_FINISHED') return false
    const oosReady = trainingArtifacts.value?.readiness?.oos_factors_ready
    return oosReady !== false  // 没有 readiness 字段时（向后兼容）按"允许"处理
  }
  // step12/13/14 需要 OOS 产物
  if (status !== 'TESTING_FINISHED') return false
  const readiness = oosArtifacts.value?.readiness || {}
  if (step === '12') return !!readiness.alphalens_report
  if (step === '13') return !!readiness.alphalens_dashboard
  if (step === '14') return !!readiness.joinquant_export
  return false
}

// 各 step 禁用时给出具体原因提示（用于按钮旁边的灰色文案）
function stepDisabledReason(step: string): string {
  const status = task.value?.status
  if (!status) return '任务状态未知'
  if (status === 'RUNNING') return '任务正在运行中'
  if (step === '10') {
    return isConfigured.value ? '' : '请先完成配置'
  }
  if (step === '11') {
    if (status === 'NEW') return '请先完成因子挖掘'
    if (status !== 'TRAINING_FINISHED' && status !== 'TESTING_FINISHED') {
      return '请先完成因子挖掘'
    }
    const oosReady = trainingArtifacts.value?.readiness?.oos_factors_ready
    if (oosReady === false) {
      return '因子挖掘没有产出任何可送样本外回测的 Top3 因子（跨窗口汇总与各窗口 validation Top3 均为空）'
    }
    return ''
  }
  if (status === 'NEW') return '请先完成因子挖掘'
  if (status === 'TRAINING_FINISHED') return '请先完成样本外回测'
  const readiness = oosArtifacts.value?.readiness || {}
  if (step === '12') {
    return readiness.alphalens_report
      ? ''
      : '样本外回测没有产出任何 Top3 因子，无法生成 alphalens 报告'
  }
  if (step === '13') {
    return readiness.alphalens_dashboard
      ? ''
      : '请先生成 alphalens 报告'
  }
  if (step === '14') {
    return readiness.joinquant_export
      ? ''
      : '样本外回测未产出可用的因子'
  }
  return ''
}

const starting = ref(false)

// 当前正在查看进度日志的 step（决定轮询哪个接口）。null 表示不轮询。
const activeStep = ref<string | null>(null)
const logLines = ref<string[]>([])
// 保留模板引用占位（模板中多个 v-for 用同名 ref）
const logContainerRef = ref<HTMLDivElement>()
// 兼容旧模板里的 wsConnected：保持为 true 时显示"WebSocket 已连接"标签
const wsConnected = ref(false)

// step10 因子挖掘产物（窗口起止时间 / 特征体检 / 市场环境 / 各 iter / discovery 候选 / validation / 跨窗口汇总）
const trainingArtifacts = ref<any>(null)
// step11 OOS 回测产物（输入因子 / 因子指标 / Top3 / 测试区间）
const oosArtifacts = ref<any>(null)
// active_context.json 的解析结果（由 step-progress 轮询同步），用于页头展示当前执行阶段
const activeContext = ref<any>(null)

// 当前执行阶段的展示数据，由 task.status + task.currentStep + activeContext.workflow_state 综合判定
const phaseDisplay = computed(() => {
  if (!task.value || task.value.status !== 'RUNNING') {
    return { kind: 'idle' as const }
  }
  const step = task.value.currentStep
  if (step === '11') {
    return { kind: 'oos' as const, text: 'OOS 回测中' }
  }
  if (step === '12' || step === '13') {
    return { kind: 'other' as const, text: 'Alphalens 处理中' }
  }
  if (step === '14') {
    return { kind: 'other' as const, text: '聚宽脚本生成中' }
  }
  // step10：根据 active_context.workflow_state 给出 window/stage/iter
  if (step === '10') {
    const ws = activeContext.value?.workflow_state
    const windowId = ws?.window_id
    const stage = ws?.stage
    if (!windowId || !stage) {
      return { kind: 'prep' as const, text: '前期数据准备' }
    }
    if (stage === 'discovery') {
      return {
        kind: 'discovery' as const,
        windowId,
        iteration: ws?.iteration ?? '?',
      }
    }
    if (stage === 'validation') {
      return { kind: 'validation' as const, windowId }
    }
    return { kind: 'other' as const, text: `${windowId} · ${stage.toUpperCase()}` }
  }
  return { kind: 'other' as const, text: step ? `Step ${step}` : '—' }
})

// 进度日志折叠（与 SelectorPanel 同样规则）
const MAX_DISPLAY_LINES = 200

function extractProgressKey(line: string): string | null {
  const stripped = line.replace(/^\s+/, '')
  let m = stripped.match(/^(\[SQLite\]\s+\S+)/)
  if (m) return `sqlite:${m[1]}`
  m = stripped.match(/^\[SQLite\]\s+从\s+Tushare\s+获取缺失的\s+(\S+)\s+数据/)
  if (m) return `fetch:${m[1]}`
  m = stripped.match(/^(审计\s+\S+)\s+[█░]/)
  if (m) return `audit:${m[1]}`
  if (/进度[:：]\s*\d+\s*\/\s*\d+/.test(stripped)) return 'aggregate_progress'
  if (/[█░]+/.test(stripped) && /\d+\s*\/\s*\d+\s*\(\d+%\)/.test(stripped)) {
    const head = stripped.split(/[█░]/, 1)[0].trim()
    return `bar:${head}`
  }
  return null
}

const displayLogs = computed<string[]>(() => {
  const lines = logLines.value
  const result: string[] = []
  const keyToIndex = new Map<string, number>()
  for (const line of lines) {
    const key = extractProgressKey(line)
    if (key !== null) {
      const existed = keyToIndex.get(key)
      if (existed !== undefined) {
        result[existed] = line
        continue
      }
      keyToIndex.set(key, result.length)
      result.push(line)
    } else {
      result.push(line)
    }
  }
  if (result.length > MAX_DISPLAY_LINES) {
    return result.slice(result.length - MAX_DISPLAY_LINES)
  }
  return result
})

async function fetchTask() {
  try {
    const res = await getTask(props.taskId)
    task.value = res.data
    try {
      const savedRes = await getTaskSavedSections(props.taskId)
      const list = (savedRes as any)?.data || []
      savedSections.value = new Set<string>(list)
    } catch {
      // 忽略配置查询错误
    }
    // 总是同步 active_context（保证用户在任意 tab 都能看到当前阶段，
    // 而不是只有进入"因子挖掘"tab 才更新）
    if (task.value?.status === 'RUNNING' && task.value.currentStep) {
      try {
        const ctxRes = await getStepProgress(props.taskId, task.value.currentStep, 0)
        activeContext.value = (ctxRes as any).data?.active_context || null
      } catch { /* ignore */ }
    } else {
      activeContext.value = null
    }
  } catch { /* ignore */ }
}

function goToConfig() {
  router.push({ name: 'TaskConfig', params: { taskId: props.taskId } })
}

// 通过 HTTP 轮询接口拉取指定 step 的实时进度日志（与"训练窗口推荐"同样的技术路线）
async function pollStepLogs() {
  if (!activeStep.value) return
  try {
    const res = await getStepProgress(props.taskId, activeStep.value)
    const data = res.data
    logLines.value = data.progress_logs || []
    wsConnected.value = !!data.running
    // 同步任务状态（结束时及时切到 TRAINING_FINISHED / TESTING_FINISHED）
    if (task.value) {
      task.value = { ...task.value, status: data.status as any, currentStep: data.currentStep || null }
    }
    // 同步 active_context（页头"阶段"信息源）
    activeContext.value = data.active_context || null
  } catch { /* ignore */ }
}

// 拉取因子挖掘 step10 的结构化产物（任务运行中/结束后都可看）
async function fetchTrainingArtifacts() {
  try {
    const res = await getTrainingArtifacts(props.taskId)
    trainingArtifacts.value = res.data
  } catch { /* ignore */ }
}

// 拉取样本外回测 step11 的结构化产物
async function fetchOosArtifacts() {
  try {
    const res = await getOosArtifacts(props.taskId)
    oosArtifacts.value = res.data
  } catch { /* ignore */ }
}

// 切换 tab → 决定要轮询哪个 step（也用来在打开任务时即可看到上一次执行的日志）
function setActiveStepByTab(tab: string) {
  const map: Record<string, string | null> = {
    overview: null,
    training: '10',
    testing: '11',
    alphalens: '12',
    joinquant: '14',
  }
  activeStep.value = map[tab] ?? null
}

async function handleStartStep(step: string) {
  starting.value = true
  try {
    logLines.value = []
    await startTask(props.taskId, { step })
    ElMessage.success('任务已启动')
    activeStep.value = step
    await fetchTask()
    await pollStepLogs()
    if (step === '10') {
      await fetchTrainingArtifacts()
    }
    if (step === '11') {
      await fetchOosArtifacts()
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || '启动失败')
  } finally {
    starting.value = false
  }
}

async function handleStop() {
  try {
    await stopTask(props.taskId)
    ElMessage.success('已发送停止信号')
    await fetchTask()
    wsConnected.value = false
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || '停止失败')
  }
}

// 切换到执行型 TAB 时立刻拉一次日志，并切换轮询目标
watch(activeTab, (tab) => {
  setActiveStepByTab(tab)
  if (activeStep.value) {
    pollStepLogs()
  } else {
    logLines.value = []
  }
  // 切到因子挖掘 / 样本外回测 tab 时，都需要 training-artifacts.readiness 判断 step11 是否可启动
  if (tab === 'training' || tab === 'testing') {
    fetchTrainingArtifacts()
  }
  // 切到样本外回测 / alphalens / 聚宽脚本时都需要 OOS readiness（决定下游按钮 enable/disable + 文案）
  if (tab === 'testing' || tab === 'alphalens' || tab === 'joinquant') {
    fetchOosArtifacts()
  }
}, { immediate: false })

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  await fetchTask()
  setActiveStepByTab(activeTab.value)
  if (activeStep.value) {
    await pollStepLogs()
  }
  // 初始进入页面时也拉一次（页签默认是 overview，但产物先拉好，等用户切到训练 tab 即可看到）
  if (activeTab.value === 'training' || activeTab.value === 'testing') {
    await fetchTrainingArtifacts()
  }
  if (activeTab.value === 'testing' || activeTab.value === 'alphalens' || activeTab.value === 'joinquant') {
    await fetchOosArtifacts()
  }
  pollTimer = setInterval(async () => {
    await fetchTask()
    if (activeStep.value) {
      await pollStepLogs()
    }
    // 训练 / 样本外回测 tab 激活时同步刷新挖掘产物（用于 readiness）
    if (activeTab.value === 'training' || activeTab.value === 'testing') {
      await fetchTrainingArtifacts()
    }
    // OOS 相关 tab 激活时同步刷新 OOS 产物及 readiness
    if (activeTab.value === 'testing' || activeTab.value === 'alphalens' || activeTab.value === 'joinquant') {
      await fetchOosArtifacts()
    }
  }, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 页头紧凑信息条：状态 / Step / PID / 创建时间 */
.header-row {
  display: flex;
  align-items: center;
  gap: 24px;
}
.header-meta-strip {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 14px;
  background: #fafbfc;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
}
.meta-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.meta-key {
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.meta-value {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}
.meta-value.mono {
  font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
  font-feature-settings: "tnum";
}
.meta-divider {
  width: 1px;
  height: 18px;
  background: #cbd5e1;
}

/* 当前执行阶段的小标签 */
.phase-item {
  gap: 6px;
  flex-wrap: nowrap;
}
.phase-window {
  font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
  font-feature-settings: "tnum";
}
.phase-sep {
  color: #cbd5e1;
  font-weight: 700;
}
.phase-iter {
  font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
  font-size: 12px;
  color: #475569;
  font-feature-settings: "tnum";
}
.phase-chip {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding: 2px 7px;
  border-radius: 4px;
  line-height: 1.5;
}
.phase-chip.phase-discovery {
  background: #dbeafe;
  color: #1d4ed8;
}
.phase-chip.phase-validation {
  background: #dcfce7;
  color: #15803d;
}
.phase-chip.phase-prep {
  background: #f1f5f9;
  color: #475569;
  letter-spacing: normal;
}
.phase-chip.phase-oos {
  background: #fef3c7;
  color: #b45309;
  letter-spacing: normal;
}
.phase-chip.phase-other {
  background: #f1f5f9;
  color: #475569;
  letter-spacing: normal;
}
.header-actions {
  /* 已经被 meta-strip 推到右侧，actions 紧贴 strip 右边即可 */
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

/* 已配置完成的提示：使用低饱和的中性配色，避免与"未配置"红色提示混淆 */
.next-step-banner.ready {
  background: #f0f9ff;
  border-color: #cfe7f5;
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

.step-log-panel {
  margin-top: 24px;
  border: 1px solid var(--sb-border-light);
  border-radius: var(--sb-radius);
  overflow: hidden;
}

.step-log-panel .log-container {
  height: 360px;
}

.log-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--sb-text);
}

/* 与 SelectorPanel 对齐的"后台执行进度"面板（浅色风格） */
.progress-panel {
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  padding: 14px 16px;
  margin-top: 16px;
  background: #fafafa;
}

.progress-title {
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
}

.progress-log-list {
  max-height: 360px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.7;
  color: #606266;
}

.progress-log-line {
  white-space: pre-wrap;
}

.step-hint {
  margin-left: 12px;
  color: var(--sb-text-muted);
  font-size: 13px;
}

/* 多个 step 的禁用原因列表 */
.step-hint-row {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.step-hint-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.step-hint-line .step-hint {
  margin-left: 0;
}
.step-hint-tag {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  background: #fef3c7;
  color: #92400e;
  letter-spacing: 0.02em;
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
