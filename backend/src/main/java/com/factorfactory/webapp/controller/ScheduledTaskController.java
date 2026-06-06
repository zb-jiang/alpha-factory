package com.factorfactory.webapp.controller;

import com.factorfactory.webapp.dto.ApiResponse;
import com.factorfactory.webapp.dto.ScheduledTaskRequest;
import com.factorfactory.webapp.dto.ScheduledTaskResponse;
import com.factorfactory.webapp.service.ScheduledTaskService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1/scheduled-tasks")
@RequiredArgsConstructor
public class ScheduledTaskController {

    private final ScheduledTaskService scheduledTaskService;

    @GetMapping
    public ApiResponse<List<ScheduledTaskResponse>> list(Authentication authentication) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(scheduledTaskService.listTasks(userId));
    }

    @PostMapping
    public ApiResponse<ScheduledTaskResponse> create(
            Authentication authentication,
            @Valid @RequestBody ScheduledTaskRequest request) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(scheduledTaskService.createTask(userId, request));
    }

    @PutMapping("/{id}")
    public ApiResponse<ScheduledTaskResponse> update(
            Authentication authentication,
            @PathVariable Long id,
            @Valid @RequestBody ScheduledTaskRequest request) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(scheduledTaskService.updateTask(userId, id, request));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> delete(
            Authentication authentication,
            @PathVariable Long id) {
        Long userId = (Long) authentication.getPrincipal();
        scheduledTaskService.deleteTask(userId, id);
        return ApiResponse.ok();
    }

    @PostMapping("/{id}/toggle")
    public ApiResponse<ScheduledTaskResponse> toggle(
            Authentication authentication,
            @PathVariable Long id,
            @RequestParam boolean enabled) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(scheduledTaskService.toggleEnabled(userId, id, enabled));
    }

    @PostMapping("/{id}/trigger")
    public ApiResponse<Void> trigger(
            Authentication authentication,
            @PathVariable Long id) {
        Long userId = (Long) authentication.getPrincipal();
        scheduledTaskService.triggerOnce(userId, id);
        return ApiResponse.ok();
    }
}
