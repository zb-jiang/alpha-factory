package com.factorfactory.webapp.service;

import com.factorfactory.webapp.config.ExecutionProperties;
import com.factorfactory.webapp.entity.Task;
import com.factorfactory.webapp.entity.TaskExecutionLog;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.repository.TaskExecutionLogRepository;
import com.factorfactory.webapp.repository.TaskRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Slf4j
@Service
@RequiredArgsConstructor
public class ExecutionService {

    private final TaskRepository taskRepository;
    private final TaskExecutionLogRepository executionLogRepository;
    private final TaskService taskService;
    private final ExecutionProperties executionProperties;
    private final LogWebSocketHandler logWebSocketHandler;

    /**
     * TaskConfigService 与 ExecutionService 互相依赖（TaskConfigService 需要调 runSelectorScript，
     * ExecutionService 启动 step10/step11 前需要调 prepareRunMode）。这里用 setter + @Lazy
     * 注入打破循环依赖：Spring 注入一个代理，真正调用时才解析到 TaskConfigService bean。
     */
    private TaskConfigService taskConfigService;

    @Autowired
    public void setTaskConfigService(@Lazy TaskConfigService taskConfigService) {
        this.taskConfigService = taskConfigService;
    }

    private static final Set<String> ALLOWED_STEPS = Set.of("10", "11", "12", "13", "14");
    private static final Map<String, String> STEP_SCRIPT_MAP = Map.of(
            "10", "step10_iterate.py",
            "11", "step11_oos_test.py",
            "12", "step12_alphalens_report.py",
            "13", "step13_alphalens_dashboard.py",
            "14", "step14_joinquant_export.py"
    );

    private final Map<Long, Process> runningProcesses = new ConcurrentHashMap<>();

    @Transactional
    public void startTask(Long userId, Long taskId, String step) {
        if (!ALLOWED_STEPS.contains(step)) {
            throw new BusinessException("不支持的 Step: " + step + "，可选值: " + ALLOWED_STEPS);
        }

        Task task = taskService.findTaskBelongsToUser(userId, taskId);

        if (task.getStatus() == Task.TaskStatus.RUNNING) {
            throw new BusinessException("任务正在运行中，请先停止");
        }

        // 前置状态约束：step11 / step12 / step13 / step14 都要求至少 step10 已成功
        if (!"10".equals(step) && task.getStatus() == Task.TaskStatus.NEW) {
            throw new BusinessException("请先完成因子挖掘");
        }
        if (("12".equals(step) || "13".equals(step) || "14".equals(step))
                && task.getStatus() == Task.TaskStatus.TRAINING_FINISHED) {
            throw new BusinessException("请先完成样本外回测");
        }

        long runningCount = taskRepository.countByStatus(Task.TaskStatus.RUNNING);
        if (runningCount >= executionProperties.getMaxConcurrentTasks()) {
            throw new BusinessException(429, "已达到最大并发任务数: " + executionProperties.getMaxConcurrentTasks());
        }

        String script = STEP_SCRIPT_MAP.get(step);
        Path srcDir = Paths.get(executionProperties.getSrcDir());
        Path scriptPath = srcDir.resolve(script);

        if (!Files.exists(scriptPath)) {
            throw new BusinessException("脚本文件不存在: " + scriptPath);
        }

        // 启动 step10 / step11 前：硬编码 analysis_rule.run_mode 为对应运行模式，
        // 然后从数据库刷新全量 YAML 副本到 staging/config（与"启动推荐"一致的预处理路径）。
        if ("10".equals(step)) {
            taskConfigService.prepareRunMode(userId, taskId, "train");
        } else if ("11".equals(step)) {
            taskConfigService.prepareRunMode(userId, taskId, "test");
        }

        TaskExecutionLog execLog = TaskExecutionLog.builder()
                .taskId(taskId)
                .step(step)
                .startTime(LocalDateTime.now())
                .logFilePath(Paths.get(task.getStagingPath(), "outputs", "_runtime",
                        "step" + step + "_" + System.currentTimeMillis() + ".log").toString())
                .build();
        executionLogRepository.save(execLog);

        // 启动前的状态会作为 RUNNING 失败时的回滚目标
        Task.TaskStatus previousStatus = task.getStatus();

        try {
            Path logPath = Paths.get(execLog.getLogFilePath());
            Files.createDirectories(logPath.getParent());

            ProcessBuilder pb = new ProcessBuilder(
                    executionProperties.getPythonExecutable(),
                    "-u",
                    scriptPath.toString(),
                    "--staging", task.getStagingPath()
            );
            pb.directory(srcDir.toFile());
            pb.environment().put("PYTHONIOENCODING", "utf-8");
            pb.environment().put("PYTHONUNBUFFERED", "1");
            pb.redirectErrorStream(true);

            Process process = pb.start();
            runningProcesses.put(taskId, process);

            task.setStatus(Task.TaskStatus.RUNNING);
            task.setCurrentStep(step);
            task.setPid(getPid(process));
            taskRepository.save(task);

            log.info("Task started: id={}, step={}, pid={}", taskId, step, task.getPid());

            startLogStreaming(taskId, process, logPath, step, previousStatus);

        } catch (IOException e) {
            // 启动失败：保持启动前的状态不变
            task.setStatus(previousStatus);
            task.setCurrentStep(null);
            task.setPid(null);
            taskRepository.save(task);

            execLog.setEndTime(LocalDateTime.now());
            execLog.setExitCode(-1);
            execLog.setErrorMessage(e.getMessage());
            executionLogRepository.save(execLog);

            throw new BusinessException("启动任务失败: " + e.getMessage());
        }
    }

