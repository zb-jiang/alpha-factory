import api from './index'

export interface TaskResponse {
  id: number
  taskName: string
  taskDesc: string
  stagingPath: string
  status: 'NEW' | 'RUNNING' | 'STOPPED' | 'COMPLETED' | 'ERROR'
  currentStep: string | null
  pid: number | null
  createdAt: string
  updatedAt: string
}

export interface TaskCreateRequest {
  taskName: string
  taskDesc?: string
}

export interface TaskStartRequest {
  step: string
}

export function listTasks() {
  return api.get<any, { data: TaskResponse[] }>('/tasks')
}

export function createTask(data: TaskCreateRequest) {
  return api.post<any, { data: TaskResponse }>('/tasks', data)
}

export function getTask(taskId: number) {
  return api.get<any, { data: TaskResponse }>(`/tasks/${taskId}`)
}

export function deleteTask(taskId: number, deleteStaging = true) {
  return api.delete(`/tasks/${taskId}?deleteStaging=${deleteStaging}`)
}

export function startTask(taskId: number, data: TaskStartRequest) {
  return api.post(`/tasks/${taskId}/start`, data)
}

export function stopTask(taskId: number) {
  return api.post(`/tasks/${taskId}/stop`)
}

export function getTaskStatus(taskId: number) {
  return api.get<any, { data: { status: string; currentStep: string; pid: number } }>(`/tasks/${taskId}/status`)
}

export function getTaskLogs(taskId: number, lines = 200) {
  return api.get<any, { data: string[] }>(`/tasks/${taskId}/logs?lines=${lines}`)
}
