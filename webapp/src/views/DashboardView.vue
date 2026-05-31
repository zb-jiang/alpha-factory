<template>
  <div class="dashboard">
    <el-container>
      <el-header class="app-header">
        <div class="header-left">
          <h1>因子工厂</h1>
        </div>
        <div class="header-right">
          <span class="username">{{ authStore.username }}</span>
          <el-button text @click="handleLogout">退出</el-button>
        </div>
      </el-header>
      <el-main>
        <div class="toolbar">
          <h2>任务看板</h2>
          <el-button type="primary" @click="showCreateDialog = true">
            <el-icon><Plus /></el-icon> 新建任务
          </el-button>
        </div>

        <el-row :gutter="16">
          <el-col v-for="task in tasks" :key="task.id" :xs="24" :sm="12" :md="8" :lg="6">
            <el-card class="task-card" shadow="hover" @click="goToTask(task.id)">
              <template #header>
                <div class="task-card-header">
                  <span class="task-name">{{ task.taskName }}</span>
                  <el-tag :type="statusMap[task.status]?.type" size="small">
                    {{ statusMap[task.status]?.label }}
                  </el-tag>
                </div>
              </template>
              <p class="task-desc">{{ task.taskDesc || '暂无描述' }}</p>
              <div class="task-meta">
                <span v-if="task.currentStep">当前: Step{{ task.currentStep }}</span>
                <span>{{ formatDateTime(task.createdAt) }}</span>
              </div>
            </el-card>
          </el-col>
        </el-row>

        <el-empty v-if="tasks.length === 0 && !loading" description="暂无任务，点击右上角新建" />

        <el-dialog v-model="showCreateDialog" title="新建任务" width="480px">
          <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="80px">
            <el-form-item label="任务名称" prop="taskName">
              <el-input v-model="createForm.taskName" placeholder="如: alpha_mining_001" />
            </el-form-item>
            <el-form-item label="任务描述" prop="taskDesc">
              <el-input v-model="createForm.taskDesc" type="textarea" :rows="3" placeholder="可选" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="showCreateDialog = false">取消</el-button>
            <el-button type="primary" :loading="creating" @click="handleCreate">创建</el-button>
          </template>
        </el-dialog>
      </el-main>
    </el-container>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { listTasks, createTask, deleteTask, type TaskResponse } from '../api/task'
import { formatDateTime, STATUS_MAP } from '../utils/constants'

const router = useRouter()
const authStore = useAuthStore()
const tasks = ref<TaskResponse[]>([])
const loading = ref(false)
const creating = ref(false)
const showCreateDialog = ref(false)
const createFormRef = ref<FormInstance>()
const statusMap = STATUS_MAP

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

function handleLogout() {
  authStore.logout()
  router.push('/login')
}

onMounted(() => {
  fetchTasks()
})
</script>

<style scoped>
.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 24px;
}

.header-left h1 {
  margin: 0;
  font-size: 20px;
  color: #303133;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.username {
  color: #606266;
  font-size: 14px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.toolbar h2 {
  margin: 0;
  font-size: 18px;
}

.task-card {
  margin-bottom: 16px;
  cursor: pointer;
  transition: transform 0.2s;
}

.task-card:hover {
  transform: translateY(-2px);
}

.task-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.task-name {
  font-weight: 600;
  font-size: 15px;
}

.task-desc {
  color: #909399;
  font-size: 13px;
  margin: 0 0 12px 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-meta {
  display: flex;
  justify-content: space-between;
  color: #c0c4cc;
  font-size: 12px;
}
</style>
