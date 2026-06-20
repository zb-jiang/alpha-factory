package com.factorfactory.webapp.service;

import com.factorfactory.webapp.config.StagingProperties;
import com.factorfactory.webapp.dto.TaskCreateRequest;
import com.factorfactory.webapp.dto.TaskResponse;
import com.factorfactory.webapp.entity.Task;
import com.factorfactory.webapp.entity.User;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.repository.TaskRepository;
import com.factorfactory.webapp.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class TaskService {

    private final TaskRepository taskRepository;
    private final UserRepository userRepository;
    private final StagingProperties stagingProperties;
    private final StagingConfigService stagingConfigService;

    public List<TaskResponse> listTasks(Long userId) {
        return taskRepository.findByUserIdOrderByCreatedAtDesc(userId).stream()
                .map(this::toResponse)
                .collect(Collectors.toList());
    }

    public TaskResponse getTask(Long userId, Long taskId) {
        Task task = findTaskBelongsToUser(userId, taskId);
        return toResponse(task);
    }

    @Transactional
    public TaskResponse createTask(Long userId, TaskCreateRequest request) {
        if (taskRepository.existsByUserIdAndTaskName(userId, request.getTaskName())) {
            throw new BusinessException("任务名称已存在: " + request.getTaskName());
        }

        String username = userRepository.findById(userId)
                .map(User::getUsername)
                .orElseThrow(() -> new BusinessException("用户不存在: " + userId));
        String sanitizedUsername = username.replaceAll("[^a-zA-Z0-9_\\-\\u4e00-\\u9fa5]", "_");

        String stagingPath = Paths.get(stagingProperties.getRootDir(),
                sanitizedUsername, request.getTaskName()).toString();

        Path configDir = Paths.get(stagingPath, "config");
        try {
            Files.createDirectories(configDir);
            Files.createDirectories(Paths.get(stagingPath, "data", "tushare_cache"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "health"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "llm"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "backtest"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "_runtime"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "train_windows"));
            Files.createDirectories(Paths.get(stagingPath, "joinquant"));

            // 创建任务时只创建空 config 目录，不复制模板；
            // 运行前由 StagingConfigService 从 DB 生成并覆盖全部 YAML 副本。
        } catch (IOException e) {
            throw new BusinessException("创建 staging 目录失败: " + e.getMessage());
        }

        Task task = Task.builder()
                .userId(userId)
                .taskName(request.getTaskName())
                .taskDesc(request.getTaskDesc())
                .stagingPath(stagingPath)
                .status(Task.TaskStatus.NEW)
                .build();

        task = taskRepository.save(task);
        log.info("Task created: id={}, name={}, staging={}", task.getId(), task.getTaskName(), stagingPath);
        return toResponse(task);
    }

    @Transactional
    public void deleteTask(Long userId, Long taskId, boolean deleteStaging) {
        Task task = findTaskBelongsToUser(userId, taskId);

        if (task.getStatus() == Task.TaskStatus.RUNNING) {
            throw new BusinessException("任务正在运行中，请先停止任务");
        }

        if (deleteStaging) {
            try {
                Path stagingPath = Paths.get(task.getStagingPath());
                if (Files.exists(stagingPath)) {
                    deleteRecursively(stagingPath);
                }
            } catch (IOException e) {
                log.warn("Failed to delete staging directory: {}", e.getMessage());
            }
        }

        taskRepository.delete(task);
        log.info("Task deleted: id={}", taskId);
    }

    public Task findTaskBelongsToUser(Long userId, Long taskId) {
        Task task = findTask(taskId);
        if (!task.getUserId().equals(userId)) {
            throw new BusinessException(403, "无权访问此任务");
        }
        return task;
    }

    public Task findTask(Long taskId) {
        return taskRepository.findById(taskId)
                .orElseThrow(() -> new BusinessException(404, "任务不存在"));
    }

    /**
     * 任务启动时从 DB 生成 staging config copy。
     * 仅在任务从 NEW → RUNNING 时调用。
     */
    @Transactional
    public void generateConfigCopy(Long userId, Long taskId) {
        Task task = findTaskBelongsToUser(userId, taskId);
        if (task.getStatus() != Task.TaskStatus.NEW) {
            throw new BusinessException("只有新建状态的任务才能生成配置副本");
        }
        stagingConfigService.writeConfigCopyFromDb(userId, task);
    }

    /**
     * 刷新当前任务 staging/config 下的 YAML 运行副本。
     * YAML 只是运行时副本，UI 配置的唯一数据源仍然是 task_config 表。
     */
    @Transactional
    public void refreshConfigCopy(Long userId, Long taskId) {
        Task task = findTaskBelongsToUser(userId, taskId);
        stagingConfigService.writeConfigCopyFromDb(userId, task);
    }

    private void deleteRecursively(Path path) throws IOException {
        if (Files.isDirectory(path)) {
            try (var entries = Files.list(path)) {
                for (Path entry : entries.collect(Collectors.toList())) {
                    deleteRecursively(entry);
                }
            }
        }
        Files.deleteIfExists(path);
    }

    private TaskResponse toResponse(Task task) {
        return TaskResponse.builder()
                .id(task.getId())
                .taskName(task.getTaskName())
                .taskDesc(task.getTaskDesc())
                .stagingPath(task.getStagingPath())
                .status(task.getStatus())
                .currentStep(task.getCurrentStep())
                .pid(task.getPid())
                .createdAt(task.getCreatedAt())
                .updatedAt(task.getUpdatedAt())
                .build();
    }
}
