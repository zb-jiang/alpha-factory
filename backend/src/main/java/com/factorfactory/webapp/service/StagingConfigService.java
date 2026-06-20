package com.factorfactory.webapp.service;

import com.factorfactory.webapp.config.StagingProperties;
import com.factorfactory.webapp.entity.Task;
import com.factorfactory.webapp.entity.TaskConfig;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.repository.TaskConfigRepository;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import com.fasterxml.jackson.dataformat.yaml.YAMLGenerator;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Slf4j
@Service
@RequiredArgsConstructor
public class StagingConfigService {

    private final TaskConfigRepository taskConfigRepository;
    private final StagingProperties stagingProperties;
    private final SystemConfigService systemConfigService;
    private final ObjectMapper objectMapper;

    /**
     * 从 task_config 数据库配置生成当前任务 staging/config 下的 YAML 运行副本。
     * YAML 只是运行时副本，UI 配置的唯一数据源仍然是 task_config 表。
     */
    @Transactional(readOnly = true)
    public void writeConfigCopyFromDb(Long userId, Task task) {
        Long taskId = task.getId();
        Path configDir = Paths.get(task.getStagingPath(), "config");
        try {
            Files.createDirectories(configDir);

            Map<String, Object> envConfig = mergeEnvConfig(userId, taskId);
            writeYaml(configDir.resolve("env.yaml"), envConfig);

            Map<String, Object> analysisConfig = mergeAnalysisConfig(taskId);
            analysisConfig.putAll(mergeLabelConfig(taskId));
            Map<String, Object> tushareForAnalysis = expandDottedKeys(sectionMap(taskId, TaskConfigService.TAB_TUSHARE, HashMap::new));
            if (tushareForAnalysis.containsKey("price_adjust")) {
                analysisConfig.put("price_adjust", tushareForAnalysis.get("price_adjust"));
            }
            if (tushareForAnalysis.containsKey("price_adjust_reference_date")) {
                analysisConfig.put("price_adjust_reference_date", tushareForAnalysis.get("price_adjust_reference_date"));
            }
            if (tushareForAnalysis.containsKey("preprocess")) {
                analysisConfig.put("preprocess", tushareForAnalysis.get("preprocess"));
            }

            Map<String, Object> stockPoolForAnalysis = expandDottedKeys(sectionMap(taskId, TaskConfigService.TAB_STOCK_POOL, HashMap::new));
            if (stockPoolForAnalysis.containsKey("stock_pool")) {
                Map<String, Object> sp = (Map<String, Object>) stockPoolForAnalysis.get("stock_pool");
                sp = filterStockPoolByType(sp);
                if (sp != null && !sp.isEmpty()) {
                    analysisConfig.put("stock_pool", sp);
                }
            }

            // 将 selector tab 中的 test 时间窗口参数合并到 analysis_rule.yaml
            Map<String, Object> selectorValues = sectionMap(taskId, TaskConfigService.TAB_SELECTOR, HashMap::new);
            if (selectorValues.containsKey("test_start_date")) {
                analysisConfig.put("test_start_date", selectorValues.get("test_start_date"));
            }
            if (selectorValues.containsKey("test_end_date")) {
                analysisConfig.put("test_end_date", selectorValues.get("test_end_date"));
            }
            if (selectorValues.containsKey("iteration_count")) {
                analysisConfig.put("iteration_count", selectorValues.get("iteration_count"));
            }
            // 启动推荐时训练窗口尚未确定，若 train_start_date/train_end_date 为空则不写入
            Object trainStart = analysisConfig.get("train_start_date");
            Object trainEnd = analysisConfig.get("train_end_date");
            if (trainStart == null || String.valueOf(trainStart).isBlank()) {
                analysisConfig.remove("train_start_date");
            }
            if (trainEnd == null || String.valueOf(trainEnd).isBlank()) {
                analysisConfig.remove("train_end_date");
            }

            writeYaml(configDir.resolve("analysis_rule.yaml"), analysisConfig);

            writeYaml(configDir.resolve("backtest_rule.yaml"), mergeBacktestConfig(taskId));
            mergeFeaturePoolConfig(taskId, configDir);
            writeYaml(configDir.resolve("market_context.yaml"), mergeMarketConfig(taskId));
            writeYaml(configDir.resolve("score.yaml"), mergeScoreConfig(taskId));
            writeYaml(configDir.resolve("selector.yaml"), mergeSelectorConfig(taskId));

            log.info("Staging config copy generated for task {}", taskId);
        } catch (IOException e) {
            throw new BusinessException("生成配置副本失败: " + e.getMessage());
        }
    }

    private Map<String, Object> mergeEnvConfig(Long userId, Long taskId) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("data_source", "tushare");

