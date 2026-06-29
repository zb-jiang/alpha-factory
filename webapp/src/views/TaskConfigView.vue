<template>
  <AppLayout>
    <div class="task-config-page" v-loading="loading">
      <!-- Page Header -->
      <div class="sb-page-header">
        <div class="header-row">
          <div class="header-left">
            <el-button text @click="router.push('/')" class="back-btn">
              <el-icon><ArrowLeft /></el-icon>
            </el-button>
            <div>
              <h1>{{ taskName }}</h1>
              <div class="header-meta">
                <el-tag v-if="task" :class="statusType" size="small" effect="light">{{ statusLabel }}</el-tag>
                <span class="header-desc">任务配置</span>
              </div>
            </div>
          </div>
          <el-tag v-if="!isEditable" type="info" size="large" effect="light">任务运行中，配置不可修改</el-tag>
        </div>
      </div>

      <!-- Content -->
      <div class="config-body">
        <!-- Sidebar Tabs -->
        <div class="config-tabs">
          <div
            v-for="(tab, index) in tabs"
            :key="tab.section"
            :class="['tab-item', { active: activeTab === tab.section, disabled: isTabDisabled(index) }]"
            @click="!isTabDisabled(index) && switchTab(tab.section)"
          >
            <el-icon size="18">
              <component :is="tabIcon(tab.sectionIcon)" />
            </el-icon>
            <div class="tab-info">
              <span class="tab-label">
                <span class="tab-badge">{{ index + 1 }}</span>
                {{ tab.sectionLabel }}
              </span>
              <span class="tab-desc">{{ tab.sectionDescription }}</span>
            </div>
            <span v-if="hasChanges(tab.section)" class="tab-dot"></span>
          </div>
        </div>

        <!-- Main Form -->
        <div class="config-form" v-if="currentTabData">
          <div class="form-title">
            <h2>{{ currentTabData.sectionLabel }}</h2>
            <p>{{ currentTabData.sectionDescription }}</p>
          </div>

          <div class="groups-container">
            <template v-for="group in currentTabData.groups" :key="group.name">
              <!-- 阶段分隔标题（fields为空时） -->
              <div v-if="!group.fields || group.fields.length === 0" class="stage-divider">
                <h3 class="stage-title">{{ group.label }}</h3>
              </div>
              <!-- 普通配置组 -->
              <div v-else-if="isGroupVisible(group)" class="form-group">
                <div class="group-header" v-if="group.label">
                  <div class="group-title">
                    <el-icon size="16" v-if="group.icon">
                      <component :is="group.icon" />
                    </el-icon>
                    <h4>{{ group.label }}</h4>
                    <!-- Agent 配置组显示链接测试按钮 -->
                    <el-button
                      v-if="group.name.startsWith('agent_')"
                      size="small"
                      :loading="testingAgent === group.name"
                      :type="testResults[group.name]?.success === true ? 'success' : testResults[group.name]?.success === false ? 'danger' : 'default'"
                      @click="testAgentConnection(group.name)"
                      class="test-btn"
                    >
                      {{ testResults[group.name] ? (testResults[group.name].success ? '连接成功' : '连接失败') : '链接测试' }}
                    </el-button>
                  </div>
                  <p class="group-desc">{{ group.description }}</p>
                  <!-- 测试结果消息 -->
                  <el-alert
                    v-if="testResults[group.name]"
                    :title="testResults[group.name].message"
                    :type="testResults[group.name].success ? 'success' : 'error'"
                    :closable="true"
                    show-icon
                    class="test-result-alert"
                    @close="delete testResults[group.name]"
                  />
                </div>

                <div class="fields-grid">
                  <div
                    v-for="field in group.fields"
                    :key="field.key"
                    :class="['field-item', { 'field-full': isFullWidth(field.type) || isFullWidthKey(field.key) }]"
                    v-show="isFieldVisible(field)"
                  >
                  <div class="field-label-row" v-if="field.label">
                    <label class="field-label">{{ field.label }}</label>
                    <span v-if="field.required" class="required-mark">*</span>
                    <el-tag v-if="field.source === 'global'" size="small" type="warning" effect="light" class="source-tag">全局</el-tag>
                    <el-tag v-else-if="field.source === 'user'" size="small" type="success" effect="light" class="source-tag">个人</el-tag>
                  </div>
                  <div class="field-control">
                    <!-- 特征池预览 -->
                    <div v-if="field.type === 'feature_pool_preview'" class="feature-pool-preview">
                      <FeaturePoolPreview :data="formValues[field.key]" />
                    </div>
                    <!-- 开关 -->
                    <el-switch
                      v-else-if="field.type === 'switch'"
                      v-model="formValues[field.key]"
                      :disabled="!isEditable || field.readonly || isFieldDisabled(field)"
                    />
                    <!-- 数字输入 -->
                    <el-input-number
                      v-else-if="field.type === 'number'"
                      v-model="formValues[field.key]"
                      :min="field.min ?? -Infinity"
                      :max="field.max ?? Infinity"
                      :step="field.step || 1"
                      :precision="field.precision"
                      :disabled="!isEditable || isFieldDisabled(field)"
                      controls-position="right"
                      size="large"
                      style="width: 100%"
                    />
                    <!-- 滑块 -->
                    <div v-else-if="field.type === 'slider'" class="slider-wrapper">
                      <el-slider
                        v-model="formValues[field.key]"
                        :min="field.min ?? 0"
                        :max="field.max ?? 1"
                        :step="field.step ?? 0.1"
                        :disabled="!isEditable || isFieldDisabled(field)"
                        show-input
                        input-size="small"
                      />
                    </div>
                    <!-- 下拉选择 -->
                    <el-select
                      v-else-if="field.type === 'select'"
                      v-model="formValues[field.key]"
                      :disabled="!isEditable || isFieldDisabled(field)"
                      size="large"
                      style="width: 100%"
                      @change="handleSelectChange(field, $event)"
                    >
                      <el-option
                        v-for="opt in getFilteredOptions(field)"
                        :key="opt.value"
                        :label="opt.label"
                        :value="opt.value"
                      />
                    </el-select>
                    <!-- 密码 -->
                    <el-input
                      v-else-if="field.type === 'password'"
                      v-model="formValues[field.key]"
                      type="password"
                      show-password
                      :placeholder="field.placeholder"
                      :disabled="!isEditable"
                      size="large"
                    />
                    <!-- 标签多选 -->
                    <div v-else-if="field.type === 'tag_select'" class="tag-select-wrapper">
                      <el-checkbox-group v-model="formValues[field.key]" :disabled="!isEditable || isFieldDisabled(field)">
                        <el-checkbox
                          v-for="opt in field.options"
                          :key="opt.value"
                          :label="opt.label"
                          :value="Number(opt.value)"
                          border
                        />
                      </el-checkbox-group>
                    </div>
                    <!-- 日期 -->
                    <el-date-picker
                      v-else-if="field.type === 'date'"
                      v-model="formValues[field.key]"
                      type="date"
                      value-format="YYYY-MM-DD"
                      :disabled="!isEditable || isFieldDisabled(field)"
                      size="large"
                      style="width: 100%"
                    />
                    <!-- 多行文本 -->
                    <el-input
                      v-else-if="field.type === 'textarea'"
                      v-model="formValues[field.key]"
                      type="textarea"
                      :rows="4"
                      :placeholder="field.placeholder"
                      :disabled="!isEditable || isFieldDisabled(field)"
                      size="large"
                      style="width: 100%"
                    />
                    <!-- 文本 -->
                    <el-input
                      v-else
                      v-model="formValues[field.key]"
                      :placeholder="field.placeholder"
                      :disabled="!isEditable || field.readonly || isFieldDisabled(field)"
                      size="large"
                    />
                  </div>
                  <div class="field-hint" v-if="field.description">{{ field.description }}</div>
                </div>
              </div>
            </div>
            <!-- 在“训练窗口选择器”组渲染完后，紧接着插入启动推荐+推荐结果面板 -->
            <SelectorPanel
              v-if="group.name === 'selector' && activeTab === 'selector' && taskId && formValues['train_window_source'] === 'recommend'"
              :task-id="taskId"
              :mode="formValues['training_workflow.mode'] || 'static_split'"
              :config-values="formValues"
              class="selector-panel-wrapper"
              @applied="onSelectorApplied"
              @apply-window="onApplySelectorWindow"
            />
            </template>
          </div>

          <!-- Tab 级保存/重置按钮 -->
          <div v-if="isEditable && currentTabData" class="tab-actions">
            <el-button @click="resetTab" size="large">重置</el-button>
            <el-button type="primary" size="large" :loading="saving" @click="saveTab">
              <el-icon><Check /></el-icon> 保存
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, markRaw } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  ArrowLeft, Check, DataAnalysis, ChatDotRound, TrendCharts, Sunrise, Aim, Filter, Coin, Timer, Flag, UserFilled, DataLine, ScaleToOriginal,
  Calendar, Operation, Money, MagicStick, Top, FirstAidKit, DataBoard, Scissor,
  RefreshLeft, Lightning, Histogram, View, Discount, Medal, Wallet,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getTask, type TaskResponse } from '../api/task'
