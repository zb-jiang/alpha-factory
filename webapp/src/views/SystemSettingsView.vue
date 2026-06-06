<template>
  <AppLayout>
    <div class="settings-page">
      <div class="sb-page-header">
        <h1>系统设置</h1>
        <p>管理数据源凭证和自定义 LLM 供应商</p>
      </div>

      <div class="sb-content">
        <!-- Tushare 凭证 -->
        <div class="sb-section">
          <div class="sb-section-header">
            <el-icon size="20" color="var(--sb-primary)"><DataLine /></el-icon>
            <div>
              <h3>Tushare 数据源</h3>
              <p>Tushare Pro 数据接口凭证，用于获取 A 股行情、财务等数据</p>
            </div>
          </div>

          <el-form label-position="top" class="sb-form" style="max-width: 480px;">
            <el-form-item label="Tushare Pro API Key" required>
              <el-input
                v-model="tushareForm.api_key"
                type="password"
                show-password
                placeholder="请输入您的 Tushare Pro API Key"
                size="large"
              />
              <div class="field-hint">用于获取 A 股行情数据，请在 tushare.pro 注册获取</div>
            </el-form-item>
          </el-form>

          <div class="save-bar">
            <el-button type="primary" size="large" :loading="savingTushare" @click="saveTushare">
              保存 Tushare 设置
            </el-button>
          </div>
        </div>

        <!-- 自定义 LLM 供应商 -->
        <div class="sb-section" style="margin-top: 32px;">
          <div class="sb-section-header">
            <el-icon size="20" color="var(--sb-primary)"><Connection /></el-icon>
            <div>
              <h3>自定义 LLM 供应商</h3>
              <p>添加系统预设之外的 LLM 供应商，任务配置时可与预设供应商合并选择</p>
            </div>
          </div>

          <div class="provider-table-wrapper" v-if="customProviders.length > 0">
            <el-table :data="customProviders" class="sb-table" style="width: 100%">
              <el-table-column label="供应商" min-width="140">
                <template #default="{ row }">
                  <div class="provider-name">{{ row.name }}</div>
                </template>
              </el-table-column>
              <el-table-column label="API 地址" min-width="280">
                <template #default="{ row }">
                  <code class="provider-url">{{ row.base_url }}</code>
                </template>
              </el-table-column>
              <el-table-column label="描述" min-width="200">
                <template #default="{ row }">
                  <span class="provider-desc">{{ row.description }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="100" align="right">
                <template #default="{ $index }">
                  <el-button text size="small" @click="editProvider($index)">
                    <el-icon><Edit /></el-icon>
                  </el-button>
                  <el-button text size="small" type="danger" @click="removeProvider($index)">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <el-empty v-else description="暂无自定义供应商，点击下方按钮添加" :image-size="60" />

          <el-button type="primary" plain @click="addProvider" class="add-btn">
            <el-icon><Plus /></el-icon> 添加自定义供应商
          </el-button>
        </div>
      </div>
    </div>

    <!-- Provider Dialog -->
    <el-dialog v-model="providerDialogVisible" :title="editingProviderIdx >= 0 ? '编辑供应商' : '添加供应商'" width="520px" class="sb-dialog">
      <el-form label-position="top" class="sb-form">
        <el-form-item label="供应商名称" required>
          <el-input v-model="editingProvider.name" placeholder="如 OpenAI、DeepSeek" size="large" />
        </el-form-item>
        <el-form-item label="API 地址" required>
          <el-input v-model="editingProvider.base_url" placeholder="如 https://api.openai.com/v1" size="large" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editingProvider.description" placeholder="如 GPT-4o / GPT-4o-mini 等" size="large" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="providerDialogVisible = false" size="large">取消</el-button>
        <el-button type="primary" size="large" @click="saveProvider">保存</el-button>
      </template>
    </el-dialog>
  </AppLayout>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { DataLine, Connection, Edit, Delete, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getSystemConfig, updateSystemConfigSection, type StructuredConfigResponse } from '../api/config'
import AppLayout from '../components/AppLayout.vue'

const loading = ref(false)
const savingTushare = ref(false)
const config = ref<StructuredConfigResponse | null>(null)

// Tushare
const tushareForm = ref<Record<string, any>>({ api_key: '' })

// 自定义供应商
const customProviders = ref<Array<{ name: string; base_url: string; description: string }>>([])
const providerDialogVisible = ref(false)
const editingProviderIdx = ref(-1)
const editingProvider = ref({ name: '', base_url: '', description: '' })

async function fetchConfig() {
  loading.value = true
  try {
    const res = await getSystemConfig()
    config.value = res.data
    for (const group of res.data.groups || []) {
      if (group.name === 'tushare_credentials') {
        for (const field of group.fields || []) {
          tushareForm.value[field.key] = field.value ?? ''
        }
      }
      if (group.name === 'custom_llm_providers') {
        const providerField = group.fields?.find(f => f.key === 'custom_providers')
        if (providerField?.value) {
          customProviders.value = [...(providerField.value as any)]
        }
      }
    }
  } finally {
    loading.value = false
  }
}

async function saveTushare() {
  savingTushare.value = true
  try {
    await updateSystemConfigSection('tushare_credentials', tushareForm.value)
    ElMessage.success('Tushare 设置已保存')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    savingTushare.value = false
  }
}

function addProvider() {
  editingProviderIdx.value = -1
  editingProvider.value = { name: '', base_url: '', description: '' }
  providerDialogVisible.value = true
}

function editProvider(idx: number) {
  editingProviderIdx.value = idx
  editingProvider.value = { ...customProviders.value[idx] }
  providerDialogVisible.value = true
}

function removeProvider(idx: number) {
  customProviders.value.splice(idx, 1)
  saveProviders()
}

function isValidUrl(str: string) {
  try {
    const url = new URL(str)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function saveProvider() {
  if (!editingProvider.value.name || !editingProvider.value.base_url) {
    ElMessage.warning('请填写供应商名称和API地址')
    return
  }
  if (!isValidUrl(editingProvider.value.base_url)) {
    ElMessage.warning('API 地址格式不正确，请输入有效的 URL（如 https://api.openai.com/v1）')
    return
  }
  if (editingProviderIdx.value >= 0) {
    customProviders.value[editingProviderIdx.value] = { ...editingProvider.value }
  } else {
    customProviders.value.push({ ...editingProvider.value })
  }
  providerDialogVisible.value = false
  saveProviders()
}

async function saveProviders() {
  try {
    await updateSystemConfigSection('custom_llm_providers', customProviders.value as any)
    ElMessage.success('自定义供应商已保存')
  } catch {
    ElMessage.error('保存失败')
  }
}

onMounted(fetchConfig)
</script>

<style scoped>
.field-hint {
  margin-top: 6px;
  font-size: 12px;
  color: var(--sb-text-muted);
  line-height: 1.5;
}

.save-bar {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--sb-border-light);
}

.provider-table-wrapper {
  margin-bottom: 16px;
}

.provider-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--sb-text);
}

.provider-url {
  font-family: 'SF Mono', monospace;
  font-size: 12px;
  color: var(--sb-text-secondary);
  background: var(--sb-bg);
  padding: 4px 8px;
  border-radius: var(--sb-radius-sm);
}

.provider-desc {
  font-size: 13px;
  color: var(--sb-text-secondary);
}

.add-btn {
  margin-top: 8px;
}
</style>
