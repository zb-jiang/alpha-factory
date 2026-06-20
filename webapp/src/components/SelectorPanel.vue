<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Aim, Loading, Check, CircleCheck } from '@element-plus/icons-vue'
import { runSelector, getSelectorResult, applySelectorResult, updateTaskConfigTab } from '../api/config'

interface WindowItem {
  start: string
  end: string
  score: number
  span: number
  isBest: boolean
}

const props = defineProps<{
  taskId: number
  mode: string
  configValues?: Record<string, any>
}>()

const emit = defineEmits<{
  (e: 'applied'): void
  (e: 'apply-window', payload: { start: string; end: string; span: number }): void
}>()

const running = ref(false)
const applying = ref(false)
const pollingTimer = ref<number | null>(null)
const showProgressLogs = ref(false)

const result = ref<{
  ready: boolean
  error?: string
  recommended_train_window?: { start: string; end: string; recommend_span_months: number }
  span_window?: Record<string, { start: string; end: string; score: number }[]>
  [key: string]: any
} | null>(null)

const selected = ref<{ start: string; end: string; span: number } | null>(null)

const hasResult = computed(() => result.value?.ready === true)
const progressLogs = computed<string[]>(() => result.value?.progress_logs || [])

const spanWindows = computed(() => {
  const map: Record<string, { start: string; end: string; score: number; isBest: boolean }[]> = {}
  const raw = result.value?.span_window
  if (!raw) return map
  for (const [span, list] of Object.entries(raw)) {
    map[span] = (list || []).map((w: any) => ({
      start: w.start,
      end: w.end,
      score: w.score,
      isBest: bestWindow.value?.start === w.start && bestWindow.value?.end === w.end,
    }))
  }
  return map
})

const bestWindow = computed<WindowItem | null>(() => {
  const raw = result.value?.span_window
  if (!raw) return null
  let best: WindowItem | null = null
  for (const [span, list] of Object.entries(raw)) {
    for (const w of list || []) {
      if (!best || w.score > best.score) {
        best = { start: w.start, end: w.end, score: w.score, span: Number(span), isBest: true }
      }
    }
  }
  return best
})

function formatScore(score: number) {
  return ((Number(score) || 0) * 100).toFixed(1) + '%'
}

function getScoreColor(score: number) {
  if (score >= 0.9) return '#67C23A'
  if (score >= 0.7) return '#E6A23C'
  return '#F56C6C'
}

function selectWindow(win: any, span: number) {
  selected.value = { start: win.start, end: win.end, span }
}

async function pollResult() {
  try {
    const res = await getSelectorResult(props.taskId)
    result.value = res.data
    running.value = !!res.data.running
    if (res.data.running) {
      showProgressLogs.value = true
    }
    if (res.data.ready) {
      stopPolling()
      running.value = false
      const best = bestWindow.value
      if (best) {
        selected.value = { start: best.start, end: best.end, span: best.span }
      }
    } else if (res.data.running && !pollingTimer.value) {
      startPolling()
    }
  } catch {
    // ignore polling errors
  }
}

function startPolling() {
  stopPolling()
  pollingTimer.value = window.setInterval(pollResult, 3000)
}

function stopPolling() {
  if (pollingTimer.value) {
    clearInterval(pollingTimer.value)
    pollingTimer.value = null
  }
}

