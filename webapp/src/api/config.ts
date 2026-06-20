import api from './index'

// ============================================================
// 类型定义
// ============================================================

export interface SelectOption {
  label: string
  value: string
  description?: string
}

export interface ConfigField {
  key: string
  label: string
  description?: string
  type: 'text' | 'number' | 'select' | 'switch' | 'slider' | 'date' | 'password' | 'tag_select' | 'provider_list'
  value: any
  defaultValue: any
  placeholder?: string
  options?: SelectOption[]
  min?: number
  max?: number
  step?: number
  precision?: number
  required?: boolean
  readonly?: boolean
  source?: 'global' | 'user' | 'task'
  showWhenKey?: string
  showWhenValue?: any
  showWhenKey2?: string
  showWhenValue2?: any
  optionFilterKey?: string
  optionFilterMap?: Record<string, string[]>
}

export interface ConfigGroup {
  name: string
  label: string
  description?: string
  icon?: string
  fields: ConfigField[]
}

export interface StructuredConfigResponse {
  section: string
  sectionLabel: string
  sectionDescription?: string
  sectionIcon?: string
  groups: ConfigGroup[]
  saved?: boolean
}

// ============================================================
// 系统配置 API（原全局+用户配置合并）
// ============================================================

export function getSystemConfig() {
  return api.get<any, { data: StructuredConfigResponse }>('/config/system')
}

export function updateSystemConfigSection(section: string, values: Record<string, any>) {
  return api.put(`/config/system/${section}`, values)
}

// ============================================================
// 任务级配置 API
// ============================================================

export function getTaskConfigTabs(taskId: number) {
  return api.get<any, { data: StructuredConfigResponse[] }>(`/tasks/${taskId}/config`)
}

export function getTaskConfigTab(taskId: number, tab: string) {
  return api.get<any, { data: StructuredConfigResponse }>(`/tasks/${taskId}/config/${tab}`)
}

export function updateTaskConfigTab(taskId: number, tab: string, values: Record<string, any>) {
  return api.put(`/tasks/${taskId}/config/${tab}`, values)
}

export function testLlmConnection(taskId: number, params: { base_url: string; model: string; api_key: string }) {
  return api.post<any, { data: { success: boolean; message: string } }>(`/tasks/${taskId}/config/llm-test`, params)
}

export function runSelector(taskId: number) {
  return api.post(`/tasks/${taskId}/config/selector/run`)
}

export function getSelectorResult(taskId: number) {
  return api.get<any, { data: { ready: boolean; running?: boolean; progress_logs?: string[]; error?: string; [key: string]: any } }>(`/tasks/${taskId}/config/selector/result`)
}

export function applySelectorResult(taskId: number, params: {
  trainStartDate: string
  trainEndDate: string
  recommendSpanMonths: number
  mode: string
}) {
  return api.post(`/tasks/${taskId}/config/selector/apply`, params)
}
