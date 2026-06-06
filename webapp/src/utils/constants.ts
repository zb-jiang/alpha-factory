export function getWebSocketUrl(taskId: number): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//localhost:8081/ws/tasks/${taskId}/logs`
}

export function formatDateTime(dt: string | null): string {
  if (!dt) return '-'
  return dt.replace('T', ' ').substring(0, 19)
}

export const STEP_LABELS: Record<string, string> = {
  '10': 'Step10 迭代挖掘',
  '11': 'Step11 样本外盲测',
  '12': 'Step12 Alphalens报告',
  '13': 'Step13 可视化看板',
  '14': 'Step14 导出聚宽代码',
}

export const STATUS_MAP: Record<string, { label: string; type: string; cssClass: string }> = {
  NEW: { label: '新建', type: 'info', cssClass: 'sb-status-new' },
  RUNNING: { label: '运行中', type: 'success', cssClass: 'sb-status-running' },
  STOPPED: { label: '已终止', type: 'danger', cssClass: 'sb-status-stopped' },
  COMPLETED: { label: '已完成', type: 'success', cssClass: 'sb-status-completed' },
  ERROR: { label: '错误', type: 'warning', cssClass: 'sb-status-error' },
}
