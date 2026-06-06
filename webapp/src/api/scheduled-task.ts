import api from './index'

export interface ScheduledTaskRequest {
  name: string
  description?: string
  sourceTaskId: number
  cronExpression: string
  enabled: boolean
}

export interface ScheduledTaskResponse {
  id: number
  name: string
  description?: string
  sourceTaskId: number
  sourceTaskName: string
  cronExpression: string
  enabled: boolean
  lastRunAt?: string
  nextRunAt?: string
  createdAt: string
  updatedAt: string
}

export function listScheduledTasks() {
  return api.get<any, ScheduledTaskResponse[]>('/scheduled-tasks')
}

export function createScheduledTask(data: ScheduledTaskRequest) {
  return api.post<any, ScheduledTaskResponse>('/scheduled-tasks', data)
}

export function updateScheduledTask(id: number, data: ScheduledTaskRequest) {
  return api.put<any, ScheduledTaskResponse>(`/scheduled-tasks/${id}`, data)
}

export function deleteScheduledTask(id: number) {
  return api.delete(`/scheduled-tasks/${id}`)
}

export function toggleScheduledTask(id: number, enabled: boolean) {
  return api.post<any, ScheduledTaskResponse>(`/scheduled-tasks/${id}/toggle?enabled=${enabled}`)
}

export function triggerScheduledTask(id: number) {
  return api.post(`/scheduled-tasks/${id}/trigger`)
}