    @Transactional
    public void stopTask(Long userId, Long taskId) {
        Task task = taskService.findTaskBelongsToUser(userId, taskId);

        if (task.getStatus() != Task.TaskStatus.RUNNING) {
            throw new BusinessException("任务未在运行中");
        }

        Process process = runningProcesses.remove(taskId);
        if (process != null && process.isAlive()) {
            process.destroyForcibly();
            log.info("Task process killed: id={}, pid={}", taskId, task.getPid());
        }

        // 用户主动停止：状态回到运行前的状态（由 log 流线程处理时根据 previousStatus 更新）
        task.setCurrentStep(null);
        task.setPid(null);
        taskRepository.save(task);

        updateExecutionLog(taskId, 130);
    }

    private void startLogStreaming(Long taskId, Process process, Path logPath,
                                    String step, Task.TaskStatus previousStatus) {
        Thread logThread = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(process.getInputStream(), "UTF-8"));
                 BufferedWriter writer = Files.newBufferedWriter(logPath,
                         StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {

                String line;
                while ((line = reader.readLine()) != null) {
                    writer.write(line);
                    writer.newLine();
                    writer.flush();

                    logWebSocketHandler.broadcastLog(taskId, line);
                }

            } catch (IOException e) {
                log.error("Log streaming error for task {}", taskId, e);
            }

            try {
                int exitCode = process.waitFor();
                log.info("Task process exited: id={}, exitCode={}", taskId, exitCode);

                Task task = taskRepository.findById(taskId).orElse(null);
                if (task != null) {
                    Task.TaskStatus newStatus = computeFinishedStatus(step, exitCode, previousStatus);
                    task.setStatus(newStatus);
                    task.setCurrentStep(null);
                    task.setPid(null);
                    taskRepository.save(task);
                }

                updateExecutionLog(taskId, exitCode);
                runningProcesses.remove(taskId);

            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }, "log-stream-task-" + taskId);

