<script setup lang="ts">
/**
 * 因子挖掘产物展示面板。
 *
 * 设计思路：editorial / 数据周报风格
 *  - 顶部一行全局摘要数字（kicker）
 *  - 每个窗口=一个章节，左侧序号+时间区间形成时间柱
 *  - discovery 阶段：特征体检 chips + 市场环境标签矩阵 + iter 网格 + 候选因子表
 *  - validation 阶段：因子表 + top3
 *  - 末尾跨窗口排名
 */
import { computed, ref } from 'vue'

const props = defineProps<{
  artifacts: {
    status?: string
    windows?: Array<Record<string, any>>
    cross_window?: Record<string, any>
  } | null
}>()

const windows = computed(() => props.artifacts?.windows || [])
const crossWindow = computed(() => props.artifacts?.cross_window || {})
const hasArtifacts = computed(() => windows.value.length > 0 || (crossWindow.value && Object.keys(crossWindow.value).length > 0))

// 折叠状态：默认全部展开
const expanded = ref<Record<string, boolean>>({})
function isExpanded(id: string): boolean {
  return expanded.value[id] !== false
}
function toggle(id: string) {
  expanded.value[id] = !isExpanded(id)
}

// ------------- 格式化工具 -------------
function fmtNum(v: any, digits = 4): string {
  if (v === null || v === undefined || v === '') return 'NA'
  const n = Number(v)
  if (!Number.isFinite(n)) return 'NA'
  return n.toFixed(digits)
}
function fmtPercent(v: any, digits = 2): string {
  if (v === null || v === undefined || v === '') return 'NA'
  const n = Number(v)
  if (!Number.isFinite(n)) return 'NA'
  return (n * 100).toFixed(digits) + '%'
}
function fmtInt(v: any): string {
  if (v === null || v === undefined || v === '') return 'NA'
  const n = Number(v)
  if (!Number.isFinite(n)) return 'NA'
  return Math.round(n).toLocaleString()
}
function fmtStr(v: any): string {
  if (v === null || v === undefined || v === '') return 'NA'
  return String(v)
}
function fmtDirection(v: any): { label: string; cls: string } {
  if (v === null || v === undefined || v === '') return { label: 'NA', cls: 'dir-na' }
  const s = String(v).toLowerCase()
  if (s === 'positive' || s === 'pos' || s === '+') return { label: '正向', cls: 'dir-pos' }
  if (s === 'negative' || s === 'neg' || s === '-') return { label: '反向', cls: 'dir-neg' }
  if (s === 'neutral') return { label: '中性', cls: 'dir-neu' }
  return { label: String(v), cls: 'dir-other' }
}

// "iter_01" → "iter 1"；空值显示 NA
function shortIter(v: any): string {
  if (v === null || v === undefined || v === '') return 'NA'
  const s = String(v)
  const m = s.match(/iter_?0*(\d+)/i)
  if (m) return `iter ${m[1]}`
  return s
}

// ------------- 时间区间 -------------
function windowRange(w: any, stage: 'discovery' | 'validation'): string {
  const startKey = `${stage}_start_date`
  const endKey = `${stage}_end_date`
  const summary = w?.window_summary?.window || {}
  const start = w?.[startKey] || summary[startKey]
  const end = w?.[endKey] || summary[endKey]
  if (!start || !end) return 'NA'
  return `${start} ~ ${end}`
}

// ------------- 跨窗口摘要 -------------
const summaryStats = computed(() => {
  const cs: any = crossWindow.value || {}
  const summary = cs.summary || {}
  return {
    windowCount: summary.window_count ?? windows.value.length,
    validationRowCount: summary.validation_row_count ?? 0,
    aggregatedCount: summary.aggregated_factor_count ?? (Array.isArray(cs.ranking) ? cs.ranking.length : 0),
  }
})
</script>

