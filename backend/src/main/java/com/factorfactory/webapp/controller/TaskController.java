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
}