import {
  getTaskConfigTabs, getTaskConfigTab, updateTaskConfigTab, testLlmConnection,
  type StructuredConfigResponse, type ConfigField, type SelectOption,
} from '../api/config'
import { getSystemConfig } from '../api/config'
import AppLayout from '../components/AppLayout.vue'
import FeaturePoolPreview from '../components/FeaturePoolPreview.vue'
import SelectorPanel from '../components/SelectorPanel.vue'

const ICON_MAP: Record<string, any> = {
  DataAnalysis: markRaw(DataAnalysis),
  ChatDotRound: markRaw(ChatDotRound),
  TrendCharts: markRaw(TrendCharts),
  Sunrise: markRaw(Sunrise),
  Aim: markRaw(Aim),
  Filter: markRaw(Filter),
  Coin: markRaw(Coin),
  Timer: markRaw(Timer),
  Flag: markRaw(Flag),
  UserFilled: markRaw(UserFilled),
  DataLine: markRaw(DataLine),
  ScaleToOriginal: markRaw(ScaleToOriginal),
  Calendar: markRaw(Calendar),
  Operation: markRaw(Operation),
  Money: markRaw(Money),
  MagicStick: markRaw(MagicStick),
  Top: markRaw(Top),
  FirstAidKit: markRaw(FirstAidKit),
  DataBoard: markRaw(DataBoard),
  Scissor: markRaw(Scissor),
  RefreshLeft: markRaw(RefreshLeft),
  Lightning: markRaw(Lightning),
  Histogram: markRaw(Histogram),
  View: markRaw(View),
  Discount: markRaw(Discount),
  Medal: markRaw(Medal),
  Wallet: markRaw(Wallet),
}

