package com.factorfactory.webapp.service;

import com.factorfactory.webapp.config.StagingProperties;
import com.factorfactory.webapp.dto.TaskCreateRequest;
import com.factorfactory.webapp.dto.TaskResponse;
import com.factorfactory.webapp.entity.Task;
import com.factorfactory.webapp.entity.User;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.repository.TaskConfigRepository;
import com.factorfactory.webapp.repository.TaskRepository;
import com.factorfactory.webapp.repository.UserRepository;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import com.fasterxml.jackson.dataformat.yaml.YAMLGenerator;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class TaskService {

    private final TaskRepository taskRepository;
    private final TaskConfigRepository taskConfigRepository;
    private final UserRepository userRepository;
    private final StagingProperties stagingProperties;
    private final GlobalConfigService globalConfigService;
    private final SystemConfigService systemConfigService;
    private final ObjectMapper objectMapper;

    public List<TaskResponse> listTasks(Long userId) {
        return taskRepository.findByUserIdOrderByCreatedAtDesc(userId).stream()
                .map(this::toResponse)
                .collect(Collectors.toList());
    }

    public TaskResponse getTask(Long userId, Long taskId) {
        Task task = findTaskBelongsToUser(userId, taskId);
        return toResponse(task);
    }

    @Transactional
    public TaskResponse createTask(Long userId, TaskCreateRequest request) {
        if (taskRepository.existsByUserIdAndTaskName(userId, request.getTaskName())) {
            throw new BusinessException("任务名称已存在: " + request.getTaskName());
        }

        String username = userRepository.findById(userId)
                .map(User::getUsername)
                .orElseThrow(() -> new BusinessException("用户不存在: " + userId));
        String sanitizedUsername = username.replaceAll("[^a-zA-Z0-9_\\-\\u4e00-\\u9fa5]", "_");

        String stagingPath = Paths.get(stagingProperties.getRootDir(),
                sanitizedUsername, request.getTaskName()).toString();

        Path configDir = Paths.get(stagingPath, "config");
        try {
            Files.createDirectories(configDir);
            Files.createDirectories(Paths.get(stagingPath, "data", "tushare_cache"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "health"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "llm"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "backtest"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "_runtime"));
            Files.createDirectories(Paths.get(stagingPath, "outputs", "train_windows"));
            Files.createDirectories(Paths.get(stagingPath, "joinquant"));

            // 创建任务时只创建空 config 目录，不复制模板；
            // 启动任务时由 generateConfigCopy() 从 DB 生成并覆盖全部 YAML。
        } catch (IOException e) {
            throw new BusinessException("创建 staging 目录失败: " + e.getMessage());
        }

        Task task = Task.builder()
                .userId(userId)
                .taskName(request.getTaskName())
                .taskDesc(request.getTaskDesc())
                .stagingPath(stagingPath)
                .status(Task.TaskStatus.NEW)
                .build();

        task = taskRepository.save(task);
        log.info("Task created: id={}, name={}, staging={}", task.getId(), task.getTaskName(), stagingPath);
        return toResponse(task);
    }

    @Transactional
    public void deleteTask(Long userId, Long taskId, boolean deleteStaging) {
        Task task = findTaskBelongsToUser(userId, taskId);

        if (task.getStatus() == Task.TaskStatus.RUNNING) {
            throw new BusinessException("任务正在运行中，请先停止任务");
        }

        if (deleteStaging) {
            try {
                Path stagingPath = Paths.get(task.getStagingPath());
                if (Files.exists(stagingPath)) {
                    deleteRecursively(stagingPath);
                }
            } catch (IOException e) {
                log.warn("Failed to delete staging directory: {}", e.getMessage());
            }
        }

        taskRepository.delete(task);
        log.info("Task deleted: id={}", taskId);
    }

    public Task findTaskBelongsToUser(Long userId, Long taskId) {
        Task task = taskRepository.findById(taskId)
                .orElseThrow(() -> new BusinessException(404, "任务不存在"));
        if (!task.getUserId().equals(userId)) {
            throw new BusinessException(403, "无权访问此任务");
        }
        return task;
    }

    /**
     * 任务启动时从 DB merge 三层配置生成 staging config copy
     * 仅在任务从 NEW → RUNNING 时调用
     */
    @Transactional
    public void generateConfigCopy(Long userId, Long taskId) {
        Task task = findTaskBelongsToUser(userId, taskId);
        if (task.getStatus() != Task.TaskStatus.NEW) {
            throw new BusinessException("只有新建状态的任务才能生成配置副本");
        }

        Path configDir = Paths.get(task.getStagingPath(), "config");
        try {
            // 1. merge env.yaml（全局LLM供应商 + 用户Key + 任务LLM参数 + 数据参数 + 预筛门槛）
            Map<String, Object> envConfig = mergeEnvConfig(userId, taskId);
            writeYaml(configDir.resolve("env.yaml"), envConfig);

            // 2. merge analysis_rule.yaml
            Map<String, Object> analysisConfig = mergeAnalysisConfig(taskId);
            // 补充 Tushare 数据配置中的复权口径和预处理设置
            Map<String, Object> tushareForAnalysis = taskConfigRepository
                    .findByTaskIdAndSection(taskId, TaskConfigService.TAB_TUSHARE)
                    .map(this::parseConfigValue)
                    .orElseGet(HashMap::new);
            if (tushareForAnalysis.containsKey("price_adjust")) {
                analysisConfig.put("price_adjust", tushareForAnalysis.get("price_adjust"));
            }
            if (tushareForAnalysis.containsKey("price_adjust_reference_date")) {
                analysisConfig.put("price_adjust_reference_date", tushareForAnalysis.get("price_adjust_reference_date"));
            }
            if (tushareForAnalysis.containsKey("preprocess")) {
                analysisConfig.put("preprocess", tushareForAnalysis.get("preprocess"));
            }
            // 补充股票池配置
            Map<String, Object> stockPoolForAnalysis = taskConfigRepository
                    .findByTaskIdAndSection(taskId, TaskConfigService.TAB_STOCK_POOL)
                    .map(this::parseConfigValue)
                    .orElseGet(HashMap::new);
            if (stockPoolForAnalysis.containsKey("stock_pool")) {
                analysisConfig.put("stock_pool", stockPoolForAnalysis.get("stock_pool"));
            }
            writeYaml(configDir.resolve("analysis_rule.yaml"), analysisConfig);

            // 3. merge backtest_rule.yaml
            Map<String, Object> backtestConfig = mergeBacktestConfig(taskId);
            writeYaml(configDir.resolve("backtest_rule.yaml"), backtestConfig);

            // 4. merge feature_pool.yaml
            Map<String, Object> featurePoolConfig = mergeFeaturePoolConfig(taskId);
            writeYaml(configDir.resolve("feature_pool.yaml"), featurePoolConfig);
            copyTemplateConfigOverwrite(configDir, "feature_pool_combination.yaml");
            copyTemplateConfigOverwrite(configDir, "feature_pool_extended.yaml");

            // 5. merge market_context.yaml
            Map<String, Object> marketConfig = mergeMarketConfig(taskId);
            writeYaml(configDir.resolve("market_context.yaml"), marketConfig);

            // 6. merge score.yaml
            Map<String, Object> scoreConfig = mergeScoreConfig(taskId);
            writeYaml(configDir.resolve("score.yaml"), scoreConfig);

            // 7. merge selector.yaml
            Map<String, Object> selectorConfig = mergeSelectorConfig(taskId);
            writeYaml(configDir.resolve("selector.yaml"), selectorConfig);

            log.info("Config copy generated for task {}", taskId);
        } catch (IOException e) {
            throw new BusinessException("生成配置副本失败: " + e.getMessage());
        }
    }

    /**
     * Merge env.yaml: 全局供应商 + 用户Key + 任务LLM/数据/预筛参数
     */
    private Map<String, Object> mergeEnvConfig(Long userId, Long taskId) {
        Map<String, Object> result = new LinkedHashMap<>();

        // 全局：data_source
        result.put("data_source", "tushare");

        // 用户级：tushare 凭证
        String tushareApiKey = systemConfigService.getTushareApiKey(userId);
        Map<String, Object> tushare = new LinkedHashMap<>();
        tushare.put("api_key", tushareApiKey != null ? tushareApiKey : "");
        tushare.put("data_cache_dir", "tushare_cache");
        tushare.put("enable_meta_reconcile", true);
        result.put("tushare", tushare);

        // 任务级：Tushare 数据配置
        Map<String, Object> tushareParams = taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_TUSHARE)
                .map(this::parseConfigValue)
                .orElseGet(HashMap::new);
        if (tushareParams.containsKey("placeholder_expire_days")) {
            tushare.put("placeholder_expire_days", tushareParams.get("placeholder_expire_days"));
        } else {
            tushare.put("placeholder_expire_days", 3);
        }
        tushare.put("enable_meta_reconcile", tushareParams.getOrDefault("enable_meta_reconcile", true));
        result.put("freq", tushareParams.getOrDefault("freq", "day"));

        // 任务级：数据参数（采样）
        Map<String, Object> dataParams = taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_DATA)
                .map(this::parseConfigValue)
                .orElseGet(HashMap::new);
        result.put("max_instruments", dataParams.getOrDefault("max_instruments", 0));
        if (dataParams.containsKey("sample_instrument") && !String.valueOf(dataParams.get("sample_instrument")).isBlank()) {
            result.put("sample_instrument", dataParams.get("sample_instrument"));
        }

        // 任务级：LLM 参数
        Map<String, Object> llmParams = taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_LLM)
                .map(this::parseConfigValue)
                .orElseGet(HashMap::new);
        result.put("llm_candidate_count", llmParams.getOrDefault("llm_candidate_count", 10));

        // 将扁平的 llm_agents.agent_key.field 结构转换为嵌套的 llm_agents 结构
        Map<String, Object> llmAgents = buildLlmAgentsConfig(llmParams);
        result.put("llm_agents", llmAgents);

        // 任务级：预筛门槛
        Map<String, Object> prescreen = taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_PRESCREEN)
                .map(this::parseConfigValue)
                .orElseGet(HashMap::new);
        result.put("min_rank_ic_to_backtest", prescreen.getOrDefault("min_rank_ic_to_backtest", 0.02));
        result.put("min_rank_ic_ir_to_backtest", prescreen.getOrDefault("min_rank_ic_ir_to_backtest", 0.2));
        result.put("min_positive_ic_ratio", prescreen.getOrDefault("min_positive_ic_ratio", 0.4));
        result.put("enable_direction_filter", prescreen.getOrDefault("enable_direction_filter", false));

        // 任务级：特征体检参数（从原始字段与特征值 Tab 读取）
        Map<String, Object> featurePool = taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_FEATURE_POOL)
                .map(this::parseConfigValue)
                .orElseGet(HashMap::new);
        result.put("summary_top_k", featurePool.getOrDefault("summary_top_k", 3));
        result.put("unstable_top_k", featurePool.getOrDefault("unstable_top_k", 3));
        result.put("high_corr_threshold", featurePool.getOrDefault("high_corr_threshold", 0.5));
        result.put("max_missing_ratio", featurePool.getOrDefault("max_missing_ratio", 0.2));
        result.put("fundamental_health_top_k", featurePool.getOrDefault("fundamental_health_top_k", 5));

        return result;
    }

    private Map<String, Object> mergeAnalysisConfig(Long taskId) {
        return taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_ANALYSIS)
                .map(this::parseConfigValue)
                .orElseGet(LinkedHashMap::new);
    }

    private Map<String, Object> mergeFeaturePoolConfig(Long taskId) {
        Map<String, Object> dbValues = taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_FEATURE_POOL)
                .map(this::parseConfigValue)
                .orElseGet(LinkedHashMap::new);
        // 以 runtime/config/feature_pool.yaml 模板为基底，用 DB 中的 enable_chip_features 覆盖
        Path template = Paths.get(stagingProperties.getConfigTemplateDir(), "feature_pool.yaml");
        Map<String, Object> result = new LinkedHashMap<>();
        if (Files.exists(template)) {
            try {
                ObjectMapper yamlMapper = new ObjectMapper(new YAMLFactory());
                result = yamlMapper.readValue(template.toFile(), new TypeReference<>() {});
            } catch (IOException e) {
                log.warn("读取 feature_pool.yaml 模板失败: {}", e.getMessage());
            }
        }
        result.put("enable_chip_features", dbValues.getOrDefault("enable_chip_features", false));
        return result;
    }

    private Map<String, Object> mergeBacktestConfig(Long taskId) {
        return taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_BACKTEST)
                .map(this::parseConfigValue)
                .orElseGet(LinkedHashMap::new);
    }

    private Map<String, Object> mergeMarketConfig(Long taskId) {
        return taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_MARKET)
                .map(this::parseConfigValue)
                .orElseGet(LinkedHashMap::new);
    }

    private Map<String, Object> mergeScoreConfig(Long taskId) {
        return taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_SCORE)
                .map(this::parseConfigValue)
                .orElseGet(LinkedHashMap::new);
    }

    private Map<String, Object> mergeSelectorConfig(Long taskId) {
        return taskConfigRepository
                .findByTaskIdAndSection(taskId, TaskConfigService.TAB_SELECTOR)
                .map(this::parseConfigValue)
                .orElseGet(LinkedHashMap::new);
    }

    /**
     * 将前端保存的扁平 llm_agents.agent_key.field 结构转换为 env.yaml 需要的嵌套 llm_agents 结构
     * 前端存储格式: { "llm_agents.trend_momentum.model": "xxx", "llm_agents.trend_momentum.base_url": "xxx", ... }
     * env.yaml 需要格式: { trend_momentum: { model: "xxx", base_url: "xxx", ... }, ... }
     */
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

                    // 映射前端字段名到env.yaml字段名
                    String yamlField = switch (fieldName) {
                        case "llm_provider" -> null; // provider仅用于前端选择，不写入yaml
                        case "llm_base_url" -> "base_url";
                        case "llm_model" -> "model";
                        case "llm_api_key" -> "api_key";
                        default -> fieldName; // temperature, max_tokens, timeout_seconds, max_retries, enable
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

    private Map<String, Object> parseConfigValue(com.factorfactory.webapp.entity.TaskConfig config) {
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
        yamlMapper.writeValue(filePath.toFile(), data);
    }

    private void copyTemplateConfigOverwrite(Path configDir, String fileName) throws IOException {
        Path templateDir = Paths.get(stagingProperties.getConfigTemplateDir());
        Path source = templateDir.resolve(fileName);
        Path target = configDir.resolve(fileName);
        if (Files.exists(source)) {
            Files.copy(source, target, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private void deleteRecursively(Path path) throws IOException {
        if (Files.isDirectory(path)) {
            try (var entries = Files.list(path)) {
                for (Path entry : entries.collect(Collectors.toList())) {
                    deleteRecursively(entry);
                }
            }
        }
        Files.deleteIfExists(path);
    }

    private TaskResponse toResponse(Task task) {
        return TaskResponse.builder()
                .id(task.getId())
                .taskName(task.getTaskName())
                .taskDesc(task.getTaskDesc())
                .stagingPath(task.getStagingPath())
                .status(task.getStatus())
                .currentStep(task.getCurrentStep())
                .pid(task.getPid())
                .createdAt(task.getCreatedAt())
                .updatedAt(task.getUpdatedAt())
                .build();
    }
}
