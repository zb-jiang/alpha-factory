package com.factorfactory.webapp.service;

import com.factorfactory.webapp.config.StagingProperties;
import com.factorfactory.webapp.dto.TaskCreateRequest;
import com.factorfactory.webapp.dto.TaskResponse;
import com.factorfactory.webapp.entity.Task;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.repository.TaskRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.nio.file.*;
import java.util.List;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class TaskService {

    private final TaskRepository taskRepository;
    private final StagingProperties stagingProperties;

    private static final List<String> CONFIG_FILES = List.of(
            "env.yaml", "analysis_rule.yaml", "backtest_rule.yaml",
            "feature_pool.yaml", "market_context.yaml", "score.yaml", "selector.yaml"
    );

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

        String stagingPath = Paths.get(stagingProperties.getRootDir(),
                String.valueOf(userId), request.getTaskName()).toString();

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

            copyTemplateConfigs(configDir);
        } catch (IOException e) {
            throw new BusinessException("创建 staging 目录失败: " + e.getMessage());
        }

        Task task = Task.builder()
                .userId(userId)
                .taskName(request.getTaskName())
                .taskDesc(request.getTaskDesc())
                .stagingPath(stagingPath)
                .status(Task.TaskStatus.IDLE)
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
        Task task = taskRepository.findById(taskId)
                .orElseThrow(() -> new BusinessException(404, "任务不存在"));
        if (!task.getUserId().equals(userId)) {
            throw new BusinessException(403, "无权访问此任务");
        }
        return task;
    }

    private void copyTemplateConfigs(Path configDir) throws IOException {
        Path templateDir = Paths.get(stagingProperties.getConfigTemplateDir());
        if (!Files.exists(templateDir)) {
            log.warn("Config template directory not found: {}, skipping copy", templateDir);
            return;
        }
        for (String configFile : CONFIG_FILES) {
            Path source = templateDir.resolve(configFile);
            Path target = configDir.resolve(configFile);
            if (Files.exists(source) && !Files.exists(target)) {
                Files.copy(source, target);
            }
        }
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
