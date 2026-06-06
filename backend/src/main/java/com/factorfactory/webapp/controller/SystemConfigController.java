package com.factorfactory.webapp.controller;

import com.factorfactory.webapp.dto.ApiResponse;
import com.factorfactory.webapp.dto.StructuredConfigResponse;
import com.factorfactory.webapp.service.SystemConfigService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 系统配置控制器
 * 管理：自定义 LLM 供应商 + Tushare 凭证
 */
@RestController
@RequestMapping("/api/v1/config/system")
@RequiredArgsConstructor
public class SystemConfigController {

    private final SystemConfigService systemConfigService;

    @GetMapping
    public ApiResponse<StructuredConfigResponse> getAll(Authentication authentication) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(systemConfigService.getAllConfigs(userId));
    }

    @PutMapping("/{section}")
    public ApiResponse<Void> updateSection(Authentication authentication,
                                            @PathVariable String section,
                                            @RequestBody Object values) {
        Long userId = (Long) authentication.getPrincipal();
        systemConfigService.updateSection(userId, section, values);
        return ApiResponse.ok();
    }
}
