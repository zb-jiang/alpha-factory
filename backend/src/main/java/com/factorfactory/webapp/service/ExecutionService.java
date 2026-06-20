package com.factorfactory.webapp.service;

import com.factorfactory.webapp.config.ExecutionProperties;
import com.factorfactory.webapp.entity.Task;
import com.factorfactory.webapp.entity.TaskExecutionLog;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.repository.TaskExecutionLogRepository;
import com.factorfactory.webapp.repository.TaskRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
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

        TaskExecutionLog execLog = TaskExecutionLog.builder()
                .taskId(taskId)
                .step(step)
                .startTime(LocalDateTime.now())
                .logFilePath(Paths.get(task.getStagingPath(), "outputs", "_runtime",
                        "step" + step + "_" + System.currentTimeMillis() + ".log").toString())
                .build();
        executionLogRepository.save(execLog);

        try {
            Path logPath = Paths.get(execLog.getLogFilePath());
            Files.createDirectories(logPath.getParent());

            ProcessBuilder pb = new ProcessBuilder(
                    executionProperties.getPythonExecutable(),
                    scriptPath.toString(),
                    "--staging", task.getStagingPath()
            );
            pb.directory(srcDir.toFile());
            pb.environment().put("PYTHONIOENCODING", "utf-8");
            pb.redirectErrorStream(true);

            Process process = pb.start();
            runningProcesses.put(taskId, process);

            task.setStatus(Task.TaskStatus.RUNNING);
            task.setCurrentStep(step);
            task.setPid(getPid(process));
            taskRepository.save(task);

            log.info("Task started: id={}, step={}, pid={}", taskId, step, task.getPid());

            startLogStreaming(taskId, process, logPath);

        } catch (IOException e) {
            task.setStatus(Task.TaskStatus.ERROR);
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

        task.setStatus(Task.TaskStatus.STOPPED);
        task.setCurrentStep(null);
        task.setPid(null);
        taskRepository.save(task);

        updateExecutionLog(taskId, 130);
    }

    private void startLogStreaming(Long taskId, Process process, Path logPath) {
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
                    task.setStatus(exitCode == 0 ? Task.TaskStatus.COMPLETED : Task.TaskStatus.ERROR);
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
                    scriptPath.toString(),
                    "--staging", task.getStagingPath()
            );
            pb.directory(srcDir.toFile());
            pb.environment().put("PYTHONIOENCODING", "utf-8");
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
}
