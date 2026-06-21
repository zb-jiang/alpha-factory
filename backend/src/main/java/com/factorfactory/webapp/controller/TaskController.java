package com.factorfactory.webapp.controller;

import com.factorfactory.webapp.dto.*;
import com.factorfactory.webapp.service.ExecutionService;
import com.factorfactory.webapp.service.TaskService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/tasks")
@RequiredArgsConstructor
public class TaskController {

    private final TaskService taskService;
    private final ExecutionService executionService;
    private final com.factorfactory.webapp.service.TrainingArtifactsService trainingArtifactsService;
    private final com.factorfactory.webapp.service.OosArtifactsService oosArtifactsService;

    @GetMapping
    public ApiResponse<List<TaskResponse>> list(Authentication authentication) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(taskService.listTasks(userId));
    }

    @PostMapping
    public ApiResponse<TaskResponse> create(Authentication authentication,
                                            @Valid @RequestBody TaskCreateRequest request) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(taskService.createTask(userId, request));
    }

    @GetMapping("/{taskId}")
    public ApiResponse<TaskResponse> get(Authentication authentication, @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(taskService.getTask(userId, taskId));
    }

    @DeleteMapping("/{taskId}")
    public ApiResponse<Void> delete(Authentication authentication,
                                    @PathVariable Long taskId,
                                    @RequestParam(defaultValue = "true") boolean deleteStaging) {
        Long userId = (Long) authentication.getPrincipal();
        taskService.deleteTask(userId, taskId, deleteStaging);
        return ApiResponse.ok();
    }

    @PostMapping("/{taskId}/start")
    public ApiResponse<Void> start(Authentication authentication,
                                   @PathVariable Long taskId,
                                   @Valid @RequestBody TaskStartRequest request) {
        Long userId = (Long) authentication.getPrincipal();
        executionService.startTask(userId, taskId, request.getStep());
        return ApiResponse.ok();
    }

    @PostMapping("/{taskId}/stop")
    public ApiResponse<Void> stop(Authentication authentication, @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        executionService.stopTask(userId, taskId);
        return ApiResponse.ok();
    }

    @GetMapping("/{taskId}/status")
    public ApiResponse<Map<String, Object>> status(Authentication authentication,
                                                    @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        TaskResponse task = taskService.getTask(userId, taskId);
        return ApiResponse.ok(Map.of(
                "status", task.getStatus().name(),
                "currentStep", task.getCurrentStep() != null ? task.getCurrentStep() : "",
                "pid", task.getPid() != null ? task.getPid() : 0
        ));
    }

    /**
     * 获取指定 step 最新一次执行的进度日志（用于前端轮询展示）。
     * 与"训练窗口推荐"的 getSelectorResult 走同样的轮询模式，更可靠且能拿到历史日志。
     */
    @GetMapping("/{taskId}/step-progress")
    public ApiResponse<Map<String, Object>> stepProgress(Authentication authentication,
                                                          @PathVariable Long taskId,
                                                          @RequestParam String step,
                                                          @RequestParam(defaultValue = "500") int maxLines) {
        Long userId = (Long) authentication.getPrincipal();
        TaskResponse task = taskService.getTask(userId, taskId);
        List<String> lines = executionService.getStepProgressLogs(taskId, step, maxLines);
        Map<String, Object> resp = new java.util.LinkedHashMap<>();
        resp.put("status", task.getStatus().name());
        resp.put("currentStep", task.getCurrentStep() != null ? task.getCurrentStep() : "");
        resp.put("running", "RUNNING".equals(task.getStatus().name()) && step.equals(task.getCurrentStep()));
        resp.put("progress_logs", lines);
        // 读取 active_context.json（由 step10 等流水线在运行时维护），前端据此渲染"当前执行阶段"
        resp.put("active_context", executionService.readActiveContext(taskId));
        return ApiResponse.ok(resp);
    }

    /**
     * 读取 step10 因子挖掘流水线的结构化产物（窗口起止 / 特征体检 / 市场环境 / 各 iter 因子 / discovery 候选 / validation 结果 / 跨窗口汇总）。
     * 前端"因子挖掘"TAB 在日志面板下方按窗口章节方式呈现。
     */
    @GetMapping("/{taskId}/training-artifacts")
    public ApiResponse<Map<String, Object>> trainingArtifacts(Authentication authentication,
                                                                @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(trainingArtifactsService.getArtifacts(userId, taskId));
    }

    /**
     * 读取 step11 样本外回测 (OOS) 产物：输入因子清单、因子指标、Top3、测试区间。
     */
    @GetMapping("/{taskId}/oos-artifacts")
    public ApiResponse<Map<String, Object>> oosArtifacts(Authentication authentication,
                                                          @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(oosArtifactsService.getArtifacts(userId, taskId));
    }
}
