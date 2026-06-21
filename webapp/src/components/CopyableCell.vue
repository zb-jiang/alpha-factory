<script setup lang="ts">
/**
 * 可复制单元格：单行省略 + 鼠标悬停显示复制小图标 + 点击复制 + 复制成功提示。
 * 用于因子表的"因子名"/"公式"等长文本列。
 *
 * 用法：
 *   <CopyableCell :value="f.formula" class="ft-col-formula mono" />
 *   <CopyableCell :value="f.factor_name" class="ft-col-name">
 *     <span class="ft-rank">{{ idx + 1 }}.</span> {{ f.factor_name }}
 *   </CopyableCell>
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { DocumentCopy, Check } from '@element-plus/icons-vue'

const props = withDefaults(defineProps<{
  /** 要复制的原始文本。也作为 title tooltip 的内容。 */
  value: string | number | null | undefined
  /** 复制成功后的提示文案。 */
  successText?: string
  /** 暗色背景下使用（图标色 / 复制成功的反馈色） */
  dark?: boolean
}>(), {
  successText: '已复制',
  dark: false,
})

const copied = ref(false)
let resetTimer: ReturnType<typeof setTimeout> | null = null

async function handleCopy(e: MouseEvent) {
  e.stopPropagation()
  const text = props.value == null ? '' : String(props.value)
  if (!text) return
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      // 兼容非 https / localhost
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    copied.value = true
    ElMessage.success(props.successText)
    if (resetTimer) clearTimeout(resetTimer)
    resetTimer = setTimeout(() => { copied.value = false }, 1200)
  } catch {
    ElMessage.error('复制失败')
  }
}
</script>

<template>
  <span class="copyable-cell" :class="{ 'copyable-dark': dark }" :title="String(value ?? '')">
    <span class="copyable-content">
      <slot>{{ value }}</slot>
    </span>
    <button
      v-if="value !== null && value !== undefined && String(value) !== ''"
      class="copy-btn"
      :class="{ 'copy-done': copied }"
      type="button"
      @click="handleCopy"
      @mousedown.stop
      :aria-label="copied ? '已复制' : '复制'"
    >
      <el-icon :size="12">
        <Check v-if="copied" />
        <DocumentCopy v-else />
      </el-icon>
    </button>
  </span>
</template>

<style scoped>
.copyable-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  min-width: 0; /* 让子元素的 ellipsis 在 grid/flex 内生效 */
}
.copyable-content {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 复制按钮：默认隐藏，cell 或父行 hover 时浮现。
   父行 hover 的联动由父组件控制（父组件给 .ft-row:hover .copy-btn 等设置 opacity:1）。 */
.copy-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: #94a3b8;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, background 0.15s ease, color 0.15s ease;
  padding: 0;
}
.copyable-cell:hover .copy-btn {
  opacity: 1;
}
.copy-btn:hover {
  background: rgba(59, 130, 246, 0.1);
  color: #1d4ed8;
}
.copy-btn.copy-done {
  opacity: 1;
  color: #15803d;
  background: rgba(34, 197, 94, 0.12);
}
/* 暗色背景版本（用于 final/cross 卡内） */
.copyable-dark .copy-btn {
  color: #64748b;
}
.copyable-dark .copy-btn:hover {
  background: rgba(251, 191, 36, 0.12);
  color: #fbbf24;
}
.copyable-dark .copy-btn.copy-done {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.12);
}
</style>