        String tushareApiKey = systemConfigService.getTushareApiKey(userId);
        Map<String, Object> tushare = new LinkedHashMap<>();
        tushare.put("api_key", tushareApiKey != null ? tushareApiKey : "");
        tushare.put("data_cache_dir", "tushare_cache");
        tushare.put("enable_meta_reconcile", true);
        result.put("tushare", tushare);

        Map<String, Object> tushareParams = sectionMap(taskId, TaskConfigService.TAB_TUSHARE, HashMap::new);
        tushare.put("placeholder_expire_days", tushareParams.getOrDefault("placeholder_expire_days", 3));
        tushare.put("enable_meta_reconcile", tushareParams.getOrDefault("enable_meta_reconcile", true));
        result.put("freq", tushareParams.getOrDefault("freq", "day"));

        Map<String, Object> llmParams = sectionMap(taskId, TaskConfigService.TAB_LLM, HashMap::new);
        result.put("llm_candidate_count", llmParams.getOrDefault("llm_candidate_count", 10));
        result.put("llm_agents", buildLlmAgentsConfig(llmParams));

        Map<String, Object> prescreen = sectionMap(taskId, TaskConfigService.TAB_PRESCREEN, HashMap::new);
        result.put("min_rank_ic_to_backtest", prescreen.getOrDefault("min_rank_ic_to_backtest", 0.02));
        result.put("min_rank_ic_ir_to_backtest", prescreen.getOrDefault("min_rank_ic_ir_to_backtest", 0.2));
        result.put("min_positive_ic_ratio", prescreen.getOrDefault("min_positive_ic_ratio", 0.4));
        result.put("enable_direction_filter", prescreen.getOrDefault("enable_direction_filter", false));

        Map<String, Object> featurePool = sectionMap(taskId, TaskConfigService.TAB_FEATURE_POOL, HashMap::new);
        result.put("summary_top_k", featurePool.getOrDefault("summary_top_k", 3));
        result.put("unstable_top_k", featurePool.getOrDefault("unstable_top_k", 3));
        result.put("high_corr_threshold", featurePool.getOrDefault("high_corr_threshold", 0.5));
        result.put("max_missing_ratio", featurePool.getOrDefault("max_missing_ratio", 0.2));
        result.put("fundamental_health_top_k", featurePool.getOrDefault("fundamental_health_top_k", 5));