async function onRunSelector() {
  try {
    // 校验 Test 时间窗口必填
    const testStart = props.configValues?.test_start_date
    const testEnd = props.configValues?.test_end_date
    if (!testStart || !testEnd) {
      ElMessage.error('请先填写 Test 开始日期和 Test 结束日期')
      return
    }
    if (new Date(testEnd) < new Date(testStart)) {
      ElMessage.error('Test 结束日期不能早于 Test 开始日期')
      return
    }
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    if (new Date(testEnd) > today) {
      ElMessage.error('Test 结束日期不能超过今天')
      return
    }

    running.value = true
    showProgressLogs.value = true
    result.value = null
    selected.value = null

    // 先保存当前 selector 配置到数据库，确保 YAML 生成有数据
    if (props.configValues && Object.keys(props.configValues).length > 0) {
      const {
        test_start_date,
        test_end_date,
        iteration_count,
        train_window_source,
        manual_train_start_date,
        manual_train_end_date,
        ...rest
      } = props.configValues
      // training_workflow.* 也不属于 selector 算法配置，需要排除
      const selectorValues: Record<string, any> = {}
      for (const key of Object.keys(rest)) {
        if (!key.startsWith('training_workflow.')) {
          selectorValues[key] = rest[key]
        }
      }
      await updateTaskConfigTab(props.taskId, 'selector', selectorValues)
      // test_start_date / test_end_date / iteration_count 应同步到 analysis_rule
      const analysisPatch: Record<string, any> = {}
      if (test_start_date !== undefined) analysisPatch.test_start_date = test_start_date
      if (test_end_date !== undefined) analysisPatch.test_end_date = test_end_date
      if (iteration_count !== undefined) analysisPatch.iteration_count = iteration_count
      if (Object.keys(analysisPatch).length > 0) {
        await updateTaskConfigTab(props.taskId, 'analysis_rule', analysisPatch)
      }
    }

    await runSelector(props.taskId)
    startPolling()
    ElMessage.success('训练窗口筛选已启动')
  } catch (e: any) {
    running.value = false
    ElMessage.error(e?.response?.data?.message || '启动失败')
  }
}

async function onApply() {
  if (!selected.value) return
  // 仅把推荐窗口填入训练窗口输入框，统一由父组件“保存”按钮落库
  emit('apply-window', {
    start: selected.value.start,
    end: selected.value.end,
    span: selected.value.span,
  })
  ElMessage.success('已填入训练窗口，请记得点击页面下方的“保存”')
}