const router = useRouter()
const route = useRoute()
const taskId = computed(() => Number(route.params.taskId))

const loading = ref(false)
const saving = ref(false)
const task = ref<TaskResponse | null>(null)
const testingAgent = ref<string | null>(null)
const testResults = ref<Record<string, { success: boolean; message: string }>>({})
const taskName = computed(() => task.value?.taskName || '加载中...')
const statusType = computed(() => {
  const map: Record<string, string> = { NEW: 'sb-status-new', RUNNING: 'sb-status-running', STOPPED: 'sb-status-stopped', COMPLETED: 'sb-status-completed', ERROR: 'sb-status-error' }
  return map[task.value?.status || 'NEW']
})
const statusLabel = computed(() => {
  const map: Record<string, string> = { NEW: '新建', RUNNING: '运行中', STOPPED: '已终止', COMPLETED: '已完成', ERROR: '错误' }
  return map[task.value?.status || 'NEW']
})
const isEditable = computed(() => task.value?.status !== 'RUNNING')

const tabs = ref<StructuredConfigResponse[]>([])
const activeTab = ref('')
const currentTabData = ref<StructuredConfigResponse | null>(null)
const formValues = ref<Record<string, any>>({})
const originalValues = ref<Record<string, any>>({})
const changedTabs = ref<Set<string>>(new Set())
const providerUrlMap = ref<Record<string, string>>({})

function tabIcon(iconName?: string) {
  return ICON_MAP[iconName || ''] || markRaw(DataAnalysis)
}

function isFullWidth(type: string) {
  return ['slider', 'tag_select', 'feature_pool_preview'].includes(type)
}

// 这些 key 单独占满整行，避免破坏后续字段两两成行的显示
const FULL_WIDTH_KEYS = new Set([
  'training_workflow.mode',
])

function isFullWidthKey(key: string) {
  return FULL_WIDTH_KEYS.has(key)
}