        return result;
    }

    private Map<String, Object> mergeAnalysisConfig(Long taskId) {
        return expandDottedKeys(sectionMap(taskId, TaskConfigService.TAB_ANALYSIS, LinkedHashMap::new));
    }

    private Map<String, Object> mergeLabelConfig(Long taskId) {
        return expandDottedKeys(sectionMap(taskId, TaskConfigService.TAB_LABEL, LinkedHashMap::new));
    }

    private void mergeFeaturePoolConfig(Long taskId, Path configDir) throws IOException {
        Map<String, Object> dbValues = sectionMap(taskId, TaskConfigService.TAB_FEATURE_POOL, LinkedHashMap::new);
        Path template = Paths.get(stagingProperties.getConfigTemplateDir(), "feature_pool.yaml");
        Path target = configDir.resolve("feature_pool.yaml");
        if (Files.exists(template)) {
            String content = Files.readString(template, StandardCharsets.UTF_8);
            Object value = dbValues.getOrDefault("enable_chip_features", false);
            content = content.replaceAll("(?m)^enable_chip_features:\\s*.*$", "enable_chip_features: " + value);
            Files.writeString(target, content, StandardCharsets.UTF_8);
        } else {
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("enable_chip_features", dbValues.getOrDefault("enable_chip_features", false));
            writeYaml(target, result);
        }
    }

    private Map<String, Object> mergeBacktestConfig(Long taskId) {
        return expandDottedKeys(sectionMap(taskId, TaskConfigService.TAB_BACKTEST, LinkedHashMap::new));
    }

    private Map<String, Object> mergeMarketConfig(Long taskId) {
        return expandDottedKeys(sectionMap(taskId, TaskConfigService.TAB_MARKET, LinkedHashMap::new));
    }

    private Map<String, Object> mergeScoreConfig(Long taskId) {
        return expandDottedKeys(sectionMap(taskId, TaskConfigService.TAB_SCORE, LinkedHashMap::new));
    }

    private Map<String, Object> mergeSelectorConfig(Long taskId) {
        Map<String, Object> raw = expandDottedKeys(sectionMap(taskId, TaskConfigService.TAB_SELECTOR, LinkedHashMap::new));
        // 以下6个字段不属于 selector 算法配置，不应进入 selector.yaml
        Set<String> excluded = Set.of(
                "test_start_date", "test_end_date", "iteration_count",
                "train_window_source", "manual_train_start_date", "manual_train_end_date"
        );
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : raw.entrySet()) {
            if (!excluded.contains(entry.getKey())) {
                result.put(entry.getKey(), entry.getValue());
            }
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> filterStockPoolByType(Map<String, Object> sp) {
        if (sp == null) return null;
        String type = String.valueOf(sp.getOrDefault("type", ""));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("type", type);
        // 公共字段（所有类型都有）
        if (sp.containsKey("include_st")) {
            result.put("include_st", sp.get("include_st"));
        }
        if (sp.containsKey("include_new_stock")) {
            result.put("include_new_stock", sp.get("include_new_stock"));
        }
        if (sp.containsKey("new_stock_days")) {
            result.put("new_stock_days", sp.get("new_stock_days"));
        }
        if ("index_components".equals(type)) {
            result.put("index_code", sp.getOrDefault("index_code", "SH000300"));
            if (sp.containsKey("dynamic_membership")) {
                result.put("dynamic_membership", sp.get("dynamic_membership"));
            }
            if (sp.containsKey("index_component_search_max_open_days")) {
                result.put("index_component_search_max_open_days", sp.get("index_component_search_max_open_days"));
            }
        } else if ("industry".equals(type)) {
            if (sp.containsKey("industry_name")) {
                result.put("industry_name", sp.get("industry_name"));
            }
        } else if ("custom_list".equals(type)) {
            if (sp.containsKey("instruments")) {
                result.put("instruments", sp.get("instruments"));
            }
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> buildLlmAgentsConfig(Map<String, Object> llmParams) {
        Map<String, Object> result = new LinkedHashMap<>();
        String prefix = "llm_agents.";
        for (Map.Entry<String, Object> entry : llmParams.entrySet()) {
            String key = entry.getKey();
            if (key.startsWith(prefix)) {
                String remainder = key.substring(prefix.length());
                int dotIdx = remainder.indexOf('.');
                if (dotIdx > 0) {
                    String agentName = remainder.substring(0, dotIdx);
                    String fieldName = remainder.substring(dotIdx + 1);
                    String yamlField = switch (fieldName) {
                        case "llm_provider" -> null;
                        case "llm_base_url" -> "base_url";
                        case "llm_model" -> "model";
                        case "llm_api_key" -> "api_key";
                        default -> fieldName;
                    };
                    if (yamlField != null) {
                        result.computeIfAbsent(agentName, k -> new LinkedHashMap<String, Object>());
                        ((Map<String, Object>) result.get(agentName)).put(yamlField, entry.getValue());
                    }
                }
            }
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> expandDottedKeys(Map<String, Object> source) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : source.entrySet()) {
            String key = entry.getKey();
            Object value = entry.getValue();
            if (!key.contains(".")) {
                result.put(key, value);
                continue;
            }

            String[] parts = key.split("\\.");
            Map<String, Object> current = result;
            for (int i = 0; i < parts.length - 1; i++) {
                Object next = current.get(parts[i]);
                if (!(next instanceof Map<?, ?>)) {
                    next = new LinkedHashMap<String, Object>();
                    current.put(parts[i], next);
                }
                current = (Map<String, Object>) next;
            }
            current.put(parts[parts.length - 1], value);
        }
        return result;
    }

    private Map<String, Object> sectionMap(Long taskId, String section, java.util.function.Supplier<Map<String, Object>> fallback) {
        return taskConfigRepository.findByTaskIdAndSection(taskId, section)
                .map(this::parseConfigValue)
                .orElseGet(fallback);
    }

    private Map<String, Object> parseConfigValue(TaskConfig config) {
        try {
            return objectMapper.readValue(config.getValue(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            log.warn("Failed to parse task config: taskId={}, section={}", config.getTaskId(), config.getSection(), e);
            return new LinkedHashMap<>();
        }
    }

    private void writeYaml(Path filePath, Map<String, Object> data) throws IOException {
        ObjectMapper yamlMapper = new ObjectMapper(new YAMLFactory()
                .disable(YAMLGenerator.Feature.WRITE_DOC_START_MARKER)
                .enable(YAMLGenerator.Feature.MINIMIZE_QUOTES));
        yamlMapper.writeValue(filePath.toFile(), sanitizeDoubles(data));
    }

    @SuppressWarnings("unchecked")
    private Object sanitizeDoubles(Object value) {
        if (value instanceof Double) {
            return BigDecimal.valueOf((Double) value);
        } else if (value instanceof Float) {
            return BigDecimal.valueOf((Float) value);
        } else if (value instanceof Map) {
            Map<String, Object> result = new LinkedHashMap<>();
            for (Map.Entry<String, Object> entry : ((Map<String, Object>) value).entrySet()) {
                result.put(entry.getKey(), sanitizeDoubles(entry.getValue()));
            }
            return result;
        } else if (value instanceof List) {
            List<Object> result = new ArrayList<>();
            for (Object item : (List<?>) value) {
                result.add(sanitizeDoubles(item));
            }
            return result;
        }
        return value;
    }
}
