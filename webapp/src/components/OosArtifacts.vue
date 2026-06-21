<script setup lang="ts">
/**
 * 样本外回测 (OOS) 产物展示面板。
 *
 * 视觉设计完全沿用因子挖掘 TAB 的 editorial 风格（TrainingArtifacts.vue）：
 *   - 顶部蓝色 kicker + Source Han Sans 大标题 + 统一 stats block
 *   - 章节用 "01 标题 ... 元信息" 的格式
 *   - 浅色因子表格（同样的 ft-head / ft-row 网格）
 *   - 末尾暗色 Top3 卡（同 cross-section）
 *
 * 注意：step11 不执行 step02/step03，因此**没有** OOS 区间的特征体检 / 市场环境数据。
 */
import { computed } from 'vue'
import CopyableCell from './CopyableCell.vue'

const props = defineProps<{
  artifacts: {
    status?: string
    factors?: Array<Record<string, any>>            // 通过回测的因子（来自 final_score.csv）
    top3?: Array<Record<string, any>>
    period?: { test_start_date?: string; test_end_date?: string }
    input_factor_count?: number
    input_factors?: Array<Record<string, any>>      // OOS 输入因子清单（factors_validated.json 全量）
  } | null
}>()

const factors = computed(() => props.artifacts?.factors || [])
const top3 = computed(() => props.artifacts?.top3 || [])
const period = computed(() => props.artifacts?.period || {})
const inputCount = computed(() => props.artifacts?.input_factor_count ?? 0)
const inputFactors = computed(() => props.artifacts?.input_factors || [])

const hasArtifacts = computed(() =>
  factors.value.length > 0 ||
  top3.value.length > 0 ||
  inputFactors.value.length > 0 ||
  !!period.value.test_start_date
)

const passedCount = computed(() => factors.value.length)
const noFactorPassed = computed(() => inputCount.value > 0 && passedCount.value === 0)

const periodText = computed(() => {
  const s = period.value.test_start_date
  const e = period.value.test_end_date
  if (!s || !e) return 'NA'
  return `${s} ~ ${e}`
})

// ------------- 格式化工具（与 TrainingArtifacts 完全一致） -------------
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
</script>

