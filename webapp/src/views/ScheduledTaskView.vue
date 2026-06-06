<template>
  <AppLayout>
    <div class="scheduled-task-page">
      <!-- Page Header -->
      <div class="sb-page-header">
        <div class="header-row">
          <div>
            <h1>计划任务</h1>
            <p>配置定时自动运行的因子挖掘任务</p>
          </div>
          <el-button type="primary" size="large" @click="showCreateDialog = true">
            <el-icon><Plus /></el-icon>
            <span>新建计划</span>
          </el-button>
        </div>
      </div>

      <!-- Content -->
      <div class="sb-content">
        <div class="sb-section" style="padding: 0; overflow: hidden;">
          <el-table :data="tasks" class="sb-table" v-loading="loading" style="width: 100%">
            <el-table-column label="计划名称" min-width="160">
              <template #default="{ row }">
                <div class="task-name-cell">
                  <div class="task-name">{{ row.name }}</div>
                  <div class="task-desc">{{ row.description || '无描述' }}</div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="源任务" min-width="140">
              <template #default="{ row }">
                <el-tag size="small" effect="light">{{ row.sourceTaskName }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Cron 表达式" width="160">
              <template #default="{ row }">
                <code class="cron-code">{{ row.cronExpression }}</code>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-switch
                  v-model="row.enabled"
                  @change="(val: boolean) => handleToggle(row, val)"
                  inline-prompt
                  active-text="启用"
                  inactive-text="停用"
                  style="--el-switch-on-color: var(--sb-primary);"
                />
              </template>
            </el-table-column>
            <el-table-column label="上次执行" width="160">
              <template #default="{ row }">
                <span class="time-text">{{ formatDateTime(row.lastRunAt) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="下次执行" width="160">
              <template #default="{ row }">
                <span class="time-text">{{ formatDateTime(row.nextRunAt) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160" align="right">
              <template #default="{ row }">
                <el-button text size="small" @click="handleTrigger(row.id)">
                  <el-icon><VideoPlay /></el-icon>
                </el-button>
                <el-button text size="small" @click="handleEdit(row)">
                  <el-icon><Edit /></el-icon>
                </el-button>
                <el-button text size="small" type="danger" @click="handleDelete(row.id)">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-empty v-if="tasks.length === 0 && !loading" description="暂无计划任务" />
        </div>
      </div>
    </div>

    <!-- Create/Edit Dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑计划任务' : '新建计划任务'"
      width="560px"
      class="sb-dialog"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="100px"
        label-position="top"
        class="sb-form"
      >
        <el-form-item label="计划名称" prop="name">
          <el-input v-model="form.name" placeholder="如：每日凌晨自动挖掘" size="large" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="可选" size="large" />
        </el-form-item>
        <el-form-item label="源任务" prop="sourceTaskId">
          <el-select v-model="form.sourceTaskId" placeholder="选择作为模板的任务" size="large" style="width: 100%">
            <el-option
              v-for="task in availableTasks"
              :key="task.id"
              :label="task.taskName"
              :value="task.id"
            />
          </el-select>
          <div class="field-hint">定时触发时会复制该任务的配置创建新任务并运行</div>
        </el-form-item>
        <el-form-item label="Cron 表达式" prop="cronExpression">
          <el-input v-model="form.cronExpression" placeholder="如：0 0 2 * * ? （每天凌晨2点）" size="large" />
          <div class="field-hint">
            标准 Cron 表达式，
            <el-link type="primary" href="https://cron.qqe2.com/" target="_blank" :underline="false">在线生成工具</el-link>
          </div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false" size="large">取消</el-button>
        <el-button type="primary" size="large" :loading="saving" @click="handleSave">
          {{ editingId ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>
  </AppLayout>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Plus, VideoPlay, Edit, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'
import {
  listScheduledTasks, createScheduledTask, updateScheduledTask,
  deleteScheduledTask, toggleScheduledTask, triggerScheduledTask,
  type ScheduledTaskResponse, type ScheduledTaskRequest,
} from '../api/scheduled-task'
import { listTasks, type TaskResponse } from '../api/task'
import { formatDateTime } from '../utils/constants'
import AppLayout from '../components/AppLayout.vue'

const tasks = ref<ScheduledTaskResponse[]>([])
const availableTasks = ref<TaskResponse[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const formRef = ref<FormInstance>()

const form = reactive<ScheduledTaskRequest>({
  name: '',
  description: '',
  sourceTaskId: 0,
  cronExpression: '',
  enabled: true,
})

const rules = {
  name: [{ required: true, message: '请输入计划名称', trigger: 'blur' }],
  sourceTaskId: [{ required: true, message: '请选择源任务', trigger: 'change', type: 'number' }],
  cronExpression: [{ required: true, message: '请输入 Cron 表达式', trigger: 'blur' }],
}

async function fetchTasks() {
  loading.value = true
  try {
    const res = await listScheduledTasks()
    tasks.value = (res as any).data
  } finally {
    loading.value = false
  }
}

async function fetchAvailableTasks() {
  try {
    const res = await listTasks()
    availableTasks.value = (res as any).data
  } catch { /* ignore */ }
}

function handleEdit(row: ScheduledTaskResponse) {
  editingId.value = row.id
  form.name = row.name
  form.description = row.description || ''
  form.sourceTaskId = row.sourceTaskId
  form.cronExpression = row.cronExpression
  form.enabled = row.enabled
  dialogVisible.value = true
}

async function handleSave() {
  await formRef.value?.validate()
  saving.value = true
  try {
    if (editingId.value) {
      await updateScheduledTask(editingId.value, { ...form })
      ElMessage.success('计划任务已更新')
    } else {
      await createScheduledTask({ ...form })
      ElMessage.success('计划任务已创建')
    }
    dialogVisible.value = false
    resetForm()
    await fetchTasks()
  } catch {
    ElMessage.error('操作失败')
  } finally {
    saving.value = false
  }
}

async function handleDelete(id: number) {
  try {
    await ElMessageBox.confirm('确定删除此计划任务？', '确认删除', { type: 'warning' })
    await deleteScheduledTask(id)
    ElMessage.success('已删除')
    await fetchTasks()
  } catch {
    // cancelled
  }
}

async function handleToggle(row: ScheduledTaskResponse, enabled: boolean) {
  try {
    await toggleScheduledTask(row.id, enabled)
    ElMessage.success(enabled ? '计划任务已启用' : '计划任务已停用')
    await fetchTasks()
  } catch {
    row.enabled = !enabled
    ElMessage.error('操作失败')
  }
}

async function handleTrigger(id: number) {
  try {
    await triggerScheduledTask(id)
    ElMessage.success('已手动触发')
  } catch {
    ElMessage.error('触发失败')
  }
}

function resetForm() {
  editingId.value = null
  form.name = ''
  form.description = ''
  form.sourceTaskId = 0
  form.cronExpression = ''
  form.enabled = true
}

onMounted(() => {
  fetchTasks()
  fetchAvailableTasks()
})
</script>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.task-name-cell {
  display: flex;
  flex-direction: column;
}

.task-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--sb-text);
}

.task-desc {
  font-size: 12px;
  color: var(--sb-text-muted);
  margin-top: 2px;
}

.cron-code {
  font-family: 'SF Mono', monospace;
  font-size: 12px;
  color: var(--sb-primary-dark);
  background: var(--sb-primary-light);
  padding: 4px 8px;
  border-radius: var(--sb-radius-sm);
}

.time-text {
  font-size: 13px;
  color: var(--sb-text-secondary);
}

.field-hint {
  margin-top: 6px;
  font-size: 12px;
  color: var(--sb-text-muted);
}
</style>
