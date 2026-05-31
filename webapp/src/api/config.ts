import api from './index'

export interface ConfigResponse {
  name: string
  content: string
}

export function listConfigs(taskId: number) {
  return api.get<any, { data: ConfigResponse[] }>(`/tasks/${taskId}/config`)
}

export function getConfig(taskId: number, configName: string) {
  return api.get<any, { data: ConfigResponse }>(`/tasks/${taskId}/config/${configName}`)
}

export function updateConfig(taskId: number, configName: string, content: string) {
  return api.put(`/tasks/${taskId}/config/${configName}`, { content })
}

export function resetConfig(taskId: number, configName: string) {
  return api.post(`/tasks/${taskId}/config/${configName}/reset`)
}
