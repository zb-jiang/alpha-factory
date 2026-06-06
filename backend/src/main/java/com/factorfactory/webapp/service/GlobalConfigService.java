package com.factorfactory.webapp.service;

import com.factorfactory.webapp.config.LlmProperties;
import com.factorfactory.webapp.config.LlmProperties.Provider;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * 全局配置服务
 * 从 application.yml 读取默认 LLM 供应商列表
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class GlobalConfigService {

    private final LlmProperties llmProperties;

    /**
     * 获取默认 LLM 供应商列表（来自 application.yml）
     */
    public List<Map<String, String>> getDefaultLlmProviders() {
        return llmProperties.getDefaultProviders().stream()
                .map(p -> {
                    Map<String, String> map = new LinkedHashMap<>();
                    map.put("name", p.getName());
                    map.put("base_url", p.getBaseUrl());
                    map.put("description", p.getDescription());
                    return map;
                })
                .toList();
    }

    /**
     * 兼容旧接口：获取 LLM 供应商列表
     * 现在只返回默认列表（来自 yml），自定义供应商由 SystemConfigService 管理
     */
    public List<Map<String, String>> getLlmProviders() {
        return getDefaultLlmProviders();
    }
}