function isFieldVisible(field: ConfigField) {
  function check(key?: string, value?: any): boolean {
    if (!key) return true
    const val = formValues.value[key]
    if (Array.isArray(value)) {
      return value.includes(val)
    }
    return val === value
  }
  return check(field.showWhenKey, field.showWhenValue) && check(field.showWhenKey2, field.showWhenValue2)
}

function isFieldDisabled(field: ConfigField): boolean {
  function check(key?: string, value?: any): boolean {
    if (!key) return false
    const val = formValues.value[key]
    if (Array.isArray(value)) {
      return value.includes(val)
    }
    return val === value
  }
  return check(field.disableWhenKey, field.disableWhenValue)
}

function isGroupVisible(group: ConfigGroup) {
  return !group.fields || group.fields.length === 0 || group.fields.some(isFieldVisible)
}

function getFilteredOptions(field: ConfigField): SelectOption[] {
  if (!field.options) return []
  if (!field.optionFilterKey || !field.optionFilterMap) return field.options
  const filterVal = formValues.value[field.optionFilterKey]
  const allowed = field.optionFilterMap[filterVal]
  if (!allowed) return field.options
  return field.options.filter(opt => allowed.includes(String(opt.value)))
}

function hasChanges(section: string) {
  return changedTabs.value.has(section)
}

function isTabDisabled(index: number): boolean {
  // 第一个 Tab 始终可点击
  if (index === 0) return false
  // 如果前面有任何 Tab 未保存过，则当前 Tab 禁用
  for (let i = 0; i < index; i++) {
    if (!tabs.value[i]?.saved) {
      return true
    }
  }
  return false
}

function handleSelectChange(field: ConfigField, value: any) {
  // 当选择供应商时，自动带出 base_url
  if (field.key.endsWith('.llm_provider') || field.key === 'llm_provider') {
    const urlKey = field.key.replace(/llm_provider$/, 'llm_base_url')
    const url = providerUrlMap.value[value] || ''
    if (url) {
      formValues.value[urlKey] = url
    }
  }
  // 调仓频率与调仓锚点联动
  if (field.key === 'rebalance') {
    const anchor = formValues.value['rebalance_anchor']
    if (value === 'weekly') {
      const monthlyAnchors = ['first_trading_day_of_month', 'last_trading_day_of_month']
      if (monthlyAnchors.includes(anchor)) {
        formValues.value['rebalance_anchor'] = 'first_trading_day_of_week'
      }
    } else if (value === 'monthly') {
      const weeklyAnchors = ['first_trading_day_of_week', 'last_trading_day_of_week']
      if (weeklyAnchors.includes(anchor)) {
        formValues.value['rebalance_anchor'] = 'first_trading_day_of_month'
      }
    }
  }
}

async function loadProviderUrlMap() {
  try {
    // 从系统配置中获取自定义供应商URL
    const res = await getSystemConfig()
    for (const group of res.data.groups || []) {
      if (group.name === 'custom_llm_providers') {
        const providerField = group.fields?.find(f => f.key === 'custom_providers')
        if (providerField?.value) {
          for (const p of providerField.value as any[]) {
            providerUrlMap.value[p.name] = p.base_url
          }
        }
      }
    }
  } catch { /* ignore */ }
}

function extractProviderUrlsFromTab(tabData: StructuredConfigResponse) {
  // 从 LLM Tab 的字段中提取供应商选项的 URL 映射
  // 后端在 SelectOption.description 中存储了 base_url
  for (const group of tabData.groups || []) {
    for (const field of group.fields || []) {
      if (field.key.endsWith('.llm_provider') || field.key === 'llm_provider') {
        for (const opt of field.options || []) {
          if (opt.description) {
            providerUrlMap.value[opt.value] = opt.description
          }
        }
      }
    }
  }
}

async function fetchTask() {
  try {
    const res = await getTask(taskId.value)
    task.value = res.data
  } catch { /* ignore */ }
}