<template>
  <div v-if="hasArtifacts" class="ta-root">
    <!-- 顶部：全局摘要 -->
    <header class="ta-header">
      <div class="ta-kicker">Factor Mining Report</div>
      <h2 class="ta-title">挖掘产物概览</h2>
      <div class="ta-stats">
        <div class="stat-block">
          <div class="stat-num">{{ summaryStats.windowCount }}</div>
          <div class="stat-label">训练窗口</div>
        </div>
        <div class="stat-divider"></div>
        <div class="stat-block">
          <div class="stat-num">{{ summaryStats.aggregatedCount }}</div>
          <div class="stat-label">聚合因子</div>
        </div>
        <div class="stat-divider"></div>
        <div class="stat-block">
          <div class="stat-num">{{ summaryStats.validationRowCount }}</div>
          <div class="stat-label">验证通过项</div>
        </div>
      </div>
    </header>

    <!-- 每个窗口一个章节 -->
    <section v-for="(w, wIdx) in windows" :key="w.window_id" class="window-chapter">
      <div class="chapter-rail">
        <div class="rail-dot"></div>
        <div class="rail-line" v-if="wIdx < windows.length - 1"></div>
      </div>

      <div class="chapter-body">
        <!-- 章节标题 -->
        <div class="chapter-head" @click="toggle(w.window_id + '_main')">
          <div class="chapter-index">{{ String(wIdx + 1).padStart(2, '0') }}</div>
          <div class="chapter-title-block">
            <div class="chapter-id">{{ w.window_id }}</div>
            <div class="chapter-period">
              <span class="period-tag stage-discovery">DISCOVERY</span>
              <span class="period-text">{{ windowRange(w, 'discovery') }}</span>
              <span class="period-tag stage-validation">VALIDATION</span>
              <span class="period-text">{{ windowRange(w, 'validation') }}</span>
            </div>
          </div>
          <div class="chapter-counters">
            <span class="counter">
              <span class="counter-num">{{ w.window_summary?.discovery_candidate_count ?? 'NA' }}</span>
              <span class="counter-label">候选</span>
            </span>
            <span class="counter">
              <span class="counter-num">{{ w.window_summary?.validation_passed_count ?? 'NA' }}</span>
              <span class="counter-label">通过</span>
            </span>
            <span class="chevron" :class="{ open: isExpanded(w.window_id + '_main') }">▾</span>
          </div>
        </div>

        <div v-if="isExpanded(w.window_id + '_main')" class="chapter-content">
          <!-- 子节 1：特征体检 -->
          <div class="sub-section">
            <div class="sub-title">
              <span class="sub-num">01</span>
              <span>特征体检</span>
            </div>
            <div v-if="w.discovery?.health_summary && Object.keys(w.discovery.health_summary).length" class="health-grid">
              <div class="health-block">
                <div class="health-label">优势特征 (top)</div>
                <div class="chip-row">
                  <span v-for="f in (w.discovery.health_summary.top_features || [])" :key="'top-' + f" class="chip chip-pos">{{ f }}</span>
                  <span v-if="!(w.discovery.health_summary.top_features || []).length" class="chip chip-na">NA</span>
                </div>
              </div>
              <div class="health-block">
                <div class="health-label">弱势特征 (weak)</div>
                <div class="chip-row">
                  <span v-for="f in (w.discovery.health_summary.weak_features || [])" :key="'weak-' + f" class="chip chip-neg">{{ f }}</span>
                  <span v-if="!(w.discovery.health_summary.weak_features || []).length" class="chip chip-na">NA</span>
                </div>
              </div>
              <div class="health-block">
                <div class="health-label">不稳定特征 (unstable)</div>
                <div class="chip-row">
                  <span v-for="f in (w.discovery.health_summary.unstable_features || [])" :key="'uns-' + f" class="chip chip-warn">{{ f }}</span>
                  <span v-if="!(w.discovery.health_summary.unstable_features || []).length" class="chip chip-na">NA</span>
                </div>
              </div>
              <div class="health-block health-block-wide">
                <div class="health-label">高相关性 (|ρ| ≥ 0.97)</div>
                <div class="corr-list">
                  <div v-for="(pair, idx) in (w.discovery.health_summary.high_corr_pairs || []).slice(0, 6)" :key="idx" class="corr-row">
                    <span class="chip-mini">{{ pair[0] }}</span>
                    <span class="corr-link">↔</span>
                    <span class="chip-mini">{{ pair[1] }}</span>
                    <span class="corr-val">{{ fmtNum(pair[2], 3) }}</span>
                  </div>
                  <div v-if="!(w.discovery.health_summary.high_corr_pairs || []).length" class="empty-tip">NA</div>
                </div>
              </div>
            </div>
            <div v-else class="empty-tip">尚未生成 health_summary.json</div>
          </div>

          <!-- 子节 2：市场环境 -->
          <div class="sub-section">
            <div class="sub-title">
              <span class="sub-num">02</span>
              <span>市场环境</span>
            </div>
            <div v-if="w.discovery?.market_context?.train_context" class="market-context">
              <div class="market-summary">
                {{ w.discovery.market_context.train_context.summary_text || 'NA' }}
              </div>
              <div class="label-grid">
                <div v-for="(value, key) in (w.discovery.market_context.train_context.labels || {})" :key="String(key)" class="label-cell">
                  <div class="label-key">{{ key }}</div>
                  <div class="label-value" :class="'lv-' + value">{{ value }}</div>
                </div>
              </div>
            </div>
            <div v-else class="empty-tip">尚未生成 market_context.json</div>
          </div>

          <!-- 子节 3：discovery 各 iter 的 top3 -->
          <div class="sub-section">
            <div class="sub-title">
              <span class="sub-num">03</span>
              <span>Discovery 迭代</span>
              <span class="sub-meta">{{ w.discovery?.iterations?.length || 0 }} 轮</span>
            </div>
            <div v-if="w.discovery?.iterations?.length" class="iter-grid">
              <div v-for="iter in w.discovery.iterations" :key="iter.iteration" class="iter-card">
                <div class="iter-head">
                  <span class="iter-name">{{ iter.iteration }}</span>
                  <span class="iter-count">{{ (iter.top3 || []).length }} / top3</span>
                </div>
                <div v-if="(iter.top3 || []).length" class="iter-factors">
                  <div v-for="(f, fi) in iter.top3" :key="iter.iteration + '-' + fi" class="iter-factor">
                    <div class="factor-row-1">
                      <span class="factor-rank">#{{ fi + 1 }}</span>
                      <span class="factor-name">{{ fmtStr(f.factor_name) }}</span>
                      <span class="factor-score">{{ fmtNum(f.total_score, 3) }}</span>
                    </div>
                    <div class="factor-formula">{{ fmtStr(f.formula) }}</div>
                    <div class="factor-row-2">
                      <span>rankIC <b>{{ fmtNum(f.mean_rank_ic, 4) }}</b></span>
                      <span>rankIR <b>{{ fmtNum(f.rank_ic_ir, 3) }}</b></span>
                      <span>obs <b>{{ fmtInt(f.observation_count) }}</b></span>
                      <span>annRet <b>{{ fmtPercent(f.annualized_return, 2) }}</b></span>
                      <span class="dir-tag" :class="fmtDirection(f.llm_direction).cls">LLM·{{ fmtDirection(f.llm_direction).label }}</span>
                      <span class="dir-tag" :class="fmtDirection(f.empirical_direction).cls">实证·{{ fmtDirection(f.empirical_direction).label }}</span>
                    </div>
                  </div>
                </div>
                <div v-else class="empty-tip">尚无 top3</div>
              </div>
            </div>
            <div v-else class="empty-tip">尚未生成迭代产物</div>
          </div>

          <!-- 子节 4：discovery 汇总候选因子 -->
          <div class="sub-section">
            <div class="sub-title">
              <span class="sub-num">04</span>
              <span>Discovery 汇总候选</span>
              <span class="sub-meta">{{ w.discovery?.candidates?.length || 0 }} 个公式</span>
            </div>
            <div v-if="w.discovery?.candidates?.length" class="factor-table">
              <div class="ft-head">
                <span class="ft-col-name">因子</span>
                <span class="ft-col-source">来源</span>
                <span class="ft-col-formula">公式</span>
                <span class="ft-col-num">rankIC</span>
                <span class="ft-col-num">rankIR</span>
                <span class="ft-col-num">obs</span>
                <span class="ft-col-num">annRet</span>
                <span class="ft-col-tag">LLM</span>
                <span class="ft-col-tag">实证</span>
              </div>
              <div v-for="(f, idx) in w.discovery.candidates" :key="'cand-' + idx" class="ft-row">
                <span class="ft-col-name"><span class="ft-rank">{{ idx + 1 }}.</span> {{ fmtStr(f.factor_name) }}</span>
                <span class="ft-col-source"><span class="src-chip">{{ shortIter(f.source_iteration) }}</span></span>
                <span class="ft-col-formula mono">{{ fmtStr(f.formula) }}</span>
                <span class="ft-col-num">{{ fmtNum(f.mean_rank_ic, 4) }}</span>
                <span class="ft-col-num">{{ fmtNum(f.rank_ic_ir, 3) }}</span>
                <span class="ft-col-num">{{ fmtInt(f.observation_count) }}</span>
                <span class="ft-col-num">{{ fmtPercent(f.annualized_return, 2) }}</span>
                <span class="ft-col-tag"><span class="dir-tag" :class="fmtDirection(f.llm_direction).cls">{{ fmtDirection(f.llm_direction).label }}</span></span>
                <span class="ft-col-tag"><span class="dir-tag" :class="fmtDirection(f.empirical_direction).cls">{{ fmtDirection(f.empirical_direction).label }}</span></span>
              </div>
            </div>
            <div v-else class="empty-tip">discovery 阶段尚未产出候选因子</div>
          </div>

          <!-- 子节 5：validation 阶段 -->
          <div class="sub-section">
            <div class="sub-title">
              <span class="sub-num">05</span>
              <span>Validation 结果</span>
              <span class="sub-meta">{{ w.validation?.factors?.length || 0 }} 个</span>
            </div>
            <div v-if="w.validation?.factors?.length" class="factor-table">
              <div class="ft-head">
                <span class="ft-col-name">因子</span>
                <span class="ft-col-source">来源</span>
                <span class="ft-col-formula">公式</span>
                <span class="ft-col-num">rankIC</span>
                <span class="ft-col-num">rankIR</span>
                <span class="ft-col-num">obs</span>
                <span class="ft-col-num">annRet</span>
                <span class="ft-col-tag">LLM</span>
                <span class="ft-col-tag">实证</span>
              </div>
              <div v-for="(f, idx) in w.validation.factors" :key="'val-' + idx" class="ft-row">
                <span class="ft-col-name"><span class="ft-rank">{{ idx + 1 }}.</span> {{ fmtStr(f.factor_name) }}</span>
                <span class="ft-col-source"><span class="src-chip">{{ shortIter(f.source_iteration) }}</span></span>
                <span class="ft-col-formula mono">{{ fmtStr(f.formula) }}</span>
                <span class="ft-col-num">{{ fmtNum(f.mean_rank_ic, 4) }}</span>
                <span class="ft-col-num">{{ fmtNum(f.rank_ic_ir, 3) }}</span>
                <span class="ft-col-num">{{ fmtInt(f.observation_count) }}</span>
                <span class="ft-col-num">{{ fmtPercent(f.annualized_return, 2) }}</span>
                <span class="ft-col-tag"><span class="dir-tag" :class="fmtDirection(f.llm_direction).cls">{{ fmtDirection(f.llm_direction).label }}</span></span>
                <span class="ft-col-tag"><span class="dir-tag" :class="fmtDirection(f.empirical_direction).cls">{{ fmtDirection(f.empirical_direction).label }}</span></span>
              </div>
            </div>
            <div v-else class="empty-tip">validation 阶段尚未产出结果</div>
          </div>
        </div>
      </div>
    </section>

    <!-- 跨窗口汇总 -->
    <section v-if="Array.isArray(crossWindow.ranking) && crossWindow.ranking.length" class="cross-section">
      <div class="cross-head">
        <div class="cross-kicker">FINAL</div>
        <div class="cross-title">跨窗口汇总因子</div>
        <div class="cross-meta">按 window_pass_ratio · validation_total_score · annualized_return 综合排序</div>
      </div>
      <div class="cross-table">
        <div class="ct-head">
          <span class="ct-col-rank">#</span>
          <span class="ct-col-name">因子</span>
          <span class="ct-col-formula">公式</span>
          <span class="ct-col-num">通过率</span>
          <span class="ct-col-num">平均score</span>
          <span class="ct-col-num">平均rankIC</span>
          <span class="ct-col-num">平均annRet</span>
          <span class="ct-col-num">窗口</span>
        </div>
        <div v-for="(f, idx) in crossWindow.ranking" :key="'cross-' + idx" class="ct-row" :class="{ 'ct-top': idx < 3 }">
          <span class="ct-col-rank">{{ idx + 1 }}</span>
          <span class="ct-col-name">{{ fmtStr(f.factor_name) }}</span>
          <span class="ct-col-formula mono">{{ fmtStr(f.formula) }}</span>
          <span class="ct-col-num">{{ fmtPercent(f.window_pass_ratio, 1) }}</span>
          <span class="ct-col-num">{{ fmtNum(f.mean_validation_total_score, 3) }}</span>
          <span class="ct-col-num">{{ fmtNum(f.mean_rank_ic, 4) }}</span>
          <span class="ct-col-num">{{ fmtPercent(f.mean_annualized_return, 2) }}</span>
          <span class="ct-col-num">{{ fmtInt(f.windows_passed) }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ta-root {
  margin-top: 24px;
  font-family: -apple-system, BlinkMacSystemFont, "Source Han Sans SC", "PingFang SC", "Segoe UI", sans-serif;
  color: #1f2937;
}

/* ========= Header ========= */
.ta-header {
  border: 1px solid #e4e7ed;
  border-radius: 14px;
  padding: 22px 26px;
  background: linear-gradient(135deg, #fafbfc 0%, #f4f6f8 100%);
  position: relative;
  overflow: hidden;
}
.ta-header::before {
  content: '';
  position: absolute;
  top: 0; right: 0;
  width: 160px; height: 100%;
  background: radial-gradient(circle at 90% 30%, rgba(29, 78, 216, 0.06), transparent 60%);
  pointer-events: none;
}
.ta-kicker {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: #1d4ed8;
  margin-bottom: 6px;
}
.ta-title {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.01em;
  margin: 0 0 18px 0;
  color: #0f172a;
}
.ta-stats {
  display: flex;
  align-items: center;
  gap: 28px;
}
.stat-block .stat-num {
  font-size: 32px;
  font-weight: 700;
  font-feature-settings: "tnum";
  color: #0f172a;
  line-height: 1.1;
}
.stat-block .stat-label {
  font-size: 12px;
  color: #64748b;
  margin-top: 2px;
}
.stat-divider {
  width: 1px;
  height: 32px;
  background: #cbd5e1;
}

/* ========= Window chapter ========= */
.window-chapter {
  display: flex;
  margin-top: 28px;
}
.chapter-rail {
  width: 32px;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: 8px;
  flex-shrink: 0;
}
.rail-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  background: #1d4ed8;
  box-shadow: 0 0 0 4px rgba(29, 78, 216, 0.12);
}
.rail-line {
  flex: 1;
  width: 2px;
  background: linear-gradient(180deg, #cbd5e1, transparent);
  margin-top: 6px;
}
.chapter-body {
  flex: 1;
  border: 1px solid #e4e7ed;
  border-radius: 14px;
  background: #fff;
  overflow: hidden;
}
.chapter-head {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 18px 22px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.chapter-head:hover { background: #fafbfc; }
.chapter-index {
  font-size: 28px;
  font-weight: 800;
  font-feature-settings: "tnum";
  color: #cbd5e1;
  line-height: 1;
  letter-spacing: -0.02em;
  min-width: 38px;
}
.chapter-title-block { flex: 1; }
.chapter-id {
  font-size: 17px;
  font-weight: 700;
  color: #0f172a;
}
.chapter-period {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}
.period-tag {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  padding: 2px 7px;
  border-radius: 4px;
}
.period-tag.stage-discovery { background: #dbeafe; color: #1d4ed8; }
.period-tag.stage-validation { background: #dcfce7; color: #15803d; }
.period-text {
  font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
  color: #475569;
}
.chapter-counters {
  display: flex;
  align-items: center;
  gap: 18px;
}
.counter {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.counter-num {
  font-size: 18px;
  font-weight: 700;
  font-feature-settings: "tnum";
  color: #0f172a;
  line-height: 1;
}
.counter-label {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}
.chevron {
  font-size: 14px;
  color: #94a3b8;
  transition: transform 0.2s ease;
  margin-left: 4px;
}
.chevron.open { transform: rotate(180deg); }
.chapter-content {
  padding: 4px 22px 22px;
  border-top: 1px solid #f1f5f9;
}

/* ========= Sub section ========= */
.sub-section {
  padding: 18px 0;
  border-bottom: 1px dashed #e2e8f0;
}
.sub-section:last-child { border-bottom: none; }
.sub-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 14px;
}
.sub-num {
  font-size: 11px;
  font-weight: 700;
  color: #1d4ed8;
  letter-spacing: 0.1em;
  font-feature-settings: "tnum";
}
.sub-title > span:nth-child(2) {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
}
.sub-meta {
  margin-left: auto;
  font-size: 12px;
  color: #94a3b8;
}

/* ========= Health ========= */
.health-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.health-block-wide { grid-column: span 2; }
.health-label {
  font-size: 11px;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 8px;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  font-size: 12px;
  font-family: "SF Mono", "JetBrains Mono", monospace;
  padding: 3px 9px;
  border-radius: 4px;
  background: #f1f5f9;
  color: #334155;
  border: 1px solid #e2e8f0;
}
.chip-pos { background: #dcfce7; color: #15803d; border-color: #bbf7d0; }
.chip-neg { background: #fee2e2; color: #b91c1c; border-color: #fecaca; }
.chip-warn { background: #fef3c7; color: #b45309; border-color: #fde68a; }
.chip-na { background: transparent; color: #94a3b8; border-style: dashed; }
.chip-mini {
  font-family: "SF Mono", "JetBrains Mono", monospace;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 3px;
  background: #f1f5f9;
  color: #475569;
}
.corr-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.corr-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.corr-link { color: #cbd5e1; }
.corr-val {
  margin-left: auto;
  font-family: "SF Mono", "JetBrains Mono", monospace;
  color: #1d4ed8;
  font-weight: 600;
}

/* ========= Market context ========= */
.market-summary {
  font-size: 13px;
  line-height: 1.75;
  color: #334155;
  padding: 12px 14px;
  background: #f8fafc;
  border-left: 3px solid #1d4ed8;
  border-radius: 0 6px 6px 0;
  margin-bottom: 14px;
}
.label-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}
.label-cell {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 8px 10px;
}
.label-key {
  font-size: 11px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.label-value {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  margin-top: 2px;
}
.lv-未知 { color: #94a3b8; }
.lv-扩张, .lv-升温, .lv-宽松, .lv-上行, .lv-普涨 { color: #15803d; }
.lv-收缩, .lv-降温, .lv-收紧, .lv-下行, .lv-普跌 { color: #b91c1c; }
.lv-震荡, .lv-中性, .lv-分化, .lv-平稳 { color: #b45309; }

/* ========= Iter grid ========= */
.iter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}
.iter-card {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px 14px;
  background: #fff;
}
.iter-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f1f5f9;
}
.iter-name {
  font-size: 13px;
  font-weight: 700;
  color: #1d4ed8;
  font-family: "SF Mono", "JetBrains Mono", monospace;
}
.iter-count {
  font-size: 11px;
  color: #94a3b8;
}
.iter-factors { display: flex; flex-direction: column; gap: 10px; }
.iter-factor {
  border-left: 2px solid #e2e8f0;
  padding: 4px 0 4px 10px;
}
.iter-factor:hover { border-left-color: #1d4ed8; }
.factor-row-1 {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 3px;
}
.factor-rank {
  font-size: 10px;
  font-weight: 700;
  color: #94a3b8;
  font-family: "SF Mono", "JetBrains Mono", monospace;
}
.factor-name {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  flex: 1;
}
.factor-score {
  font-size: 12px;
  font-weight: 700;
  color: #1d4ed8;
  font-family: "SF Mono", "JetBrains Mono", monospace;
}
.factor-formula {
  font-size: 11px;
  color: #64748b;
  font-family: "SF Mono", "JetBrains Mono", monospace;
  line-height: 1.5;
  margin-bottom: 4px;
  word-break: break-all;
}
.factor-row-2 {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
  color: #64748b;
}
.factor-row-2 b {
  color: #1f2937;
  font-weight: 600;
  font-family: "SF Mono", "JetBrains Mono", monospace;
}

/* ========= Direction tags ========= */
.dir-tag {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  letter-spacing: 0.03em;
}
.dir-pos { background: #dcfce7; color: #15803d; }
.dir-neg { background: #fee2e2; color: #b91c1c; }
.dir-neu { background: #f1f5f9; color: #475569; }
.dir-na  { background: transparent; color: #cbd5e1; border: 1px dashed #cbd5e1; }
.dir-other { background: #f1f5f9; color: #475569; }

/* ========= Factor table ========= */
.factor-table {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
}
.ft-head, .ft-row {
  display: grid;
  grid-template-columns: 1.5fr 0.7fr 2.3fr 0.7fr 0.7fr 0.7fr 0.8fr 0.7fr 0.7fr;
  gap: 10px;
  align-items: center;
  padding: 9px 14px;
  font-size: 12px;
}
.ft-head {
  background: #f8fafc;
  border-bottom: 1px solid #e4e7ed;
  font-weight: 600;
  color: #64748b;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.ft-row {
  border-bottom: 1px solid #f1f5f9;
}
.ft-row:last-child { border-bottom: none; }
.ft-row:hover { background: #fafbfc; }
.ft-col-num {
  font-family: "SF Mono", "JetBrains Mono", monospace;
  text-align: right;
  font-feature-settings: "tnum";
  color: #1f2937;
}
.ft-col-name {
  font-weight: 600;
  color: #0f172a;
}
.ft-col-source {
  font-size: 11px;
}
.src-chip {
  display: inline-block;
  font-family: "SF Mono", "JetBrains Mono", monospace;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 4px;
  background: #eef2ff;
  color: #4338ca;
  font-feature-settings: "tnum";
  white-space: nowrap;
}
.ft-rank {
  color: #94a3b8;
  font-family: "SF Mono", "JetBrains Mono", monospace;
  font-size: 11px;
  margin-right: 4px;
}
.ft-col-formula { color: #475569; font-size: 11px; word-break: break-all; }
.mono { font-family: "SF Mono", "JetBrains Mono", monospace; }
.ft-col-tag { text-align: center; }

/* ========= Cross window ========= */
.cross-section {
  margin-top: 36px;
  border: 1px solid #0f172a;
  border-radius: 14px;
  background: #0f172a;
  color: #f1f5f9;
  overflow: hidden;
}
.cross-head {
  padding: 22px 26px;
  border-bottom: 1px solid #1e293b;
}
.cross-kicker {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.2em;
  color: #fbbf24;
}
.cross-title {
  font-size: 22px;
  font-weight: 700;
  margin-top: 4px;
}
.cross-meta {
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
}
.cross-table {
  padding: 8px 0;
}
.ct-head, .ct-row {
  display: grid;
  grid-template-columns: 0.4fr 1.6fr 2.5fr 0.8fr 0.9fr 0.9fr 0.9fr 0.6fr;
  gap: 10px;
  align-items: center;
  padding: 9px 26px;
  font-size: 12px;
}
.ct-head {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #94a3b8;
  border-bottom: 1px solid #1e293b;
}
.ct-row {
  border-bottom: 1px solid #1e293b;
}
.ct-row:last-child { border-bottom: none; }
.ct-row.ct-top { background: rgba(251, 191, 36, 0.05); }
.ct-col-rank {
  font-family: "SF Mono", "JetBrains Mono", monospace;
  color: #fbbf24;
  font-weight: 700;
}
.ct-col-name { font-weight: 600; }
.ct-col-formula {
  color: #94a3b8;
  font-size: 11px;
  word-break: break-all;
}
.ct-col-num {
  font-family: "SF Mono", "JetBrains Mono", monospace;
  text-align: right;
  font-feature-settings: "tnum";
}

/* ========= Misc ========= */
.empty-tip {
  font-size: 12px;
  color: #94a3b8;
  font-style: italic;
}
</style>
