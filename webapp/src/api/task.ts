import api from './index'

export interface TaskResponse {
  id: number
  taskName: string
  taskDesc: string
  stagingPath: string
  status: 'NEW' | 'RUNNING' | 'TRAINING_FINISHED' | 'TESTING_FINISHED'
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

export interface StepProgressResponse {
  status: string
  currentStep: string
  running: boolean
  progress_logs: string[]
  active_context: {
    run_mode?: string
    workflow_state?: {
      window_id?: string
      stage?: string  // 'discovery' | 'validation'
      iteration?: number
      window_config?: Record<string, any>
    }
    [key: string]: any
  } | null
}

export function getStepProgress(taskId: number, step: string, maxLines = 500) {
  return api.get<any, { data: StepProgressResponse }>(
    `/tasks/${taskId}/step-progress`,
    { params: { step, maxLines } }
  )
}

export interface TrainingArtifactsResponse {
  status: string
  windows: Array<Record<string, any>>
  cross_window: Record<string, any>
  readiness?: {
    oos_factors_ready?: boolean
  }
}

export function getTrainingArtifacts(taskId: number) {
  return api.get<any, { data: TrainingArtifactsResponse }>(`/tasks/${taskId}/training-artifacts`)
}

export interface OosArtifactsResponse {
  status: string
  factors: Array<Record<string, any>>
  top3: Array<Record<string, any>>
  period: { test_start_date?: string; test_end_date?: string }
  input_factor_count: number
  input_factors?: Array<Record<string, any>>
  readiness?: {
    alphalens_report?: boolean
    alphalens_dashboard?: boolean
    joinquant_export?: boolean
  }
}

export function getOosArtifacts(taskId: number) {
  return api.get<any, { data: OosArtifactsResponse }>(`/tasks/${taskId}/oos-artifacts`)
}

export function getTaskLogs(taskId: number, lines = 200) {
  return api.get<any, { data: string[] }>(`/tasks/${taskId}/logs?lines=${lines}`)
}
