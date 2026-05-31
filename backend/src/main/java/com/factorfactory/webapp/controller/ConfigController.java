package com.factorfactory.webapp.controller;

import com.factorfactory.webapp.dto.ApiResponse;
import com.factorfactory.webapp.dto.ConfigResponse;
import com.factorfactory.webapp.dto.ConfigUpdateRequest;
import com.factorfactory.webapp.service.ConfigService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1/tasks/{taskId}/config")
@RequiredArgsConstructor
public class ConfigController {

    private final ConfigService configService;

    @GetMapping
    public ApiResponse<List<ConfigResponse>> list(Authentication authentication,
                                                   @PathVariable Long taskId) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(configService.listConfigs(userId, taskId));
    }

    @GetMapping("/{configName}")
    public ApiResponse<ConfigResponse> get(Authentication authentication,
                                            @PathVariable Long taskId,
                                            @PathVariable String configName) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(configService.getConfig(userId, taskId, configName));
    }

    @PutMapping("/{configName}")
    public ApiResponse<Void> update(Authentication authentication,
                                     @PathVariable Long taskId,
                                     @PathVariable String configName,
                                     @Valid @RequestBody ConfigUpdateRequest request) {
        Long userId = (Long) authentication.getPrincipal();
        configService.updateConfig(userId, taskId, configName, request.getContent());
        return ApiResponse.ok();
    }

    @PostMapping("/{configName}/reset")
    public ApiResponse<Void> reset(Authentication authentication,
                                    @PathVariable Long taskId,
                                    @PathVariable String configName) {
        Long userId = (Long) authentication.getPrincipal();
        configService.resetConfig(userId, taskId, configName);
        return ApiResponse.ok();
    }
}
