package com.factorfactory.webapp.service;

import com.factorfactory.webapp.dto.StructuredConfigResponse;
import com.factorfactory.webapp.dto.StructuredConfigResponse.*;
import com.factorfactory.webapp.entity.TaskConfig;
import com.factorfactory.webapp.repository.TaskConfigRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;

/**
 * 任务级配置服务
 * 管理每个任务的独立配置，按业务语义分为8个Tab
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class TaskConfigService {

    private final TaskConfigRepository taskConfigRepository;
    private final ObjectMapper objectMapper;
    private final GlobalConfigService globalConfigService;
    private final SystemConfigService systemConfigService;

    // Tab 分区名
    public static final String TAB_ANALYSIS = "analysis_rule";
    public static final String TAB_LLM = "llm_params";
    public static final String TAB_BACKTEST = "backtest_rule";
    public static final String TAB_MARKET = "market_context";
    public static final String TAB_SELECTOR = "selector";
    public static final String TAB_PRESCREEN = "prescreen";
    public static final String TAB_SCORE = "score";
    public static final String TAB_DATA = "data_params";

    public List<StructuredConfigResponse> getAllTabs(Long userId, Long taskId) {
        return List.of(
                buildAnalysisTab(taskId),
                buildLlmTab(userId, taskId),
                buildBacktestTab(taskId),
                buildMarketTab(taskId),
                buildSelectorTab(taskId),
                buildPrescreenTab(taskId),
                buildScoreTab(taskId),
                buildDataTab(taskId)
        );
    }

    public StructuredConfigResponse getTab(Long userId, Long taskId, String tab) {
        return switch (tab) {
            case TAB_ANALYSIS -> buildAnalysisTab(taskId);
            case TAB_LLM -> buildLlmTab(userId, taskId);
            case TAB_BACKTEST -> buildBacktestTab(taskId);
            case TAB_MARKET -> buildMarketTab(taskId);
            case TAB_SELECTOR -> buildSelectorTab(taskId);
            case TAB_PRESCREEN -> buildPrescreenTab(taskId);
            case TAB_SCORE -> buildScoreTab(taskId);
            case TAB_DATA -> buildDataTab(taskId);
            default -> throw new IllegalArgumentException("无效的配置分区: " + tab);
        };
    }

    @Transactional
    public void updateTab(Long taskId, String tab, Map<String, Object> values) {
        TaskConfig config = taskConfigRepository.findByTaskIdAndSection(taskId, tab)
                .orElseGet(() -> TaskConfig.builder().taskId(taskId).section(tab).build());
        try {
            config.setValue(objectMapper.writeValueAsString(values));
        } catch (JsonProcessingException e) {
            throw new RuntimeException("序列化配置失败", e);
        }
        taskConfigRepository.save(config);
        log.info("Task {} config tab {} updated", taskId, tab);
    }

    /**
     * 测试 LLM 连接
     * 向指定 base_url/model/api_key 发送一个简短的 chat completion 请求
     */
    public Map<String, Object> testLlmConnection(Long userId, Map<String, String> params) {
        String baseUrl = params.getOrDefault("base_url", "").trim();
        String model = params.getOrDefault("model", "").trim();
        String apiKey = params.getOrDefault("api_key", "").trim();

        if (baseUrl.isEmpty() || model.isEmpty() || apiKey.isEmpty()) {
            return Map.of("success", false, "message", "请填写完整的 API 地址、模型名称和 API Key");
        }

        // 确保 base_url 以 /chat/completions 结尾
        String url = baseUrl;
        if (!url.endsWith("/chat/completions")) {
            url = url.endsWith("/") ? url + "chat/completions" : url + "/chat/completions";
        }

        try {
            Map<String, Object> requestBody = Map.of(
                    "model", model,
                    "messages", List.of(Map.of("role", "user", "content", "Hi")),
                    "max_tokens", 5
            );

            String jsonBody = objectMapper.writeValueAsString(requestBody);

            java.net.http.HttpClient client = java.net.http.HttpClient.newBuilder()
                    .connectTimeout(java.time.Duration.ofSeconds(10))
                    .build();

            java.net.http.HttpRequest request = java.net.http.HttpRequest.newBuilder()
                    .uri(java.net.URI.create(url))
                    .header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + apiKey)
                    .timeout(java.time.Duration.ofSeconds(30))
                    .POST(java.net.http.HttpRequest.BodyPublishers.ofString(jsonBody))
                    .build();

            java.net.http.HttpResponse<String> response = client.send(request, java.net.http.HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() == 200) {
                return Map.of("success", true, "message", "连接成功！模型 " + model + " 响应正常");
            } else {
                String body = response.body();
                // 尝试提取错误信息
                String errorMsg;
                try {
                    Map<String, Object> errorResp = objectMapper.readValue(body, new TypeReference<Map<String, Object>>() {});
                    Object errorObj = errorResp.get("error");
                    if (errorObj instanceof Map) {
                        errorMsg = String.valueOf(((Map<?, ?>) errorObj).get("message"));
                    } else {
                        errorMsg = body.length() > 200 ? body.substring(0, 200) + "..." : body;
                    }
                } catch (Exception e) {
                    errorMsg = body.length() > 200 ? body.substring(0, 200) + "..." : body;
                }
                return Map.of("success", false, "message", "HTTP " + response.statusCode() + ": " + errorMsg);
            }
        } catch (java.net.ConnectException e) {
            return Map.of("success", false, "message", "连接失败：无法连接到 " + url + "，请检查地址是否正确");
        } catch (java.net.SocketTimeoutException e) {
            return Map.of("success", false, "message", "连接超时：服务器未在30秒内响应");
        } catch (Exception e) {
            return Map.of("success", false, "message", "连接失败：" + e.getMessage());
        }
    }

    /**
     * 获取任务某个分区的配置Map
     */
    public Map<String, Object> getSectionMap(Long taskId, String section) {
        return taskConfigRepository.findByTaskIdAndSection(taskId, section)
                .map(config -> {
                    try {
                        return objectMapper.readValue(config.getValue(), new TypeReference<Map<String, Object>>() {});
                    } catch (JsonProcessingException e) {
                        log.warn("Failed to parse task config: taskId={}, section={}", taskId, section, e);
                        return new HashMap<String, Object>();
                    }
                })
                .orElseGet(HashMap::new);
    }

    // ========================================================================
    // Tab 1: 分析口径
    // ========================================================================
    private StructuredConfigResponse buildAnalysisTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_ANALYSIS);
        List<ConfigGroup> groups = new ArrayList<>();

        // 组1: 运行模式与时间区间
        groups.add(ConfigGroup.builder()
                .name("time_range").label("运行模式与时间区间").icon("Calendar")
                .description("设置分析样本模式和时间区间")
                .fields(List.of(
                        field("run_mode", "运行模式", "select", values.getOrDefault("run_mode", "train"), "train",
                                "train=样本内训练, test=样本外盲测",
                                List.of(opt("样本内训练", "train"), opt("样本外盲测", "test")), "task"),
                        field("train_start_date", "训练开始日期", "date", values.getOrDefault("train_start_date", ""), "",
                                "样本内训练区间起始日期", null, "task"),
                        field("train_end_date", "训练结束日期", "date", values.getOrDefault("train_end_date", ""), "",
                                "样本内训练区间截止日期", null, "task"),
                        field("test_start_date", "测试开始日期", "date", values.getOrDefault("test_start_date", ""), "",
                                "样本外测试区间起始日期", null, "task"),
                        field("test_end_date", "测试结束日期", "date", values.getOrDefault("test_end_date", ""), "",
                                "样本外测试区间截止日期", null, "task")
                ))
                .build());

        // 组2: 训练切分配置
        groups.add(ConfigGroup.builder()
                .name("training_workflow").label("训练切分配置").icon("Split")
                .description("设置训练窗口的切分方式")
                .fields(List.of(
                        field("training_workflow.mode", "切分模式", "select",
                                getNestedValue(values, "training_workflow", "mode"), "static_split",
                                "static_split=固定两段切分, walk_forward=滚动窗口",
                                List.of(opt("固定切分", "static_split"), opt("滚动窗口", "walk_forward")), "task"),
                        field("training_workflow.static_split.discovery_start_date", "Discovery 开始日期", "date",
                                getNestedValue(values, "training_workflow", "static_split", "discovery_start_date"), "",
                                "海选阶段起始日期", null, "task"),
                        field("training_workflow.static_split.discovery_end_date", "Discovery 结束日期", "date",
                                getNestedValue(values, "training_workflow", "static_split", "discovery_end_date"), "",
                                "海选阶段截止日期", null, "task"),
                        field("training_workflow.static_split.validation_start_date", "Validation 开始日期", "date",
                                getNestedValue(values, "training_workflow", "static_split", "validation_start_date"), "",
                                "复试阶段起始日期", null, "task"),
                        field("training_workflow.static_split.validation_end_date", "Validation 结束日期", "date",
                                getNestedValue(values, "training_workflow", "static_split", "validation_end_date"), "",
                                "复试阶段截止日期", null, "task"),
                        numField("training_workflow.walk_forward.discovery_window_months", "滚动窗口 Discovery 月数",
                                getNestedValue(values, "training_workflow", "walk_forward", "discovery_window_months"), 5,
                                "每个滚动窗口中 discovery 阶段长度", 1, 60, 1, 0, true),
                        numField("training_workflow.walk_forward.validation_window_months", "滚动窗口 Validation 月数",
                                getNestedValue(values, "training_workflow", "walk_forward", "validation_window_months"), 1,
                                "每个滚动窗口中 validation 阶段长度", 1, 60, 1, 0, true),
                        numField("training_workflow.walk_forward.step_months", "滚动步长（月）",
                                getNestedValue(values, "training_workflow", "walk_forward", "step_months"), 3,
                                "窗口向前滚动的步长", 1, 60, 1, 0, true),
                        numField("training_workflow.walk_forward.max_windows", "最大窗口数",
                                getNestedValue(values, "training_workflow", "walk_forward", "max_windows"), 2,
                                "最多生成的滚动窗口数量，0=不设上限", 0, 100, 1, 0, false)
                ))
                .build());

        // 组3: 迭代参数
        groups.add(ConfigGroup.builder()
                .name("iteration").label("迭代参数").icon("Refresh")
                .description("大模型迭代轮数")
                .fields(List.of(
                        numField("iteration_count", "迭代轮数",
                                values.getOrDefault("iteration_count", 1), 1,
                                "每个训练窗口 discovery 阶段大模型迭代几轮", 1, 10, 1, 0, true)
                ))
                .build());

        // 组4: 股票池
        groups.add(ConfigGroup.builder()
                .name("stock_pool").label("股票池").icon("DataBoard")
                .description("设置分析的目标股票池")
                .fields(List.of(
                        field("stock_pool.type", "股票池类型", "select",
                                getNestedValue(values, "stock_pool", "type"), "index_components",
                                "当前仅支持指数成分股",
                                List.of(opt("指数成分股", "index_components")), "task"),
                        field("stock_pool.index_code", "指数代码", "text",
                                getNestedValue(values, "stock_pool", "index_code"), "SH000300",
                                "如 SH000300=沪深300, SH000905=中证500, SH000852=中证1000",
                                null, "task"),
                        field("stock_pool.dynamic_membership", "动态成分股", "switch",
                                getNestedValue(values, "stock_pool", "dynamic_membership"), true,
                                "按每个观测日动态更新指数成分", null, "task"),
                        field("stock_pool.include_st", "包含ST股票", "switch",
                                getNestedValue(values, "stock_pool", "include_st"), false,
                                "是否包含ST股票", null, "task"),
                        field("stock_pool.include_new_stock", "包含次新股", "switch",
                                getNestedValue(values, "stock_pool", "include_new_stock"), false,
                                "是否包含次新股", null, "task"),
                        numField("stock_pool.new_stock_days", "新股最小上市天数",
                                getNestedValue(values, "stock_pool", "new_stock_days"), 60,
                                "判定新股的最小上市天数阈值", 1, 1000, 10, 0, false)
                ))
                .build());

        // 组5: 复权口径
        groups.add(ConfigGroup.builder()
                .name("price_adjust").label("复权口径").icon("TrendCharts")
                .description("价格复权方式设置")
                .fields(List.of(
                        field("price_adjust", "复权方式", "select",
                                values.getOrDefault("price_adjust", "pre"), "pre",
                                "前复权=固定最新日价格调整历史",
                                List.of(opt("不复权", "none"), opt("前复权", "pre"), opt("后复权", "post")), "task"),
                        field("price_adjust_reference_date", "前复权参考日", "select",
                                values.getOrDefault("price_adjust_reference_date", "auto"), "auto",
                                "auto=自动取任务load_end, 或指定固定日期",
                                List.of(opt("自动(推荐)", "auto"), opt("指定日期", "custom")), "task")
                ))
                .build());

        // 组6: 调仓节奏
        groups.add(ConfigGroup.builder()
                .name("rebalance").label("调仓节奏").icon("Timer")
                .description("因子分析口径的调仓频率设置")
                .fields(List.of(
                        field("rebalance", "调仓频率", "select",
                                values.getOrDefault("rebalance", "weekly"), "weekly",
                                "调仓周期",
                                List.of(opt("日频", "daily"), opt("周频", "weekly"), opt("月频", "monthly")), "task"),
                        numField("rebalance_interval", "调仓间隔",
                                values.getOrDefault("rebalance_interval", 1), 1,
                                "1=每个周期都调仓, 2=隔一个周期调仓", 1, 12, 1, 0, true),
                        field("rebalance_anchor", "调仓锚点", "select",
                                values.getOrDefault("rebalance_anchor", "first_trading_day_of_week"),
                                "first_trading_day_of_week",
                                "周频/月频调仓的锚点日",
                                List.of(
                                        opt("每周首个交易日", "first_trading_day_of_week"),
                                        opt("每周最后交易日", "last_trading_day_of_week"),
                                        opt("每月首个交易日", "first_trading_day_of_month"),
                                        opt("每月最后交易日", "last_trading_day_of_month")
                                ), "task"),
                        numField("min_valid_ratio_per_observation", "最小有效样本占比",
                                values.getOrDefault("min_valid_ratio_per_observation", 0.9), 0.9,
                                "低于此阈值的观测日不参与IC/IR聚合", 0, 1, 0.05, 2, true)
                ))
                .build());

        // 组7: 预处理
        groups.add(ConfigGroup.builder()
                .name("preprocess").label("预处理设置").icon("Operation")
                .description("候选因子值的预处理方式")
                .fields(List.of(
                        field("preprocess.outlier_method", "离群值处理", "select",
                                getNestedValue(values, "preprocess", "outlier_method"), "none",
                                "截面离群值处理方式",
                                List.of(opt("不处理", "none"), opt("MAD", "mad"), opt("分位法", "quantile"), opt("3Sigma", "sigma")), "task"),
                        field("preprocess.neutralization", "中性化处理", "select",
                                getNestedValue(values, "preprocess", "neutralization"), "none",
                                "截面中性化方式",
                                List.of(opt("不处理", "none"), opt("行业中性化", "industry"), opt("市值中性化", "market_cap"), opt("行业+市值", "industry_market_cap")), "task")
                ))
                .build());

        // 组8: 筹码特征开关
        groups.add(ConfigGroup.builder()
                .name("chip_features").label("筹码特征").icon("Coin")
                .description("是否启用筹码分布特征")
                .fields(List.of(
                        field("enable_chip_features", "启用筹码特征", "switch",
                                values.getOrDefault("enable_chip_features", false), false,
                                "开启后会在基础特征池中加入筹码集中度、获利盘比例等特征，并启用chip_distribution分析师", null, "task")
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_ANALYSIS)
                .sectionLabel("分析口径")
                .sectionDescription("设置因子分析的时间区间、训练切分、股票池、调仓节奏等核心参数")
                .sectionIcon("DataAnalysis")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 2: LLM 参数
    // ========================================================================

    /** Agent 定义：key=agent名称, label=显示名称, description=描述, defaultTemp=默认temperature, stage=阶段 */
    private static final List<AgentDef> AGENT_DEFS = List.of(
            // 阶段1: 专业分析师团队（7个并行）
            new AgentDef("trend_momentum", "趋势动量分析师", "识别趋势延续与动量反转信号", 0.6, 1),
            new AgentDef("reversal_mean_reversion", "反转均值回归分析师", "捕捉均值回归与超跌反弹机会", 0.6, 1),
            new AgentDef("volatility_risk", "波动风险分析师", "评估波动率结构与风险敞口", 0.6, 1),
            new AgentDef("volume_price", "量价配合分析师", "分析量价背离与资金流向", 0.6, 1),
            new AgentDef("microstructure", "微观结构分析师", "挖掘订单簿与交易微观特征", 0.6, 1),
            new AgentDef("chip_distribution", "筹码分布分析师", "分析筹码集中度与获利盘结构", 0.6, 1),
            new AgentDef("fundamental_value_growth", "基本面价值成长分析师", "评估基本面价值与成长性", 0.6, 1),
            // 阶段2: 首席分析师
            new AgentDef("chief_analyst", "首席分析师", "整合7个分析师输出，形成统一方向", 0.6, 2),
            // 阶段3: 因子生成器
            new AgentDef("generator", "因子生成器", "按设计方向生成因子公式", 0.2, 3),
            // 阶段4: 因子评审员
            new AgentDef("reviewer", "因子评审员", "PASS/REJECT 严格评审", 0.2, 4)
    );

    private static final Map<Integer, String> STAGE_LABELS = Map.of(
            1, "阶段 1：专业分析师团队（7 个并行）",
            2, "阶段 2：首席分析师（整合输出）",
            3, "阶段 3：因子生成器",
            4, "阶段 4：因子评审员"
    );

    private record AgentDef(String key, String label, String description, double defaultTemp, int stage) {}

    @SuppressWarnings("unchecked")
    private StructuredConfigResponse buildLlmTab(Long userId, Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_LLM);
        List<ConfigGroup> groups = new ArrayList<>();

        // 合并供应商列表：默认(yml) + 自定义(DB)
        List<Map<String, String>> providers = systemConfigService.getMergedProviders(userId);
        List<SelectOption> providerOptions = providers.stream()
                .map(p -> SelectOption.builder().label(p.get("name")).value(p.get("name")).description(p.get("base_url")).build())
                .toList();

        // 读取已保存的 llm_agents 配置（扁平key格式: llm_agents.trend_momentum.model）
        // 将扁平key转换为嵌套结构: {trend_momentum: {model: xxx, ...}, ...}
        Map<String, Map<String, Object>> savedAgents = new HashMap<>();
        String agentPrefix = "llm_agents.";
        for (Map.Entry<String, Object> entry : values.entrySet()) {
            if (entry.getKey().startsWith(agentPrefix)) {
                String remainder = entry.getKey().substring(agentPrefix.length());
                int dotIdx = remainder.indexOf('.');
                if (dotIdx > 0) {
                    String agentName = remainder.substring(0, dotIdx);
                    String fieldName = remainder.substring(dotIdx + 1);
                    savedAgents.computeIfAbsent(agentName, k -> new HashMap<>())
                            .put(fieldName, entry.getValue());
                }
            }
        }

        // 默认全局配置（用于初始化未配置的Agent）
        String defaultProvider = (String) values.getOrDefault("llm_provider", "");
        String defaultUrl = (String) values.getOrDefault("llm_base_url", "");
        String defaultModel = (String) values.getOrDefault("llm_model", "");
        String defaultApiKey = (String) values.getOrDefault("llm_api_key", "");

        // 如果全局配置为空，尝试从供应商列表自动带出
        if (defaultUrl.isEmpty() && !defaultProvider.isEmpty()) {
            defaultUrl = providers.stream()
                    .filter(p -> p.get("name").equals(defaultProvider))
                    .map(p -> p.get("base_url"))
                    .findFirst().orElse("");
        }

        // 为每个Agent生成配置组，按阶段插入分隔标题
        int lastStage = -1;
        for (AgentDef agent : AGENT_DEFS) {
            // 插入阶段分隔标题
            if (agent.stage() != lastStage) {
                lastStage = agent.stage();
                String stageLabel = STAGE_LABELS.getOrDefault(agent.stage(), "");
                groups.add(ConfigGroup.builder()
                        .name("stage_" + agent.stage()).label(stageLabel).icon("Flag")
                        .description("")
                        .fields(List.of())
                        .build());
            }

            Map<String, Object> agentConfig = savedAgents.getOrDefault(agent.key(), Map.of());

            // Agent配置优先，否则回退到全局默认
            String agentProvider = (String) agentConfig.getOrDefault("llm_provider", defaultProvider);
            String agentUrl = (String) agentConfig.getOrDefault("llm_base_url", defaultUrl);
            String agentModel = (String) agentConfig.getOrDefault("llm_model", defaultModel);
            String agentApiKey = (String) agentConfig.getOrDefault("llm_api_key", defaultApiKey);
            double agentTemp = toDouble(agentConfig.get("temperature"), agent.defaultTemp());
            double agentTimeout = toDouble(agentConfig.get("timeout_seconds"), agent.stage() == 1 ? 60.0 : 120.0);
            int agentRetries = toInt(agentConfig.get("max_retries"), 2);

            // 根据供应商自动带出URL
            if (agentUrl.isEmpty() && !agentProvider.isEmpty()) {
                agentUrl = providers.stream()
                        .filter(p -> p.get("name").equals(agentProvider))
                        .map(p -> p.get("base_url"))
                        .findFirst().orElse("");
            }

            String prefix = "llm_agents." + agent.key();
            groups.add(ConfigGroup.builder()
                    .name("agent_" + agent.key()).label(agent.label()).icon("UserFilled")
                    .description(agent.description())
                    .fields(List.of(
                            field(prefix + ".llm_provider", "供应商", "select",
                                    agentProvider, "",
                                    "选择LLM供应商",
                                    providerOptions, "task"),
                            field(prefix + ".llm_base_url", "API 地址", "text",
                                    agentUrl, "",
                                    "选择供应商后自动填入，也可手动修改", null, "task"),
                            field(prefix + ".llm_model", "模型名称", "text",
                                    agentModel, "",
                                    "如 gpt-4o, deepseek-chat, qwen-max 等", null, "task"),
                            field(prefix + ".llm_api_key", "API Key", "password",
                                    agentApiKey, "",
                                    "对应供应商的 API Key", null, "task"),
                            numField(prefix + ".temperature", "Temperature",
                                    agentTemp, agent.defaultTemp(),
                                    "创意程度，0.2=严谨, 0.6=平衡, 0.9=创意", 0, 2, 0.1, 2, true),
                            numField(prefix + ".timeout_seconds", "超时时间(秒)",
                                    agentTimeout, 60,
                                    "单次请求超时时间", 10, 600, 10, 0, true),
                            numField(prefix + ".max_retries", "最大重试次数",
                                    agentRetries, 2,
                                    "请求失败后最大重试次数", 0, 10, 1, 0, false)
                    ))
                    .build());
        }

        return StructuredConfigResponse.builder()
                .section(TAB_LLM)
                .sectionLabel("LLM 参数")
                .sectionDescription("配置多Agent流水线中每个Agent的LLM供应商和参数")
                .sectionIcon("ChatDotRound")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 3: 回测策略
    // ========================================================================
    private StructuredConfigResponse buildBacktestTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_BACKTEST);
        List<ConfigGroup> groups = new ArrayList<>();

        // 组1: 策略选择
        groups.add(ConfigGroup.builder()
                .name("strategy").label("策略选择").icon("TrendCharts")
                .description("选择回测使用的策略类型")
                .fields(List.of(
                        field("strategy_type", "策略类型", "select",
                                values.getOrDefault("strategy_type", "TopKDropout"), "TopKDropout",
                                "当前可用策略",
                                List.of(opt("TopK-Dropout", "TopKDropout"), opt("SoftTopK", "SoftTopK")), "task")
                ))
                .build());

        // 组2: TopKDropout 参数
        groups.add(ConfigGroup.builder()
                .name("topk_dropout").label("TopK-Dropout 参数").icon("Top")
                .description("买入前N、跌出前M卖出、持仓K只")
                .fields(List.of(
                        numField("TopKDropout.buy_top_n", "买入排名前N",
                                getNestedValue(values, "TopKDropout", "buy_top_n"), 40,
                                "因子得分排名前N的股票进入买入候选", 1, 500, 5, 0, true),
                        numField("TopKDropout.sell_drop_to", "跌出前M名则卖出",
                                getNestedValue(values, "TopKDropout", "sell_drop_to"), 200,
                                "持仓股票跌出前M名则卖出（宽幅缓冲降低换手）", 1, 1000, 10, 0, true),
                        numField("TopKDropout.holding_count", "目标持仓数",
                                getNestedValue(values, "TopKDropout", "holding_count"), 20,
                                "最终目标持仓股票数量", 1, 500, 5, 0, true),
                        field("TopKDropout.weight_mode", "权重模式", "select",
                                getNestedValue(values, "TopKDropout", "weight_mode"), "equal_weight",
                                "持仓权重分配方式",
                                List.of(opt("等权持仓", "equal_weight"), opt("因子分加权", "score_weight")), "task"),
                        numField("TopKDropout.max_drop_per_day", "每日最大换出数",
                                getNestedValue(values, "TopKDropout", "max_drop_per_day"), 5,
                                "每次调仓最多换出数量，控制换手率", 0, 100, 1, 0, false),
                        numField("TopKDropout.min_score_coverage", "因子分数保有率阈值",
                                getNestedValue(values, "TopKDropout", "min_score_coverage"), 0.90,
                                "低于此值本调仓日不操作", 0, 1, 0.05, 2, false)
                ))
                .build());

        // 组3: SoftTopK 参数
        groups.add(ConfigGroup.builder()
                .name("soft_topk").label("SoftTopK 参数").icon("MagicStick")
                .description("按分数连续分配权重，而非简单等权")
                .fields(List.of(
                        field("SoftTopK.weight_func", "权重函数", "select",
                                getNestedValue(values, "SoftTopK", "weight_func"), "softmax",
                                "softmax=对高分更敏感, rank_power=按排名幂律衰减",
                                List.of(opt("Softmax", "softmax"), opt("Rank Power", "rank_power")), "task"),
                        numField("SoftTopK.holding_count", "持仓股票数",
                                getNestedValue(values, "SoftTopK", "holding_count"), 30,
                                "按因子分从高到低选前N只", 1, 500, 5, 0, true),
                        numField("SoftTopK.softmax_temperature", "Softmax 温度",
                                getNestedValue(values, "SoftTopK", "softmax_temperature"), 0.7,
                                "T越小集中度越高，T越大越接近等权", 0.01, 10, 0.1, 2, true),
                        numField("SoftTopK.rank_power_alpha", "Rank Power 衰减参数",
                                getNestedValue(values, "SoftTopK", "rank_power_alpha"), 1.0,
                                "α越大头部越集中，α越小越接近等权", 0.1, 10, 0.1, 2, true),
                        numField("SoftTopK.min_score_coverage", "因子分数保有率阈值",
                                getNestedValue(values, "SoftTopK", "min_score_coverage"), 0.90,
                                "低于此值本调仓日不操作", 0, 1, 0.05, 2, false)
                ))
                .build());

        // 组4: 择时过滤
        groups.add(ConfigGroup.builder()
                .name("market_timing").label("择时过滤").icon("Sunrise")
                .description("叠加层：根据市场状态调整仓位")
                .fields(List.of(
                        field("MarketTiming.enabled", "启用择时", "switch",
                                getNestedValue(values, "MarketTiming", "enabled"), true,
                                "关闭则按主策略满仓执行", null, "task"),
                        field("MarketTiming.market_indicator", "市场指标", "select",
                                getNestedValue(values, "MarketTiming", "market_indicator"), "EMA_60",
                                "当前仅支持 EMA_60",
                                List.of(opt("EMA 60日", "EMA_60")), "task"),
                        numField("MarketTiming.reduce_to", "风险状态目标仓位",
                                getNestedValue(values, "MarketTiming", "reduce_to"), 0.6,
                                "择时触发后的目标暴露比例(0~1)", 0, 1, 0.05, 2, true),
                        field("MarketTiming.stock_open_filter", "个股开仓过滤", "select",
                                getNestedValue(values, "MarketTiming", "stock_open_filter"), "rsi",
                                "仅对新开仓生效，老持仓不受影响",
                                List.of(opt("不开启", "none"), opt("EMA过滤", "ema"), opt("RSI过滤", "rsi")), "task"),
                        numField("MarketTiming.stock_ema_period", "个股EMA周期",
                                getNestedValue(values, "MarketTiming", "stock_ema_period"), 60,
                                "仅 stock_open_filter=ema 时生效", 5, 250, 5, 0, false),
                        numField("MarketTiming.rsi_period", "RSI计算窗口",
                                getNestedValue(values, "MarketTiming", "rsi_period"), 14,
                                "仅 stock_open_filter=rsi 时生效", 5, 100, 1, 0, false),
                        numField("MarketTiming.rsi_buy_max", "RSI开仓上限",
                                getNestedValue(values, "MarketTiming", "rsi_buy_max"), 70.0,
                                "RSI>此值禁止新开仓", 50, 100, 1, 0, false)
                ))
                .build());

        // 组5: 执行参数
        groups.add(ConfigGroup.builder()
                .name("execution").label("执行参数").icon("Money")
                .description("回测交易执行参数")
                .fields(List.of(
                        numField("Execution.initial_cash", "初始资金（元）",
                                getNestedValue(values, "Execution", "initial_cash"), 1000000,
                                "回测起始总资产", 100000, 100000000, 100000, 0, true),
                        field("Execution.trade_price", "交易价格", "select",
                                getNestedValue(values, "Execution", "trade_price"), "next_open",
                                "信号后使用哪个价格成交",
                                List.of(opt("下一交易日开盘价", "next_open"), opt("下一交易日收盘价", "next_close")), "task"),
                        numField("Execution.buy_cost", "买入费率",
                                getNestedValue(values, "Execution", "buy_cost"), 0.0015,
                                "包含佣金、过户费等，0.0015=千分之1.5", 0, 0.01, 0.0005, 4, true),
                        numField("Execution.sell_cost", "卖出费率",
                                getNestedValue(values, "Execution", "sell_cost"), 0.0025,
                                "0.0025=千分之2.5（不含印花税）", 0, 0.01, 0.0005, 4, true),
                        numField("Execution.stamp_duty", "印花税率",
                                getNestedValue(values, "Execution", "stamp_duty"), 0.001,
                                "卖出时额外加收，0.001=千分之1", 0, 0.01, 0.0005, 4, true),
                        numField("Execution.slippage", "滑点成本",
                                getNestedValue(values, "Execution", "slippage"), 0.0005,
                                "模拟真实交易吃单导致的价差", 0, 0.01, 0.0001, 4, true),
                        numField("Execution.cash_buffer_ratio", "资金缓冲比例",
                                getNestedValue(values, "Execution", "cash_buffer_ratio"), 0.02,
                                "预留资金防止手续费导致订单失败", 0, 0.1, 0.01, 2, true),
                        field("Execution.enable_detailed_backtest_log", "详细回测日志", "switch",
                                getNestedValue(values, "Execution", "enable_detailed_backtest_log"), false,
                                "开启后输出逐因子详细交易日志", null, "task")
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_BACKTEST)
                .sectionLabel("回测策略")
                .sectionDescription("配置回测策略类型、参数、择时过滤和执行参数")
                .sectionIcon("TrendCharts")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 4: 市场环境
    // ========================================================================
    private StructuredConfigResponse buildMarketTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_MARKET);
        List<ConfigGroup> groups = new ArrayList<>();

        // 组1: 窗口参数
        groups.add(ConfigGroup.builder()
                .name("windows").label("窗口参数").icon("Timer")
                .description("程序往前回头看多少个交易日来判断市场状态")
                .fields(List.of(
                        numField("windows.trend_days", "趋势窗口（天）",
                                getNestedValue(values, "windows", "trend_days"), 20,
                                "看最近多少天的市场整体累计表现", 5, 250, 5, 0, true),
                        numField("windows.volatility_days", "波动窗口（天）",
                                getNestedValue(values, "windows", "volatility_days"), 20,
                                "看最近多少天的日涨跌幅波动", 5, 250, 5, 0, true),
                        numField("windows.dispersion_days", "分化窗口（天）",
                                getNestedValue(values, "windows", "dispersion_days"), 20,
                                "看最近多少天的股票间差异", 5, 250, 5, 0, true),
                        numField("windows.rank_lookback_days", "历史分位回看（天）",
                                getNestedValue(values, "windows", "rank_lookback_days"), 250,
                                "把当前指标放到过去多少天里算分位", 50, 500, 10, 0, true),
                        numField("windows.northbound_days", "北向资金窗口（天）",
                                getNestedValue(values, "windows", "northbound_days"), 5,
                                "看最近几天的北向净流入累计", 1, 30, 1, 0, true),
                        numField("windows.margin_days", "两融情绪窗口（天）",
                                getNestedValue(values, "windows", "margin_days"), 5,
                                "看最近几天的融资净流累计", 1, 30, 1, 0, true),
                        numField("windows.warmup_trading_days", "预热天数",
                                getNestedValue(values, "windows", "warmup_trading_days"), 260,
                                "程序在训练起点前多读的历史天数", 100, 500, 10, 0, true)
                ))
                .build());

        // 组2: 标签阈值
        groups.add(ConfigGroup.builder()
                .name("thresholds").label("标签阈值").icon("ScaleToOriginal")
                .description("连续数值算出来后，什么程度才算高/低")
                .fields(List.of(
                        numField("thresholds.trend_up", "趋势上行阈值",
                                getNestedValue(values, "thresholds", "trend_up"), 0.03,
                                "累计涨幅>=此值记为上行（如0.03=3%）", -1, 1, 0.01, 2, true),
                        numField("thresholds.trend_down", "趋势下行阈值",
                                getNestedValue(values, "thresholds", "trend_down"), -0.03,
                                "累计涨幅<=此值记为下行", -1, 1, 0.01, 2, true),
                        numField("thresholds.rank_high", "分位高阈值",
                                getNestedValue(values, "thresholds", "rank_high"), 0.67,
                                "历史分位>=此值记为高", 0.5, 1, 0.01, 2, true),
                        numField("thresholds.rank_low", "分位低阈值",
                                getNestedValue(values, "thresholds", "rank_low"), 0.33,
                                "历史分位<=此值记为低", 0, 0.5, 0.01, 2, true),
                        numField("thresholds.breadth_risk_on", "广度偏强阈值",
                                getNestedValue(values, "thresholds", "breadth_risk_on"), 0.62,
                                "上涨股票占比>=此值记为普涨", 0.5, 1, 0.01, 2, true),
                        numField("thresholds.breadth_risk_off", "广度偏弱阈值",
                                getNestedValue(values, "thresholds", "breadth_risk_off"), 0.38,
                                "上涨股票占比<=此值记为普跌", 0, 0.5, 0.01, 2, true),
                        numField("thresholds.northbound_inflow_high", "北向偏流入阈值",
                                getNestedValue(values, "thresholds", "northbound_inflow_high"), 0.67,
                                "北向资金分位>=此值记为偏流入", 0.5, 1, 0.01, 2, true),
                        numField("thresholds.northbound_outflow_low", "北向偏流出阈值",
                                getNestedValue(values, "thresholds", "northbound_outflow_low"), 0.33,
                                "北向资金分位<=此值记为偏流出", 0, 0.5, 0.01, 2, true),
                        numField("thresholds.leverage_hot_high", "两融升温阈值",
                                getNestedValue(values, "thresholds", "leverage_hot_high"), 0.67,
                                "融资分位>=此值记为升温", 0.5, 1, 0.01, 2, true),
                        numField("thresholds.leverage_cold_low", "两融降温阈值",
                                getNestedValue(values, "thresholds", "leverage_cold_low"), 0.33,
                                "融资分位<=此值记为降温", 0, 0.5, 0.01, 2, true)
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_MARKET)
                .sectionLabel("市场环境")
                .sectionDescription("配置市场环境判断的窗口参数和标签阈值")
                .sectionIcon("Sunrise")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 5: 窗口选择
    // ========================================================================
    private StructuredConfigResponse buildSelectorTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_SELECTOR);
        List<ConfigGroup> groups = new ArrayList<>();

        groups.add(ConfigGroup.builder()
                .name("selector").label("训练窗口选择器").icon("Aim")
                .description("为当前test窗口推荐最相似的历史训练窗口")
                .fields(List.of(
                        numField("lookback_years", "回看历史年数",
                                values.getOrDefault("lookback_years", 10), 10,
                                "以test_start_date为锚点向前回看多少年", 1, 30, 1, 0, true),
                        field("recommend_span_months", "推荐窗口长度（月）", "tag_select",
                                values.getOrDefault("recommend_span_months", List.of(12, 18, 24)),
                                List.of(12, 18, 24),
                                "候选训练窗口的长度，支持多种长度同时评估",
                                List.of(opt("12个月", "12"), opt("18个月", "18"), opt("24个月", "24"), opt("36个月", "36")), "task"),
                        numField("top_k_similar_months", "Top-K 相似月份数",
                                values.getOrDefault("top_k_similar_months", 4), 4,
                                "找出与test状态最相似的前K个月", 1, 20, 1, 0, true),
                        numField("score_similarity_weight", "相似度权重",
                                values.getOrDefault("score_similarity_weight", 0.7), 0.7,
                                "窗口内平均相似度占总分的权重", 0, 1, 0.05, 2, true),
                        numField("score_coverage_weight", "覆盖度权重",
                                values.getOrDefault("score_coverage_weight", 0.3), 0.3,
                                "窗口命中Top-K相似月份的比例占总分的权重", 0, 1, 0.05, 2, true),
                        field("disable_dynamic_membership", "关闭动态成分股", "switch",
                                values.getOrDefault("disable_dynamic_membership", true), true,
                                "开启可减少指数成分回溯请求，加快运行速度", null, "task")
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_SELECTOR)
                .sectionLabel("窗口选择")
                .sectionDescription("配置训练窗口选择器参数，为test窗口推荐最佳训练区间")
                .sectionIcon("Aim")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 6: 预筛门槛
    // ========================================================================
    private StructuredConfigResponse buildPrescreenTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_PRESCREEN);
        List<ConfigGroup> groups = new ArrayList<>();

        groups.add(ConfigGroup.builder()
                .name("prescreen").label("因子预筛门槛").icon("Filter")
                .description("不满足全部条件的因子将被跳过回测")
                .fields(List.of(
                        numField("min_rank_ic_to_backtest", "Rank IC 绝对值阈值",
                                values.getOrDefault("min_rank_ic_to_backtest", 0.02), 0.02,
                                "行业通常0.02-0.03以上算有效", 0, 0.5, 0.005, 3, true),
                        numField("min_rank_ic_ir_to_backtest", "Rank IC IR 绝对值阈值",
                                values.getOrDefault("min_rank_ic_ir_to_backtest", 0.2), 0.2,
                                "代表稳定性，行业通常0.2以上", 0, 5, 0.05, 2, true),
                        numField("min_positive_ic_ratio", "IC胜率阈值",
                                values.getOrDefault("min_positive_ic_ratio", 0.4), 0.4,
                                "IC为正的胜率，要求不偏离50%太多", 0, 1, 0.05, 2, true),
                        field("enable_direction_filter", "方向过滤", "switch",
                                values.getOrDefault("enable_direction_filter", false), false,
                                "是否要求LLM猜测方向与样本内经验方向一致", null, "task")
                ))
                .build());

        // 特征体检参数
        groups.add(ConfigGroup.builder()
                .name("health_check").label("特征体检参数").icon("FirstAidKit")
                .description("生成健康检查摘要时的参数")
                .fields(List.of(
                        numField("summary_top_k", "最强/最弱特征数",
                                values.getOrDefault("summary_top_k", 3), 3,
                                "挑选几个最强和最弱的特征给大模型看", 1, 20, 1, 0, true),
                        numField("unstable_top_k", "最不稳定特征数",
                                values.getOrDefault("unstable_top_k", 3), 3,
                                "挑选几个最不稳定的特征给大模型看", 1, 20, 1, 0, true),
                        numField("high_corr_threshold", "高相关阈值",
                                values.getOrDefault("high_corr_threshold", 0.5), 0.5,
                                "相关系数绝对值超过此值视为高度重合", 0, 1, 0.05, 2, true),
                        numField("max_missing_ratio", "缺失率阈值",
                                values.getOrDefault("max_missing_ratio", 0.2), 0.2,
                                "缺失率超过此值的特征标记为review", 0, 1, 0.05, 2, true)
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_PRESCREEN)
                .sectionLabel("预筛门槛")
                .sectionDescription("配置因子预筛和特征体检的阈值参数")
                .sectionIcon("Filter")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 7: 评分权重
    // ========================================================================
    private StructuredConfigResponse buildScoreTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_SCORE);
        List<ConfigGroup> groups = new ArrayList<>();

        groups.add(ConfigGroup.builder()
                .name("score_weights").label("因子评分权重").icon("TrendCharts")
                .description("所有正权重之和 - 负权重之和 = 1.0")
                .fields(List.of(
                        numField("weights.ic_stability", "IC 稳定性权重",
                                getNestedValue(values, "weights", "ic_stability"), 0.35,
                                "Rank IC IR的归一化值，衡量因子预测能力的稳定性", 0, 1, 0.05, 2, true),
                        numField("weights.annual_return", "年化收益权重",
                                getNestedValue(values, "weights", "annual_return"), 0.50,
                                "年化收益率的归一化值，衡量因子实际盈利能力", 0, 1, 0.05, 2, true),
                        numField("weights.drawdown", "回撤惩罚权重",
                                getNestedValue(values, "weights", "drawdown"), 0.05,
                                "最大回撤绝对值的归一化值，惩罚高风险因子", 0, 1, 0.05, 2, true),
                        numField("weights.turnover", "换手率惩罚权重",
                                getNestedValue(values, "weights", "turnover"), 0.05,
                                "换手率的归一化值，惩罚过度交易", 0, 1, 0.05, 2, true),
                        numField("weights.instability", "IC方向不稳定惩罚权重",
                                getNestedValue(values, "weights", "instability"), 0.05,
                                "(1-positive_ic_ratio)的归一化值，惩罚IC方向不稳定", 0, 1, 0.05, 2, true),
                        numField("negative_return_penalty", "负收益惩罚",
                                values.getOrDefault("negative_return_penalty", 0.5), 0.5,
                                "当年化收益率<0时，total_score额外减去此值", 0, 2, 0.1, 2, true)
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_SCORE)
                .sectionLabel("评分权重")
                .sectionDescription("配置因子评分公式中各分项的权重")
                .sectionIcon("TrendCharts")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 8: 数据参数
    // ========================================================================
    private StructuredConfigResponse buildDataTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_DATA);
        List<ConfigGroup> groups = new ArrayList<>();

        groups.add(ConfigGroup.builder()
                .name("data_params").label("数据参数").icon("Coin")
                .description("数据缓存和频率相关参数")
                .fields(List.of(
                        numField("placeholder_expire_days", "占位符过期天数",
                                values.getOrDefault("placeholder_expire_days", 3), 3,
                                "Tushare暂时获取不到数据时插入占位符，超过此天数自动过期重新拉取", 1, 30, 1, 0, true),
                        field("freq", "数据频率", "select",
                                values.getOrDefault("freq", "day"), "day",
                                "当前仅支持日线",
                                List.of(opt("日线", "day")), "task")
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_DATA)
                .sectionLabel("数据参数")
                .sectionDescription("数据缓存策略和频率配置")
                .sectionIcon("Coin")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // 工具方法
    // ========================================================================

    private ConfigField field(String key, String label, String type, Object value, Object defaultValue,
                              String description, List<SelectOption> options, String source) {
        return ConfigField.builder()
                .key(key).label(label).type(type)
                .value(value).defaultValue(defaultValue)
                .description(description)
                .options(options)
                .source(source)
                .build();
    }

    /** 带数值约束的字段构造 */
    private ConfigField numField(String key, String label, Object value, Object defaultValue,
                                 String description, Number min, Number max, Number step, Integer precision,
                                 boolean required) {
        return ConfigField.builder()
                .key(key).label(label).type("number")
                .value(value).defaultValue(defaultValue)
                .description(description)
                .min(min).max(max).step(step).precision(precision)
                .required(required)
                .source("task")
                .build();
    }

    private SelectOption opt(String label, String value) {
        return SelectOption.builder().label(label).value(value).build();
    }

    /** 安全地将Object转为double，支持Number/String/null */
    private double toDouble(Object val, double defaultVal) {
        if (val == null) return defaultVal;
        if (val instanceof Number) return ((Number) val).doubleValue();
        try { return Double.parseDouble(val.toString()); } catch (NumberFormatException e) { return defaultVal; }
    }

    /** 安全地将Object转为int，支持Number/String/null */
    private int toInt(Object val, int defaultVal) {
        if (val == null) return defaultVal;
        if (val instanceof Number) return ((Number) val).intValue();
        try { return Integer.parseInt(val.toString()); } catch (NumberFormatException e) { return defaultVal; }
    }

    /**
     * 从Map中获取嵌套值，同时支持扁平key和嵌套Map两种格式。
     * 例如 getNestedValue(map, "training_workflow", "mode") 会尝试:
     * 1. 扁平key: map.get("training_workflow.mode")
     * 2. 嵌套Map: map.get("training_workflow").get("mode")
     */
    @SuppressWarnings("unchecked")
    private Object getNestedValue(Map<String, Object> map, String... keys) {
        if (keys.length == 0) return null;
        if (keys.length == 1) return map.get(keys[0]);

        // 先尝试扁平key
        String flatKey = String.join(".", keys);
        if (map.containsKey(flatKey)) {
            return map.get(flatKey);
        }

        // 再尝试嵌套Map
        Object current = map;
        for (String key : keys) {
            if (current == null) return null;
            if (current instanceof Map) {
                current = ((Map<String, Object>) current).get(key);
            } else {
                return null;
            }
        }
        return current;
    }
}
