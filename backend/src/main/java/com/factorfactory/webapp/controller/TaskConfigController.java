package com.factorfactory.webapp.controller;

import com.factorfactory.webapp.dto.ApiResponse;
import com.factorfactory.webapp.dto.SelectorApplyRequest;
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

    /** 获取任务在 task_config 表中已存在的全部 section 名集合（前端用来判断"配置完成度"） */
    @GetMapping("/saved-sections")
    public ApiResponse<List<String>> getSavedSections(Authentication authentication,
                                                       @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(taskConfigService.getSavedSections(userId, taskId));
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

    /** 诊断：检查 feature_pool.yaml 读取状态 */
    @GetMapping("/_debug/feature-pool-preview")
    public ApiResponse<Map<String, Object>> debugFeaturePoolPreview(Authentication authentication,
                                                                      @PathVariable Long taskId) {
        return ApiResponse.ok(taskConfigService.debugFeaturePoolPreview());
    }

    /** 启动训练窗口选择器计算 */
    @PostMapping("/selector/run")
    public ApiResponse<Void> runSelector(Authentication authentication,
                                          @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        taskConfigService.runSelector(userId, taskId);
        return ApiResponse.ok();
    }

    /** 获取训练窗口选择器结果 */
    @GetMapping("/selector/result")
    public ApiResponse<Map<String, Object>> getSelectorResult(Authentication authentication,
                                                               @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(taskConfigService.getSelectorResult(userId, taskId));
    }

    /** 应用选中的训练窗口到 analysis_rule */
    @PostMapping("/selector/apply")
    public ApiResponse<Void> applySelectorResult(Authentication authentication,
                                                  @PathVariable Long taskId,
                                                  @RequestBody SelectorApplyRequest request) {
        Long userId = (Long) authentication.getPrincipal();
        taskConfigService.applySelectorResult(userId, taskId, request);
        return ApiResponse.ok();
    }
}