        logThread.setDaemon(true);
        logThread.start();
    }

    /**
     * 根据 step 与退出码决定任务的新状态。
     * 规则：
     *  - step10 退出码 0 → TRAINING_FINISHED
     *  - step11 退出码 0 → TESTING_FINISHED
     *  - 其他 step 退出码 0 → 保持启动前状态不变
     *  - 任何 step 退出码非 0 → 保持启动前状态不变（出错或被停止）
     */
    private Task.TaskStatus computeFinishedStatus(String step, int exitCode, Task.TaskStatus previousStatus) {
        if (exitCode != 0) {
            return previousStatus;
        }
        if ("10".equals(step)) {
            return Task.TaskStatus.TRAINING_FINISHED;
        }
        if ("11".equals(step)) {
            return Task.TaskStatus.TESTING_FINISHED;
        }
        return previousStatus;
    }

    private void updateExecutionLog(Long taskId, int exitCode) {
        executionLogRepository.findTopByTaskIdOrderByStartTimeDesc(taskId).ifPresent(execLog -> {
            execLog.setEndTime(LocalDateTime.now());
            execLog.setExitCode(exitCode);
            executionLogRepository.save(execLog);
        });
    }

    private Long getPid(Process process) {
        try {
            return process.pid();
        } catch (Exception e) {
            return null;
        }
    }

    private final Map<Long, Process> runningSelectors = new ConcurrentHashMap<>();
    private final Map<Long, Path> selectorLogPaths = new ConcurrentHashMap<>();

    public void runSelectorScript(Long taskId) {
        Task task = taskRepository.findById(taskId)
                .orElseThrow(() -> new BusinessException("任务不存在"));

        Path srcDir = Paths.get(executionProperties.getSrcDir());
        Path scriptPath = srcDir.resolve("train_window_selector.py");

        if (!Files.exists(scriptPath)) {
            throw new BusinessException("脚本文件不存在: " + scriptPath);
        }

        Process existing = runningSelectors.get(taskId);
        if (existing != null && existing.isAlive()) {
            existing.destroyForcibly();
        }

        Path logPath = Paths.get(task.getStagingPath(), "outputs", "_runtime",
                "selector_" + System.currentTimeMillis() + ".log");

        try {
            Files.createDirectories(logPath.getParent());

            ProcessBuilder pb = new ProcessBuilder(
                    executionProperties.getPythonExecutable(),
                    "-u",
                    scriptPath.toString(),
                    "--staging", task.getStagingPath()
            );
            pb.directory(srcDir.toFile());
            pb.environment().put("PYTHONIOENCODING", "utf-8");
            pb.environment().put("PYTHONUNBUFFERED", "1");
            pb.redirectErrorStream(true);

            Process process = pb.start();
            runningSelectors.put(taskId, process);
            selectorLogPaths.put(taskId, logPath);

            Thread logThread = new Thread(() -> {
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(process.getInputStream(), "UTF-8"));
                     BufferedWriter writer = Files.newBufferedWriter(logPath,
                             StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {

                    String line;
                    while ((line = reader.readLine()) != null) {
                        writer.write(line);
                        writer.newLine();
                        writer.flush();
                        logWebSocketHandler.broadcastLog(taskId, "[selector] " + line);
                    }
                } catch (IOException e) {
                    log.error("Selector log streaming error for task {}", taskId, e);
                }

                try {
                    int exitCode = process.waitFor();
                    log.info("Selector process exited: taskId={}, exitCode={}", taskId, exitCode);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                } finally {
                    runningSelectors.remove(taskId);
                }
            }, "selector-stream-" + taskId);

            logThread.setDaemon(true);
            logThread.start();

        } catch (IOException e) {
            throw new BusinessException("启动选择器失败: " + e.getMessage());
        }
    }

    public boolean isSelectorRunning(Long taskId) {
        Process process = runningSelectors.get(taskId);
        return process != null && process.isAlive();
    }

    public List<String> getSelectorProgressLogs(Long taskId, int maxLines) {
        Path logPath = selectorLogPaths.get(taskId);
        if (logPath == null) {
            Task task = taskRepository.findById(taskId).orElse(null);
            if (task != null) {
                Path runtimeDir = Paths.get(task.getStagingPath(), "outputs", "_runtime");
                try (var stream = Files.list(runtimeDir)) {
                    logPath = stream
                            .filter(path -> path.getFileName().toString().startsWith("selector_"))
                            .filter(path -> path.getFileName().toString().endsWith(".log"))
                            .max(Comparator.comparingLong(path -> path.toFile().lastModified()))
                            .orElse(null);
                } catch (IOException ignored) {
                    logPath = null;
                }
            }
        }
        if (logPath == null || !Files.exists(logPath)) {
            return List.of();
        }
        try {
            List<String> lines = Files.readAllLines(logPath);
            int from = Math.max(0, lines.size() - maxLines);
            return lines.subList(from, lines.size());
        } catch (IOException e) {
            return List.of();
        }
    }

    /**
     * 读取指定任务的 active_context.json 内容（用于前端展示当前执行阶段）。
     * 文件位于 staging/outputs/_runtime/active_context.json，由 step10/selector/... 等脚本运行时维护。
     * 不存在或解析失败时返回 null（前端据此显示 "前期数据准备" 占位文案）。
     */
    public java.util.Map<String, Object> readActiveContext(Long taskId) {
        Task task = taskRepository.findById(taskId).orElse(null);
        if (task == null) {
            return null;
        }
        Path contextPath = Paths.get(task.getStagingPath(), "outputs", "_runtime", "active_context.json");
        if (!Files.exists(contextPath)) {
            return null;
        }
        try {
            byte[] bytes = Files.readAllBytes(contextPath);
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            return mapper.readValue(bytes, new com.fasterxml.jackson.core.type.TypeReference<java.util.LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            log.warn("Failed to read active_context.json for task {}: {}", taskId, e.getMessage());
            return null;
        }
    }

    /**
     * 读取指定 step 的最新进度日志（用于前端轮询展示，技术路线与"训练窗口推荐"对齐）。
     * 实现：在 staging/outputs/_runtime 目录里挑出 step{step}_*.log 中最新的一个，读取最后 maxLines 行。
     */
    public List<String> getStepProgressLogs(Long taskId, String step, int maxLines) {
        Task task = taskRepository.findById(taskId).orElse(null);
        if (task == null) {
            return List.of();
        }
        Path runtimeDir = Paths.get(task.getStagingPath(), "outputs", "_runtime");
        if (!Files.exists(runtimeDir)) {
            return List.of();
        }
        Path logPath = null;
        try (var stream = Files.list(runtimeDir)) {
            String prefix = "step" + step + "_";
            logPath = stream
                    .filter(path -> path.getFileName().toString().startsWith(prefix))
                    .filter(path -> path.getFileName().toString().endsWith(".log"))
                    .max(Comparator.comparingLong(path -> path.toFile().lastModified()))
                    .orElse(null);
        } catch (IOException ignored) {
            return List.of();
        }
        if (logPath == null || !Files.exists(logPath)) {
            return List.of();
        }
        try {
            List<String> lines = Files.readAllLines(logPath);
            int from = Math.max(0, lines.size() - maxLines);
            return lines.subList(from, lines.size());
        } catch (IOException e) {
            return List.of();
        }
    }
}
