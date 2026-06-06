package com.factorfactory.webapp.service;

import com.factorfactory.webapp.dto.StructuredConfigResponse;
import com.factorfactory.webapp.dto.StructuredConfigResponse.*;
import com.factorfactory.webapp.entity.UserConfig;
import com.factorfactory.webapp.repository.UserConfigRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;

/**
 * 系统级配置服务（原用户级配置升级）
 * 管理：自定义 LLM 供应商 + Tushare 凭证
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class SystemConfigService {

    private final UserConfigRepository userConfigRepository;
    private final GlobalConfigService globalConfigService;
    private final ObjectMapper objectMapper;

    private static final String SECTION_TUSHARE = "tushare_credentials";
    private static final String SECTION_CUSTOM_PROVIDERS = "custom_llm_providers";

    public StructuredConfigResponse getAllConfigs(Long userId) {
        return StructuredConfigResponse.builder()
                .section("system")
                .sectionLabel("系统设置")
                .sectionDescription("管理数据源凭证和自定义 LLM 供应商")
                .sectionIcon("Setting")
                .groups(List.of(
                        buildTushareGroup(userId),
                        buildCustomProvidersGroup(userId)
                ))
                .build();
    }

    @Transactional
    public void updateSection(Long userId, String section, Object values) {
        UserConfig config = userConfigRepository.findByUserIdAndSection(userId, section)
                .orElseGet(() -> UserConfig.builder().userId(userId).section(section).build());
        try {
            config.setValue(objectMapper.writeValueAsString(values));
        } catch (JsonProcessingException e) {
            throw new RuntimeException("序列化配置失败", e);
        }
        userConfigRepository.save(config);
        log.info("System config for user {} section {} updated", userId, section);
    }

    /**
     * 获取用户的 Tushare API Key
     */
    public String getTushareApiKey(Long userId) {
        return getSectionValue(userId, SECTION_TUSHARE, "api_key");
    }

    /**
     * 获取自定义 LLM 供应商列表
     */
    @SuppressWarnings("unchecked")
    public List<Map<String, String>> getCustomProviders(Long userId) {
        UserConfig config = userConfigRepository.findByUserIdAndSection(userId, SECTION_CUSTOM_PROVIDERS).orElse(null);
        if (config == null || config.getValue() == null || config.getValue().isBlank()) {
            return List.of();
        }
        try {
            return objectMapper.readValue(config.getValue(), List.class);
        } catch (JsonProcessingException e) {
            log.warn("Failed to parse custom providers for user {}", userId, e);
            return List.of();
        }
    }

    /**
     * 获取合并后的供应商列表：默认(yml) + 自定义(DB)
     */
    public List<Map<String, String>> getMergedProviders(Long userId) {
        List<Map<String, String>> result = new ArrayList<>(globalConfigService.getDefaultLlmProviders());
        result.addAll(getCustomProviders(userId));
        return result;
    }

    private ConfigGroup buildTushareGroup(Long userId) {
        Map<String, Object> values = getSectionMap(userId, SECTION_TUSHARE);

        return ConfigGroup.builder()
                .name(SECTION_TUSHARE)
                .label("Tushare 数据源")
                .description("Tushare Pro 数据接口凭证，用于获取 A 股行情、财务等数据")
                .icon("DataLine")
                .fields(List.of(
                        ConfigField.builder()
                                .key("api_key").label("Tushare Pro API Key").type("password")
                                .value(values.getOrDefault("api_key", "")).defaultValue("")
                                .placeholder("请输入您的 Tushare Pro API Key")
                                .description("用于获取 A 股行情数据，请在 tushare.pro 注册获取")
                                .source("system")
                                .required(true)
                                .build()
                ))
                .build();
    }

    @SuppressWarnings("unchecked")
    private ConfigGroup buildCustomProvidersGroup(Long userId) {
        List<Map<String, String>> customProviders = getCustomProviders(userId);

        return ConfigGroup.builder()
                .name(SECTION_CUSTOM_PROVIDERS)
                .label("自定义 LLM 供应商")
                .description("添加系统预设之外的 LLM 供应商，任务配置时可选择")
                .icon("Connection")
                .fields(List.of(
                        ConfigField.builder()
                                .key("custom_providers").label("自定义供应商列表").type("provider_list")
                                .value(customProviders).defaultValue(List.of())
                                .description("添加自定义 LLM 供应商（名称 + API 地址 + 描述），任务配置时与系统预设供应商合并显示")
                                .build()
                ))
                .build();
    }

    private Map<String, Object> getSectionMap(Long userId, String section) {
        return userConfigRepository.findByUserIdAndSection(userId, section)
                .map(config -> {
                    try {
                        return objectMapper.readValue(config.getValue(), new TypeReference<Map<String, Object>>() {});
                    } catch (JsonProcessingException e) {
                        log.warn("Failed to parse config: userId={}, section={}", userId, section, e);
                        return new HashMap<String, Object>();
                    }
                })
                .orElseGet(HashMap::new);
    }

    private String getSectionValue(Long userId, String section, String key) {
        Map<String, Object> map = getSectionMap(userId, section);
        Object value = map.get(key);
        return value != null ? value.toString() : null;
    }
}