async function testAgentConnection(groupName: string) {
  // 从 groupName 提取 agent key，如 "agent_trend_momentum" -> "trend_momentum"
  const agentKey = groupName.replace(/^agent_/, '')
  const prefix = `llm_agents.${agentKey}`

  const baseUrl = String(formValues.value[`${prefix}.llm_base_url`] || '').trim()
  const model = String(formValues.value[`${prefix}.llm_model`] || '').trim()
  const apiKey = String(formValues.value[`${prefix}.llm_api_key`] || '').trim()
  const temperature = Number(formValues.value[`${prefix}.temperature`] ?? 0.2)
  const maxTokens = Number(formValues.value[`${prefix}.max_tokens`] ?? 8192)
  const timeoutSeconds = Number(formValues.value[`${prefix}.timeout_seconds`] ?? 60)

  if (!baseUrl || !model || !apiKey) {
    testResults.value[groupName] = { success: false, message: '请先填写 API 地址、模型名称和 API Key' }
    return
  }

  testingAgent.value = groupName
  delete testResults.value[groupName]

  try {
    const res = await testLlmConnection(taskId.value, {
      base_url: baseUrl,
      model,
      api_key: apiKey,
      temperature,
      max_tokens: maxTokens,
      timeout_seconds: timeoutSeconds,
    })
    testResults.value[groupName] = res.data
  } catch (e: any) {
    testResults.value[groupName] = { success: false, message: e?.response?.data?.message || e?.message || '请求失败' }
  } finally {
    testingAgent.value = null
  }
}

async function fetchTabs() {
  loading.value = true
  try {
    const res = await getTaskConfigTabs(taskId.value)
    tabs.value = res.data
    if (tabs.value.length > 0 && !activeTab.value) {
      activeTab.value = tabs.value[0].section
      await loadTab(activeTab.value)
    }
  } finally {
    loading.value = false
  }
}

async function loadTab(section: string) {
  try {
    const res = await getTaskConfigTab(taskId.value, section)
    currentTabData.value = res.data
    // 从 LLM Tab 提取供应商URL映射
    if (section === 'llm_params') {
      extractProviderUrlsFromTab(res.data)
    }
    const values: Record<string, any> = {}
    // 第一遍：收集所有原始值（不对数字做兜底）
    for (const group of res.data.groups || []) {
      for (const field of group.fields || []) {
        values[field.key] = field.value ?? field.defaultValue
      }
    }
    // 第二遍：数字字段兜底，但处于禁用状态的字段保持原样
    for (const group of res.data.groups || []) {
      for (const field of group.fields || []) {
        if (field.type === 'number' || field.type === 'slider') {
          let val = values[field.key]
          const disabled = (() => {
            function check(key?: string, value?: any): boolean {
              if (!key) return false
              const v = values[key]
              if (Array.isArray(value)) return value.includes(v)
              return v === value
            }
            return check(field.disableWhenKey, field.disableWhenValue)
          })()
          if (!disabled && (val === null || val === undefined || val === '' || isNaN(Number(val)))) {
            val = field.defaultValue ?? 0
          }
          if (val !== null && val !== undefined && val !== '') {
            val = Number(val)
          }
          values[field.key] = val
        }
      }
    }
    formValues.value = { ...values }
    originalValues.value = { ...values }
    changedTabs.value.delete(section)
  } catch { /* ignore */ }
}

async function switchTab(section: string) {
  if (activeTab.value === section) return

  // 检查当前 Tab 是否有未保存的修改
  if (changedTabs.value.has(activeTab.value)) {
    try {
      await ElMessageBox.confirm(
        '当前标签页的参数已经被修改，保存修改还是放弃修改？',
        '未保存的修改',
        {
          confirmButtonText: '保存',
          cancelButtonText: '放弃',
          distinguishCancelAndClose: true,
          type: 'warning',
        }
      )
      // 用户点击"保存"
      await saveTab()
    } catch (action: any) {
      if (action === 'cancel') {
        // 用户点击"放弃"，重置当前 Tab
        resetTab()
      } else {
        // 用户点击关闭或按 ESC，取消切换
        return
      }
    }
  }

  activeTab.value = section
  await loadTab(section)
}