<template>
  <div v-if="hasArtifacts" class="ta-root">
    <!-- 顶部：全局摘要（与因子挖掘 TAB 完全相同的 header 结构） -->
    <header class="ta-header">
      <div class="ta-kicker">Out-of-Sample Report</div>
      <h2 class="ta-title">样本外回测概览</h2>
      <div class="ta-stats">
        <div class="stat-block stat-period-block">
          <div class="stat-period mono">{{ periodText }}</div>
          <div class="stat-label">测试区间</div>
        </div>
        <div class="stat-divider"></div>
        <div class="stat-block">
          <div class="stat-num">{{ inputCount }}</div>
          <div class="stat-label">输入因子</div>
        </div>
        <div class="stat-divider"></div>
        <div class="stat-block">
          <div class="stat-num" :class="{ 'stat-warn': noFactorPassed, 'stat-pass': !noFactorPassed && passedCount > 0 }">{{ passedCount }}</div>
          <div class="stat-label">通过回测</div>
        </div>
      </div>
    </header>

    <!-- 空状态提示（淡琥珀色，与挖掘 TAB 的 banner 风格协调） -->
    <div v-if="noFactorPassed" class="oos-empty-banner">
      <el-icon size="18" color="#b45309"><svg viewBox="0 0 1024 1024"><path fill="currentColor" d="M512 64L64 928h896L512 64zm0 224l320 576H192l320-576zm-32 192v192h64V480h-64zm0 256v64h64v-64h-64z"/></svg></el-icon>
      <div class="empty-text">
        <div class="empty-title">没有因子在 OOS 区间通过回测</div>
        <div class="empty-desc">接收了 <b>{{ inputCount }}</b> 个训练阶段筛选出的优秀因子，但全部在样本外区间未能通过回测筛选（可能是覆盖率不足、IC 全部反向或显著性不达标）。下方"OOS 测试因子清单"展示了具体送测的因子。</div>
      </div>
    </div>

    <!-- 子节 1：OOS 测试因子清单（永远显示，只要有 factors_validated.json） -->
    <section class="ta-section">
      <div class="sub-title">
        <span class="sub-num">01</span>
        <span>OOS 测试因子清单</span>
        <span class="sub-meta">送入 step11 的 {{ inputFactors.length }} 个因子</span>
      </div>
      <div v-if="inputFactors.length" class="factor-table">
        <div class="ft-head">
          <span class="ft-col-name">因子</span>
          <span class="ft-col-formula">公式</span>
          <span class="ft-col-tag">LLM</span>
          <span class="ft-col-tag">实证</span>
          <span class="ft-col-reason">来源 / 说明</span>
        </div>
        <div v-for="(f, idx) in inputFactors" :key="'inp-' + idx" class="ft-row ft-row-narrow">
          <CopyableCell class="ft-col-name" :value="fmtStr(f.factor_name)">
            <span class="ft-rank">{{ idx + 1 }}.</span> {{ fmtStr(f.factor_name) }}
          </CopyableCell>
          <CopyableCell class="ft-col-formula mono" :value="fmtStr(f.formula)" />
          <span class="ft-col-tag"><span class="dir-tag" :class="fmtDirection(f.llm_direction).cls">{{ fmtDirection(f.llm_direction).label }}</span></span>
          <span class="ft-col-tag"><span class="dir-tag" :class="fmtDirection(f.empirical_direction).cls">{{ fmtDirection(f.empirical_direction).label }}</span></span>
          <span class="ft-col-reason" :title="fmtStr(f.reason || f.risk)">{{ fmtStr(f.reason || f.risk) }}</span>
        </div>
      </div>
      <div v-else class="empty-tip">尚未生成 factors_validated.json</div>
    </section>

    <!-- 子节 2：OOS 因子指标（按 total_score 排序） -->
    <section class="ta-section">
      <div class="sub-title">
        <span class="sub-num">02</span>
        <span>OOS 因子指标</span>
        <span class="sub-meta">{{ factors.length }} 个通过回测筛选</span>
      </div>
      <div v-if="factors.length" class="factor-table">
        <div class="ft-head ft-head-9">
          <span class="ft-col-name">因子</span>
          <span class="ft-col-formula">公式</span>
          <span class="ft-col-num">rankIC</span>
          <span class="ft-col-num">rankIR</span>
          <span class="ft-col-num">obs</span>
          <span class="ft-col-num">annRet</span>
          <span class="ft-col-num">total</span>
          <span class="ft-col-tag">LLM</span>
          <span class="ft-col-tag">实证</span>
        </div>
        <div v-for="(f, idx) in factors" :key="'oos-' + idx" class="ft-row ft-row-9">
          <CopyableCell class="ft-col-name" :value="fmtStr(f.factor_name)">
            <span class="ft-rank">{{ idx + 1 }}.</span> {{ fmtStr(f.factor_name) }}
          </CopyableCell>
          <CopyableCell class="ft-col-formula mono" :value="fmtStr(f.formula)" />
          <span class="ft-col-num">{{ fmtNum(f.mean_rank_ic, 4) }}</span>
          <span class="ft-col-num">{{ fmtNum(f.rank_ic_ir, 3) }}</span>
          <span class="ft-col-num">{{ fmtInt(f.observation_count) }}</span>
          <span class="ft-col-num">{{ fmtPercent(f.annualized_return, 2) }}</span>
          <span class="ft-col-num">{{ fmtNum(f.total_score, 3) }}</span>
          <span class="ft-col-tag"><span class="dir-tag" :class="fmtDirection(f.llm_direction).cls">{{ fmtDirection(f.llm_direction).label }}</span></span>
          <span class="ft-col-tag"><span class="dir-tag" :class="fmtDirection(f.empirical_direction).cls">{{ fmtDirection(f.empirical_direction).label }}</span></span>
        </div>
      </div>
      <div v-else class="empty-tip">在该测试区间未筛选出任何通过回测的因子</div>
    </section>

    <!-- 子节 3：OOS Top3（暗色卡，同因子挖掘的"跨窗口汇总"风格） -->
    <section v-if="top3.length" class="cross-section">
      <div class="cross-head">
        <div class="cross-kicker">FINAL · TOP 3</div>
        <div class="cross-title">OOS 优胜因子</div>
        <div class="cross-meta">在样本外测试区间 {{ periodText }} 表现最优的 Top3</div>
      </div>
      <div class="cross-table">
        <div class="ct-head">
          <span class="ct-col-rank">#</span>
          <span class="ct-col-name">因子</span>
          <span class="ct-col-formula">公式</span>
          <span class="ct-col-num">rankIC</span>
          <span class="ct-col-num">rankIR</span>
          <span class="ct-col-num">annRet</span>
          <span class="ct-col-num">obs</span>
          <span class="ct-col-num">total</span>
        </div>
        <div v-for="(f, idx) in top3" :key="'top-' + idx" class="ct-row" :class="{ 'ct-top': idx < 1 }">
          <span class="ct-col-rank">{{ idx + 1 }}</span>
          <CopyableCell class="ct-col-name" :value="fmtStr(f.factor_name)" dark />
          <CopyableCell class="ct-col-formula mono" :value="fmtStr(f.formula)" dark />
          <span class="ct-col-num">{{ fmtNum(f.mean_rank_ic, 4) }}</span>
          <span class="ct-col-num">{{ fmtNum(f.rank_ic_ir, 3) }}</span>
          <span class="ct-col-num">{{ fmtPercent(f.annualized_return, 2) }}</span>
          <span class="ct-col-num">{{ fmtInt(f.observation_count) }}</span>
          <span class="ct-col-num">{{ fmtNum(f.total_score, 3) }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* === 完全沿用 TrainingArtifacts 的 token === */
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
.stat-num.stat-warn { color: #b91c1c; }
.stat-num.stat-pass { color: #15803d; }
.stat-block .stat-label {
  font-size: 12px;
  color: #64748b;
  margin-top: 2px;
}
.stat-period-block .stat-period {
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.2;
}
.stat-divider {
  width: 1px;
  height: 32px;
  background: #cbd5e1;
}

/* ========= Empty banner（柔和琥珀色，不喧宾夺主） ========= */
.oos-empty-banner {
  margin-top: 16px;
  display: flex;
  gap: 12px;
  padding: 14px 18px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 10px;
  align-items: flex-start;
}
.empty-title {
  font-size: 13px;
  font-weight: 700;
  color: #92400e;
  margin-bottom: 3px;
}
.empty-desc {
  font-size: 12px;
  line-height: 1.7;
  color: #78350f;
}
.empty-desc b {
  color: #92400e;
  font-weight: 700;
}

/* ========= Section（与挖掘 TAB 的 sub-section 相同） ========= */
.ta-section {
  margin-top: 28px;
  border: 1px solid #e4e7ed;
  border-radius: 14px;
  background: #fff;
  padding: 18px 22px 22px;
}
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

/* ========= Factor table（完全同 TrainingArtifacts.ft-head/ft-row） ========= */
.factor-table {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow-x: auto;
  overflow-y: hidden;
}
.factor-table::-webkit-scrollbar { height: 8px; }
.factor-table::-webkit-scrollbar-track { background: #f8fafc; }
.factor-table::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
.factor-table::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

.ft-head, .ft-row {
  display: grid;
  /* 输入清单：因子 | 公式 | LLM | 实证 | 来源说明 */
  grid-template-columns:
    minmax(180px, 1.4fr)
    minmax(280px, 2.4fr)
    minmax(70px, 0.5fr)
    minmax(70px, 0.5fr)
    minmax(180px, 1.6fr);
  min-width: 880px;
  gap: 10px;
  align-items: center;
  padding: 9px 14px;
  font-size: 12px;
}
.ft-head-9, .ft-row-9 {
  /* OOS 因子指标表：因子 | 公式 | rankIC | rankIR | obs | annRet | total | LLM | 实证 */
  grid-template-columns:
    minmax(180px, 1.4fr)
    minmax(260px, 2.2fr)
    minmax(70px, 0.6fr)
    minmax(70px, 0.6fr)
    minmax(60px, 0.5fr)
    minmax(80px, 0.6fr)
    minmax(70px, 0.5fr)
    minmax(70px, 0.5fr)
    minmax(70px, 0.5fr);
  min-width: 1020px;
}
.ft-head {
  background: #f8fafc;
  border-bottom: 1px solid #e4e7ed;
  font-weight: 600;
  color: #64748b;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  position: sticky;
  top: 0;
  z-index: 1;
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
  white-space: nowrap;
}
.ft-col-name {
  font-weight: 600;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ft-rank {
  color: #94a3b8;
  font-family: "SF Mono", "JetBrains Mono", monospace;
  font-size: 11px;
  margin-right: 4px;
}
.ft-col-formula {
  color: #475569;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: "SF Mono", "JetBrains Mono", monospace;
}
.mono { font-family: "SF Mono", "JetBrains Mono", monospace; }
.ft-col-tag { text-align: center; white-space: nowrap; }
.ft-col-reason {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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

/* ========= Cross section / Top3 暗色卡（完全同 TrainingArtifacts） ========= */
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
  overflow-x: auto;
}
.cross-table::-webkit-scrollbar { height: 8px; }
.cross-table::-webkit-scrollbar-track { background: #1e293b; }
.cross-table::-webkit-scrollbar-thumb { background: #475569; border-radius: 4px; }
.cross-table::-webkit-scrollbar-thumb:hover { background: #64748b; }

.ct-head, .ct-row {
  display: grid;
  grid-template-columns:
    minmax(40px, 0.3fr)
    minmax(180px, 1.4fr)
    minmax(280px, 2.2fr)
    minmax(80px, 0.6fr)
    minmax(80px, 0.6fr)
    minmax(80px, 0.6fr)
    minmax(60px, 0.5fr)
    minmax(70px, 0.6fr);
  min-width: 980px;
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
  white-space: nowrap;
}
.ct-col-name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ct-col-formula {
  color: #94a3b8;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: "SF Mono", "JetBrains Mono", monospace;
}
.ct-col-num {
  font-family: "SF Mono", "JetBrains Mono", monospace;
  text-align: right;
  font-feature-settings: "tnum";
  white-space: nowrap;
}

/* ========= Misc ========= */
.empty-tip {
  font-size: 12px;
  color: #94a3b8;
  font-style: italic;
}
</style>
