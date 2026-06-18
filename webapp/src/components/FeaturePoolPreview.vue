<script setup lang="ts">
import { computed, markRaw, ref } from 'vue'
import {
  TrendCharts, RefreshLeft, Lightning, Histogram, View, Coin,
  Discount, Medal, Wallet, FirstAidKit, Money,
} from '@element-plus/icons-vue'

interface Feature {
  name: string
  description?: string
  expr?: string
}

interface Dimension {
  key: string
  label: string
  icon: string
  category: string
  features: Feature[]
}

interface PreviewData {
  dimensions?: Dimension[]
  rawFields?: Feature[]
}

const props = defineProps<{
  data: PreviewData
}>()

const ICON_MAP: Record<string, any> = {
  TrendCharts: markRaw(TrendCharts),
  RefreshLeft: markRaw(RefreshLeft),
  Lightning: markRaw(Lightning),
  Histogram: markRaw(Histogram),
  View: markRaw(View),
  Coin: markRaw(Coin),
  Discount: markRaw(Discount),
  Medal: markRaw(Medal),
  Wallet: markRaw(Wallet),
  FirstAidKit: markRaw(FirstAidKit),
  Money: markRaw(Money),
}

const dimensions = computed(() => props.data?.dimensions || [])
const rawFields = computed(() => props.data?.rawFields || [])
const activeNames = ref<string[]>([])

const categoryClass = (category: string) => {
  return category === '技术面' ? 'tech' : 'fundamental'
}

const totalFeatures = computed(() =>
  dimensions.value.reduce((sum, d) => sum + (d.features?.length || 0), 0)
)
</script>

<template>
  <div class="feature-pool-preview">
    <div class="preview-header">
      <div class="preview-stats">
        <el-tag size="large" type="info" effect="plain">
          原始字段 {{ rawFields.length }} 个
        </el-tag>
        <el-tag size="large" type="info" effect="plain">
          基础特征 {{ totalFeatures }} 个
        </el-tag>
      </div>
    </div>

    <el-collapse v-model="activeNames" class="dimension-collapse">
      <el-collapse-item
        v-for="dim in dimensions"
        :key="dim.key"
        :name="dim.key"
        class="dimension-item"
      >
        <template #title>
          <div class="dimension-title">
            <el-icon class="dimension-icon" :class="categoryClass(dim.category)">
              <component :is="ICON_MAP[dim.icon] || TrendCharts" />
            </el-icon>
            <span class="dimension-label">{{ dim.label }}</span>
            <el-tag size="small" :type="dim.category === '技术面' ? 'primary' : 'success'" effect="light">
              {{ dim.category }}
            </el-tag>
            <span class="dimension-count">{{ dim.features.length }} 个特征</span>
          </div>
        </template>

        <div class="feature-list">
          <div
            v-for="feature in dim.features"
            :key="feature.name"
            class="feature-card"
          >
            <div class="feature-name">{{ feature.name }}</div>
            <div v-if="feature.expr" class="feature-expr">
              <code>{{ feature.expr }}</code>
            </div>
            <div v-if="feature.description" class="feature-desc">
              {{ feature.description }}
            </div>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>

    <el-divider v-if="rawFields.length > 0" content-position="left">原始字段</el-divider>

    <div v-if="rawFields.length > 0" class="raw-field-list">
      <el-tag
        v-for="field in rawFields"
        :key="field.name"
        class="raw-field-tag"
        type="info"
        effect="plain"
        size="large"
      >
        <el-tooltip :content="field.description || field.name" placement="top">
          <span>{{ field.name }}</span>
        </el-tooltip>
      </el-tag>
    </div>
  </div>
</template>

<style scoped>
.feature-pool-preview {
  width: 100%;
}

.preview-header {
  margin-bottom: 16px;
}

.preview-stats {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.dimension-collapse {
  border: none;
}

.dimension-item {
  margin-bottom: 8px;
  border-radius: 8px;
  overflow: hidden;
}

.dimension-title {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}

.dimension-icon {
  font-size: 20px;
}

.dimension-icon.tech {
  color: #409eff;
}

.dimension-icon.fundamental {
  color: #67c23a;
}

.dimension-label {
  font-weight: 600;
  font-size: 15px;
}

.dimension-count {
  margin-left: auto;
  color: #909399;
  font-size: 13px;
}

.feature-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  padding: 12px 0;
}

.feature-card {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px;
  background: #fafafa;
  transition: all 0.2s;
}

.feature-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  border-color: #c0c4cc;
}

.feature-name {
  font-weight: 600;
  color: #303133;
  margin-bottom: 6px;
  font-family: 'Courier New', monospace;
  font-size: 14px;
}

.feature-expr {
  margin-bottom: 6px;
}

.feature-expr code {
  display: block;
  padding: 6px 8px;
  background: #f0f2f5;
  border-radius: 4px;
  color: #606266;
  font-size: 12px;
  word-break: break-all;
  font-family: 'Courier New', monospace;
}

.feature-desc {
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
}

.raw-field-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0;
}

.raw-field-tag {
  cursor: default;
}

:deep(.el-collapse-item__header) {
  padding-left: 12px;
  padding-right: 12px;
  background: #f5f7fa;
  border-radius: 8px;
  font-weight: normal;
}

:deep(.el-collapse-item__wrap) {
  border-radius: 0 0 8px 8px;
}

:deep(.el-collapse-item__content) {
  padding: 0 16px 16px;
}
</style>