function validateForm(): string | null {
  if (!currentTabData.value) return null
  for (const group of currentTabData.value.groups || []) {
    for (const field of group.fields || []) {
      // 被禁用的字段跳过验证
      if (isFieldDisabled(field)) continue

      const val = formValues.value[field.key]

      // 必填验证
      if (field.required) {
        if (val === null || val === undefined || val === '') {
          return `${group.label} - ${field.label} 为必填项`
        }
      }

      // 数字范围验证
      if (field.type === 'number' && val !== null && val !== undefined && val !== '') {
        const num = Number(val)
        if (isNaN(num)) {
          return `${group.label} - ${field.label} 必须是有效数字`
        }
        if (field.min !== null && field.min !== undefined && num < Number(field.min)) {
          return `${group.label} - ${field.label} 不能小于 ${field.min}`
        }
        if (field.max !== null && field.max !== undefined && num > Number(field.max)) {
          return `${group.label} - ${field.label} 不能大于 ${field.max}`
        }
      }
    }
  }
  return null
}

async function saveTab() {
  if (!currentTabData.value) return

  const error = validateForm()
  if (error) {
    ElMessage.warning(error)
    return
  }

  // selector tab 额外的日期校验
  if (currentTabData.value.section === 'selector') {
    const selectorError = validateSelectorDates()
    if (selectorError) {
      ElMessage.warning(selectorError)
      return
    }
  }

  saving.value = true
  try {
    await updateTaskConfigTab(taskId.value, currentTabData.value.section, formValues.value)
    originalValues.value = { ...formValues.value }
    changedTabs.value.delete(currentTabData.value.section)
    ElMessage.success('配置已保存')
    // 刷新 Tab 元数据，更新 saved 状态使后续 Tab 解锁
    await fetchTabs()
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function resetTab() {
  formValues.value = { ...originalValues.value }
  changedTabs.value.delete(activeTab.value)
  ElMessage.info('已重置为上次保存的值')
}

async function onSelectorApplied() {
  await loadTab('selector')
}

function onApplySelectorWindow(payload: { start: string; end: string; span: number }) {
  formValues.value['manual_train_start_date'] = payload.start
  formValues.value['manual_train_end_date'] = payload.end
}

function validateSelectorDates(): string | null {
  const v = formValues.value
  const today = new Date()
  today.setHours(0, 0, 0, 0)

  const testStart = v['test_start_date']
  const testEnd = v['test_end_date']
  if (!testStart || !testEnd) {
    return 'Test 开始日期与 Test 结束日期必填'
  }
  if (new Date(testEnd) < new Date(testStart)) {
    return 'Test 结束日期不能早于 Test 开始日期'
  }
  if (new Date(testEnd) > today) {
    return 'Test 结束日期不能超过今天'
  }

  // 训练窗口（两种模式都必须填）
  const trainStart = v['manual_train_start_date']
  const trainEnd = v['manual_train_end_date']
  if (!trainStart || !trainEnd) {
    return '训练开始日期与训练结束日期必填'
  }
  if (new Date(trainEnd) < new Date(trainStart)) {
    return '训练结束日期不能早于训练开始日期'
  }
  if (new Date(trainEnd) > today) {
    return '训练结束日期不能超过今天'
  }
  if (new Date(trainEnd) >= new Date(testStart)) {
    return '训练结束日期必须早于 Test 开始日期'
  }

  // 训练工作流校验
  const mode = v['training_workflow.mode'] || 'static_split'
  if (mode === 'static_split') {
    const ds = v['training_workflow.static_split.discovery_start_date']
    const de = v['training_workflow.static_split.discovery_end_date']
    const vs = v['training_workflow.static_split.validation_start_date']
    const ve = v['training_workflow.static_split.validation_end_date']
    if (!ds || !de || !vs || !ve) {
      return '固定两段切分模式下，发现期与验证期的开始/结束日期必填'
    }
    if (new Date(de) < new Date(ds)) {
      return '发现期结束日期不能早于发现期开始日期'
    }
    if (new Date(ve) < new Date(vs)) {
      return '验证期结束日期不能早于验证期开始日期'
    }
    if (new Date(de) >= new Date(vs)) {
      return '验证期开始日期必须晚于发现期结束日期'
    }
    if (new Date(ds) < new Date(trainStart) || new Date(ve) > new Date(trainEnd)) {
      return '发现期与验证期必须在训练窗口范围内'
    }
  } else if (mode === 'walk_forward') {
    const dwm = Number(v['training_workflow.walk_forward.discovery_window_months'] ?? 0)
    const vwm = Number(v['training_workflow.walk_forward.validation_window_months'] ?? 0)
    const sm = Number(v['training_workflow.walk_forward.step_months'] ?? 0)
    if (dwm <= 0 || vwm <= 0 || sm <= 0) {
      return '滚动窗口模式下，发现窗口/验证窗口/滚动步长必须大于 0'
    }
    if (vwm > dwm) {
      return '验证窗口（月）不能大于发现窗口（月）'
    }
  }

  return null
}

watch(formValues, () => {
  if (currentTabData.value) {
    const changed = JSON.stringify(formValues.value) !== JSON.stringify(originalValues.value)
    if (changed) {
      changedTabs.value.add(currentTabData.value.section)
    } else {
      changedTabs.value.delete(currentTabData.value.section)
    }
  }
}, { deep: true })

onMounted(async () => {
  await fetchTask()
  await loadProviderUrlMap()
  await fetchTabs()
})
</script>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

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
  gap: 10px;
}

.config-body {
  display: flex;
  min-height: calc(100vh - var(--sb-header-height) - 80px);
}

.config-tabs {
  width: 280px;
  background: var(--sb-surface);
  border-right: 1px solid var(--sb-border-light);
  padding: 16px 12px;
  flex-shrink: 0;
}

.tab-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  margin: 2px 0;
  border-radius: var(--sb-radius);
  color: var(--sb-text-secondary);
  cursor: pointer;
  transition: var(--sb-transition);
  position: relative;
}

