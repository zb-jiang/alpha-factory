package com.factorfactory.webapp.service;

import com.factorfactory.webapp.dto.ScheduledTaskRequest;
import com.factorfactory.webapp.dto.ScheduledTaskResponse;
import com.factorfactory.webapp.dto.TaskCreateRequest;
import com.factorfactory.webapp.entity.ScheduledTask;
import com.factorfactory.webapp.entity.Task;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.repository.ScheduledTaskRepository;
import com.factorfactory.webapp.repository.TaskConfigRepository;
import com.factorfactory.webapp.repository.TaskRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.event.ContextRefreshedEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.TaskScheduler;
import org.springframework.scheduling.support.CronTrigger;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ScheduledFuture;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class ScheduledTaskService {

    private final ScheduledTaskRepository scheduledTaskRepository;
    private final TaskRepository taskRepository;
    private final TaskConfigRepository taskConfigRepository;
    private final TaskService taskService;
    private final ExecutionService executionService;
    private final TaskScheduler taskScheduler;

    // 保存已注册的定时任务，key=scheduledTaskId
    private final Map<Long, ScheduledFuture<?>> scheduledFutures = new ConcurrentHashMap<>();

    /**
     * 应用启动后，加载所有启用的计划任务
     */
    @EventListener(ContextRefreshedEvent.class)
    public void onApplicationStart() {
        log.info("Loading scheduled tasks...");
        List<ScheduledTask> tasks = scheduledTaskRepository.findByEnabledTrue();
        for (ScheduledTask st : tasks) {
            register(st);
        }
        log.info("Registered {} scheduled tasks", tasks.size());
    }

    public List<ScheduledTaskResponse> listTasks(Long userId) {
        return scheduledTaskRepository.findByUserIdOrderByCreatedAtDesc(userId).stream()
                .map(this::toResponse)
                .collect(Collectors.toList());
    }

    @Transactional
    public ScheduledTaskResponse createTask(Long userId, ScheduledTaskRequest request) {
        Task sourceTask = taskRepository.findById(request.getSourceTaskId())
                .orElseThrow(() -> new BusinessException("源任务不存在"));
        if (!sourceTask.getUserId().equals(userId)) {
            throw new BusinessException(403, "无权访问源任务");
        }

        ScheduledTask st = ScheduledTask.builder()
                .userId(userId)
                .name(request.getName())
                .description(request.getDescription())
                .sourceTaskId(request.getSourceTaskId())
                .cronExpression(request.getCronExpression())
                .enabled(request.isEnabled())
                .build();

        st = scheduledTaskRepository.save(st);

        if (st.isEnabled()) {
            register(st);
        }

        return toResponse(st);
    }

    @Transactional
    public ScheduledTaskResponse updateTask(Long userId, Long id, ScheduledTaskRequest request) {
        ScheduledTask st = findTaskBelongsToUser(userId, id);

        Task sourceTask = taskRepository.findById(request.getSourceTaskId())
                .orElseThrow(() -> new BusinessException("源任务不存在"));
        if (!sourceTask.getUserId().equals(userId)) {
            throw new BusinessException(403, "无权访问源任务");
        }

        // 取消旧的任务注册
        cancel(st.getId());

        st.setName(request.getName());
        st.setDescription(request.getDescription());
        st.setSourceTaskId(request.getSourceTaskId());
        st.setCronExpression(request.getCronExpression());
        st.setEnabled(request.isEnabled());

        st = scheduledTaskRepository.save(st);

        if (st.isEnabled()) {
            register(st);
        }

        return toResponse(st);
    }

    @Transactional
    public void deleteTask(Long userId, Long id) {
        ScheduledTask st = findTaskBelongsToUser(userId, id);
        cancel(st.getId());
        scheduledTaskRepository.delete(st);
    }

    @Transactional
    public ScheduledTaskResponse toggleEnabled(Long userId, Long id, boolean enabled) {
        ScheduledTask st = findTaskBelongsToUser(userId, id);
        st.setEnabled(enabled);
        st = scheduledTaskRepository.save(st);

        if (enabled) {
            register(st);
        } else {
            cancel(st.getId());
        }

        return toResponse(st);
    }

    /**
     * 手动触发一次计划任务
     */
    @Transactional
    public void triggerOnce(Long userId, Long id) {
        ScheduledTask st = findTaskBelongsToUser(userId, id);
        execute(st);
    }

    private ScheduledTask findTaskBelongsToUser(Long userId, Long id) {
        ScheduledTask st = scheduledTaskRepository.findById(id)
                .orElseThrow(() -> new BusinessException(404, "计划任务不存在"));
        if (!st.getUserId().equals(userId)) {
            throw new BusinessException(403, "无权访问此计划任务");
        }
        return st;
    }

    /**
     * 注册定时任务到调度器
     */
    private void register(ScheduledTask st) {
        cancel(st.getId());
        try {
            CronTrigger trigger = new CronTrigger(st.getCronExpression());
            ScheduledFuture<?> future = taskScheduler.schedule(() -> execute(st), trigger);
            scheduledFutures.put(st.getId(), future);
            st.setNextRunAt(calculateNextRunTime(st.getCronExpression()));
            scheduledTaskRepository.save(st);
            log.info("Registered scheduled task: id={}, name={}, cron={}", st.getId(), st.getName(), st.getCronExpression());
        } catch (Exception e) {
            log.error("Failed to register scheduled task: id={}, cron={}", st.getId(), st.getCronExpression(), e);
        }
    }

    /**
     * 取消定时任务
     */
    private void cancel(Long scheduledTaskId) {
        ScheduledFuture<?> future = scheduledFutures.remove(scheduledTaskId);
        if (future != null && !future.isCancelled()) {
            future.cancel(false);
        }
    }

    /**
     * 执行计划任务：复制源任务配置，创建新任务并启动
     */
    private void execute(ScheduledTask st) {
        log.info("Executing scheduled task: id={}, name={}", st.getId(), st.getName());
        try {
            Task sourceTask = taskRepository.findById(st.getSourceTaskId()).orElse(null);
            if (sourceTask == null) {
                log.error("Source task not found: {}", st.getSourceTaskId());
                return;
            }

            // 生成带时间戳的任务名，避免重复
            String newTaskName = sourceTask.getTaskName() + "_" + System.currentTimeMillis();

            // 创建新任务
            TaskCreateRequest createRequest = new TaskCreateRequest();
            createRequest.setTaskName(newTaskName);
            createRequest.setTaskDesc("[定时任务] " + (st.getDescription() != null ? st.getDescription() : st.getName()));
            var newTaskResponse = taskService.createTask(st.getUserId(), createRequest);
            Long newTaskId = newTaskResponse.getId();

            // 复制源任务的配置到新任务
            copyTaskConfigs(st.getSourceTaskId(), newTaskId);

            // 生成配置副本
            taskService.generateConfigCopy(st.getUserId(), newTaskId);

            // 启动任务 (Step 10 = 因子挖掘完整流程)
            executionService.startTask(st.getUserId(), newTaskId, "10");

            // 更新执行时间
            st.setLastRunAt(LocalDateTime.now());
            st.setNextRunAt(calculateNextRunTime(st.getCronExpression()));
            scheduledTaskRepository.save(st);

            log.info("Scheduled task executed successfully: created task id={}", newTaskId);
        } catch (Exception e) {
            log.error("Failed to execute scheduled task: id={}", st.getId(), e);
        }
    }

    /**
     * 复制源任务的所有配置到新任务
     */
    private void copyTaskConfigs(Long sourceTaskId, Long newTaskId) {
        var configs = taskConfigRepository.findByTaskId(sourceTaskId);
        for (var config : configs) {
            var newConfig = com.factorfactory.webapp.entity.TaskConfig.builder()
                    .taskId(newTaskId)
                    .section(config.getSection())
                    .value(config.getValue())
                    .build();
            taskConfigRepository.save(newConfig);
        }
    }

    private LocalDateTime calculateNextRunTime(String cronExpression) {
        try {
            CronTrigger trigger = new CronTrigger(cronExpression);
            java.util.Date next = trigger.nextExecutionTime(
                    new org.springframework.scheduling.TriggerContext() {
                        @Override public java.time.Instant lastScheduledExecution() { return null; }
                        @Override public java.time.Instant lastActualExecution() { return null; }
                        @Override public java.time.Instant lastCompletion() { return null; }
                    }
            );
            return next != null ? next.toInstant().atZone(java.time.ZoneId.systemDefault()).toLocalDateTime() : null;
        } catch (Exception e) {
            return null;
        }
    }

    private ScheduledTaskResponse toResponse(ScheduledTask st) {
        String sourceTaskName = taskRepository.findById(st.getSourceTaskId())
                .map(Task::getTaskName)
                .orElse("未知任务");

        return ScheduledTaskResponse.builder()
                .id(st.getId())
                .name(st.getName())
                .description(st.getDescription())
                .sourceTaskId(st.getSourceTaskId())
                .sourceTaskName(sourceTaskName)
                .cronExpression(st.getCronExpression())
                .enabled(st.isEnabled())
                .lastRunAt(st.getLastRunAt())
                .nextRunAt(st.getNextRunAt())
                .createdAt(st.getCreatedAt())
                .updatedAt(st.getUpdatedAt())
                .build();
    }
}
