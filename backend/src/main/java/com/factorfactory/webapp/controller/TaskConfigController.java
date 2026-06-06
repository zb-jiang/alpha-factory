package com.factorfactory.webapp.controller;

import com.factorfactory.webapp.dto.ApiResponse;
import com.factorfactory.webapp.dto.StructuredConfigResponse;
import com.factorfactory.webapp.service.TaskConfigService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 任务级配置控制器
 * 每个任务的独立配置，按8个Tab组织
 */
@RestController
@RequestMapping("/api/v1/tasks/{taskId}/config")
@RequiredArgsConstructor
public class TaskConfigController {

    private final TaskConfigService taskConfigService;

    /** 获取任务所有配置Tab */
    @GetMapping
    public ApiResponse<List<StructuredConfigResponse>> getAllTabs(Authentication authentication,
                                                                    @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(taskConfigService.getAllTabs(userId, taskId));
    }

    /** 获取任务某个配置Tab */
    @GetMapping("/{tab}")
    public ApiResponse<StructuredConfigResponse> getTab(Authentication authentication,
                                                          @PathVariable Long taskId,
                                                          @PathVariable String tab) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(taskConfigService.getTab(userId, taskId, tab));
    }

    /** 更新任务某个配置Tab */
    @PutMapping("/{tab}")
    public ApiResponse<Void> updateTab(Authentication authentication,
                                        @PathVariable Long taskId,
                                        @PathVariable String tab,
                                        @RequestBody Map<String, Object> values) {
        taskConfigService.updateTab(taskId, tab, values);
        return ApiResponse.ok();
    }

    /** 测试 LLM 连接 */
    @PostMapping("/llm-test")
    public ApiResponse<Map<String, Object>> testLlmConnection(Authentication authentication,
                                                                @PathVariable Long taskId,
                                                                @RequestBody Map<String, String> params) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(taskConfigService.testLlmConnection(userId, params));
    }
}