.tab-item:hover {
  background: var(--sb-bg);
  color: var(--sb-text);
}

.tab-item.active {
  background: var(--sb-primary-light);
  color: var(--sb-primary-dark);
}

.tab-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  background: var(--sb-primary);
  border-radius: 0 2px 2px 0;
}

.tab-info {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}

.tab-label {
  font-size: 14px;
  font-weight: 500;
  line-height: 1.3;
  display: flex;
  align-items: center;
  gap: 8px;
}

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  background: var(--sb-primary);
  border-radius: 10px;
  flex-shrink: 0;
}

.tab-item.active .tab-badge {
  background: var(--sb-primary-dark);
}

.tab-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}

.tab-desc {
  font-size: 11px;
  color: var(--sb-text-muted);
  margin-top: 2px;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tab-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--sb-primary);
  flex-shrink: 0;
}

.config-form {
  flex: 1;
  padding: 28px 40px 40px;
  overflow-y: auto;
}

.form-title {
  margin-bottom: 28px;
}

.form-title h2 {
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 6px;
}

.form-title p {
  color: var(--sb-text-secondary);
  font-size: 14px;
  margin: 0;
}

.form-group {
  background: var(--sb-surface);
  border: 1px solid var(--sb-border-light);
  border-radius: var(--sb-radius-lg);
  padding: 24px;
  margin-bottom: 16px;
}

.stage-divider {
  margin: 28px 0 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--sb-primary);
}

.stage-divider:first-child {
  margin-top: 0;
}

.stage-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--sb-primary-dark);
  margin: 0;
}

.group-header {
  margin-bottom: 20px;
}

.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.group-title h4 {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
}

.test-btn {
  margin-left: auto;
  flex-shrink: 0;
}

.test-result-alert {
  margin-top: 8px;
}

.group-desc {
  color: var(--sb-text-secondary);
  font-size: 13px;
  margin: 0;
}

.fields-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 20px 24px;
}

.field-item {
  display: flex;
  flex-direction: column;
}

.field-item.field-full {
  grid-column: span 2;
}

.field-label-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--sb-text);
}

.required-mark {
  color: var(--el-color-danger);
  font-size: 14px;
  font-weight: bold;
  margin-left: 2px;
}

.source-tag {
  font-size: 10px;
  height: 18px;
  padding: 0 6px;
}

.field-control {
  width: 100%;
}

.field-hint {
  margin-top: 6px;
  font-size: 12px;
  color: var(--sb-text-muted);
  line-height: 1.4;
}

.slider-wrapper {
  padding: 8px 4px;
}

.tag-select-wrapper {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tab-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 32px;
  padding-top: 24px;
  border-top: 1px solid var(--sb-border-light);
}

@media (max-width: 768px) {
  .fields-grid {
    grid-template-columns: 1fr;
  }
  .field-item.field-full {
    grid-column: span 1;
  }
  .config-tabs {
    width: 200px;
  }
}
</style>
