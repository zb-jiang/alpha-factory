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

export const STATUS_MAP: Record<string, { label: string; type: string }> = {
  IDLE: { label: '空闲', type: 'info' },
  RUNNING: { label: '运行中', type: 'success' },
  STOPPED: { label: '已停止', type: 'warning' },
  ERROR: { label: '错误', type: 'danger' },
}

export const CONFIG_LABELS: Record<string, string> = {
  env: '环境配置 (env.yaml)',
  analysis_rule: '分析口径 (analysis_rule.yaml)',
  backtest_rule: '回测配置 (backtest_rule.yaml)',
  feature_pool: '特征池 (feature_pool.yaml)',
  market_context: '市场环境 (market_context.yaml)',
  score: '评分权重 (score.yaml)',
  selector: '窗口选择器 (selector.yaml)',
}
