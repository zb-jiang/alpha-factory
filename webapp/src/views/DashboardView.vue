<template>
  <AppLayout>
    <div class="dashboard">
      <!-- Page Header -->
      <div class="sb-page-header">
        <div class="header-row">
          <div>
            <h1>任务看板</h1>
            <p>管理和监控您的因子挖掘任务</p>
          </div>
          <el-button type="primary" size="large" @click="showCreateDialog = true">
            <el-icon><Plus /></el-icon>
            <span>新建任务</span>
          </el-button>
        </div>
      </div>

      <!-- Content -->
      <div class="sb-content">
        <!-- Stats Row -->
        <el-row :gutter="16" class="stats-row">
          <el-col :span="6">
            <div class="stat-card">
              <div class="stat-value">{{ tasks.length }}</div>
              <div class="stat-label">总任务数</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-card">
              <div class="stat-value stat-running">{{ runningCount }}</div>
              <div class="stat-label">运行中</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-card">
              <div class="stat-value stat-completed">{{ completedCount }}</div>
              <div class="stat-label">已完成</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-card">
              <div class="stat-value stat-new">{{ newCount }}</div>
              <div class="stat-label">待启动</div>
            </div>
          </el-col>
        </el-row>

        <!-- Task Grid -->
        <div class="section-title-row">
          <h2>全部任务</h2>
          <span class="section-count">{{ tasks.length }} 个任务</span>
        </div>

        <el-row :gutter="16">
          <el-col v-for="task in tasks" :key="task.id" :xs="24" :sm="12" :md="8" :lg="6" :xl="6">
            <div class="task-card sb-card">
              <div class="task-card-header">
                <div class="task-name">{{ task.taskName }}</div>
                <el-tag
                  size="small"
                  :class="statusMap[task.status]?.cssClass"
                  effect="light"
                >
                  {{ statusMap[task.status]?.label }}
                </el-tag>
              </div>
              <p class="task-desc">{{ task.taskDesc || '暂无描述' }}</p>
              <div class="task-meta">
                <span v-if="task.currentStep" class="task-step">Step {{ task.currentStep }}</span>
                <span class="task-date">{{ formatDateTime(task.createdAt) }}</span>
              </div>
              <div class="task-actions">
                <el-button size="small" text @click="goToTask(task.id)">
                  <el-icon><View /></el-icon> 详情
                </el-button>
                <el-button size="small" text type="primary" @click="goToConfig(task.id)"
                  :disabled="task.status !== 'NEW'">
                  <el-icon><Setting /></el-icon> 配置
                </el-button>
              </div>
            </div>
          </el-col>
        </el-row>

        <el-empty v-if="tasks.length === 0 && !loading" description="暂无任务，点击右上角新建" />
      </div>
    </div>

    <!-- Create Dialog -->
    <el-dialog v-model="showCreateDialog" title="新建任务" width="520px" class="sb-dialog">
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="90px">
        <el-form-item label="任务名称" prop="taskName">
          <el-input v-model="createForm.taskName" placeholder="如: alpha_mining_001" size="large" />
        </el-form-item>
        <el-form-item label="任务描述" prop="taskDesc">
          <el-input v-model="createForm.taskDesc" type="textarea" :rows="3" placeholder="可选" size="large" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false" size="large">取消</el-button>
        <el-button type="primary" size="large" :loading="creating" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </AppLayout>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, View, Setting } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { listTasks, createTask, type TaskResponse } from '../api/task'
import { formatDateTime, STATUS_MAP } from '../utils/constants'
import AppLayout from '../components/AppLayout.vue'

const router = useRouter()
const authStore = useAuthStore()
const tasks = ref<TaskResponse[]>([])
const loading = ref(false)
const creating = ref(false)
const showCreateDialog = ref(false)
const createFormRef = ref<FormInstance>()
const statusMap = STATUS_MAP

const runningCount = computed(() => tasks.value.filter(t => t.status === 'RUNNING').length)
const completedCount = computed(() => tasks.value.filter(t => t.status === 'COMPLETED').length)
const newCount = computed(() => tasks.value.filter(t => t.status === 'NEW').length)

const createForm = reactive({
  taskName: '',
  taskDesc: '',
})

const createRules = {
  taskName: [{ required: true, message: '请输入任务名称', trigger: 'blur' }],
}

async function fetchTasks() {
  loading.value = true
  try {
    const res = await listTasks()
    tasks.value = res.data
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  await createFormRef.value?.validate()
  creating.value = true
  try {
    await createTask({ taskName: createForm.taskName, taskDesc: createForm.taskDesc })
    ElMessage.success('任务创建成功')
    showCreateDialog.value = false
    createForm.taskName = ''
    createForm.taskDesc = ''
    await fetchTasks()
  } finally {
    creating.value = false
  }
}

function goToTask(taskId: number) {
  router.push({ name: 'TaskDetail', params: { taskId } })
}

function goToConfig(taskId: number) {
  router.push({ name: 'TaskConfig', params: { taskId } })
}

onMounted(() => {
  fetchTasks()
})
</script>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.stats-row {
  margin-bottom: 32px;
}

.stat-card {
  background: var(--sb-surface);
  border-radius: var(--sb-radius-lg);
  border: 1px solid var(--sb-border-light);
  padding: 24px;
  text-align: center;
  transition: var(--sb-transition);
}

.stat-card:hover {
  box-shadow: var(--sb-shadow-md);
  transform: translateY(-1px);
}

.stat-value {
  font-size: 32px;
  font-weight: 700;
  color: var(--sb-text);
  letter-spacing: -1px;
  line-height: 1;
  margin-bottom: 8px;
}

.stat-running { color: var(--sb-primary); }
.stat-completed { color: #10B981; }
.stat-new { color: var(--sb-info); }

.stat-label {
  font-size: 13px;
  color: var(--sb-text-secondary);
  font-weight: 500;
}

.section-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.section-title-row h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}

.section-count {
  font-size: 13px;
  color: var(--sb-text-muted);
}

.task-card {
  padding: 20px;
  margin-bottom: 16px;
  cursor: default;
}

.task-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.task-name {
  font-weight: 600;
  font-size: 15px;
  color: var(--sb-text);
  word-break: break-all;
  padding-right: 8px;
}

.task-desc {
  color: var(--sb-text-secondary);
  font-size: 13px;
  margin: 0 0 16px 0;
  line-height: 1.5;
  min-height: 20px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.task-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.task-step {
  font-size: 12px;
  color: var(--sb-primary);
  background: var(--sb-primary-light);
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 500;
}

.task-date {
  font-size: 12px;
  color: var(--sb-text-muted);
}

.task-actions {
  display: flex;
  gap: 4px;
  padding-top: 12px;
  border-top: 1px solid var(--sb-border-light);
}
</style>