onMounted(() => {
  pollResult()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div class="selector-panel">
      <div class="action-bar">
        <el-button type="primary" size="large" :loading="running" @click="onRunSelector">
          <el-icon class="btn-icon"><Aim /></el-icon>
          {{ hasResult ? '重新推荐' : '启动推荐' }}
        </el-button>
        <el-text v-if="running" type="info" class="running-hint">
          <el-icon class="is-loading"><Loading /></el-icon>
          后台正在根据 Test 时间窗口寻找相似历史训练窗口…
        </el-text>
      </div>

      <div v-if="showProgressLogs && (running || progressLogs.length)" class="progress-panel">
        <div class="progress-title">后台执行进度</div>
        <div class="progress-log-list">
          <div v-for="(line, idx) in progressLogs" :key="idx" class="progress-log-line">{{ line }}</div>
        </div>
      </div>

      <div v-if="result?.test_summary" class="summary-panel">
        <div class="summary-title">Test 窗口市场状态</div>
        <div class="summary-text">{{ result.test_summary }}</div>
      </div>

      <div v-if="result?.ready && !running" class="result-area">
      <el-alert
        v-if="bestWindow"
        :title="`全局最高评分：${bestWindow.start} ~ ${bestWindow.end}（${bestWindow.span} 个月，相似度 ${formatScore(bestWindow.score)}）`"
        type="success"
        :closable="false"
        show-icon
        class="best-alert"
      />

      <div class="span-grid">
        <el-card
          v-for="(windows, span) in spanWindows"
          :key="span"
          class="span-card"
          :class="{ 'has-best': windows.some(w => w.isBest) }"
          shadow="hover"
        >
          <template #header>
            <div class="card-header">
              <span class="span-title">{{ span }} 个月窗口</span>
              <el-tag v-if="windows.some(w => w.isBest)" type="success" effect="dark" size="small">全局最优</el-tag>
            </div>
          </template>

          <div class="window-list">
            <div
              v-for="(win, idx) in windows"
              :key="idx"
              class="window-item"
              :class="{
                'is-best': win.isBest,
                'is-selected': selected?.start === win.start && selected?.end === win.end && selected?.span === Number(span)
              }"
              @click="selectWindow(win, Number(span))"
            >
              <div class="rank-badge" :class="{ 'rank-1': idx === 0, 'rank-2': idx === 1, 'rank-3': idx === 2 }">
                {{ idx + 1 }}
              </div>
              <div class="window-info">
                <div class="window-date">{{ win.start }} ~ {{ win.end }}</div>
                <div class="window-score">
                  <el-progress
                    :percentage="Math.min(Math.round(win.score * 100), 100)"
                    :color="getScoreColor(win.score)"
                    :stroke-width="6"
                    :show-text="false"
                  />
                  <span class="score-text">{{ formatScore(win.score) }}</span>
                </div>
              </div>
              <el-radio
                :model-value="selected?.start === win.start && selected?.end === win.end && selected?.span === Number(span)"
                :label="true"
                class="window-radio"
              />
            </div>
          </div>
        </el-card>
      </div>

      <div v-if="selected" class="apply-bar">
        <el-divider />
        <div class="selected-summary">
          <el-icon color="#67C23A"><Check /></el-icon>
          <span>已选择：<strong>{{ selected.start }} ~ {{ selected.end }}</strong>（{{ selected.span }} 个月）</span>
        </div>
        <el-button type="success" size="large" :loading="applying" @click="onApply">
          <el-icon><CircleCheck /></el-icon>
          应用此窗口（填入训练窗口）
        </el-button>
      </div>
    </div>

      <el-empty
        v-else-if="!running && !result?.ready"
        description="尚未计算训练窗口推荐结果，请点击上方按钮启动推荐"
        :image-size="120"
      />
  </div>
</template>

<style scoped>
.selector-panel {
  padding: 16px 0;
}

.progress-panel,
.summary-panel {
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 16px;
  background: #fafafa;
}

.progress-title,
.summary-title {
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
}

.progress-log-list {
  max-height: 260px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.7;
  color: #606266;
}

.progress-log-line {
  white-space: pre-wrap;
}

.summary-text {
  font-size: 13px;
  line-height: 1.8;
  color: #606266;
}

.action-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.btn-icon {
  margin-right: 6px;
}

.running-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.best-alert {
  margin-bottom: 20px;
}

.span-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.span-card {
  border-radius: 12px;
  transition: all 0.3s;
}

.span-card.has-best {
  border-color: #67C23A;
  box-shadow: 0 0 0 1px #67C23A40;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.span-title {
  font-weight: 600;
  font-size: 15px;
}

.window-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.window-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  cursor: pointer;
  transition: all 0.2s;
  background: #fafafa;
}

.window-item:hover {
  background: #f0f9ff;
  border-color: #a0cfff;
}

.window-item.is-best {
  background: #f0f9eb;
  border-color: #b3e19d;
}

.window-item.is-selected {
  background: #ecf5ff;
  border-color: #409eff;
  box-shadow: 0 0 0 2px #409eff20;
}

.rank-badge {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 13px;
  color: #606266;
  background: #e4e7ed;
  flex-shrink: 0;
}

.rank-badge.rank-1 {
  background: #fde2e2;
  color: #c45656;
}

.rank-badge.rank-2 {
  background: #faecd8;
  color: #a16207;
}

.rank-badge.rank-3 {
  background: #e1e3e9;
  color: #4b5563;
}

.window-info {
  flex: 1;
  min-width: 0;
}

.window-date {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 6px;
}

.window-score {
  display: flex;
  align-items: center;
  gap: 10px;
}

.window-score :deep(.el-progress) {
  flex: 1;
}

.score-text {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  width: 52px;
  text-align: right;
  flex-shrink: 0;
}

.window-radio {
  flex-shrink: 0;
  margin: 0;
}

.apply-bar {
  margin-top: 24px;
  text-align: center;
}

.selected-summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  font-size: 15px;
  color: #303133;
}
</style>
