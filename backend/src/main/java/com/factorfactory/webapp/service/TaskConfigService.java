package com.factorfactory.webapp.service;

import com.factorfactory.webapp.config.StagingProperties;
import com.factorfactory.webapp.dto.SelectorApplyRequest;
import com.factorfactory.webapp.dto.StructuredConfigResponse;
import com.factorfactory.webapp.dto.StructuredConfigResponse.*;
import com.factorfactory.webapp.entity.TaskConfig;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.repository.TaskConfigRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Duration;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 任务级配置服务
 * 管理每个任务的独立配置，按业务语义分为9个Tab
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class TaskConfigService {

    private final TaskConfigRepository taskConfigRepository;
    private final ObjectMapper objectMapper;
    private final GlobalConfigService globalConfigService;
    private final SystemConfigService systemConfigService;
    private final StagingProperties stagingProperties;
    private final ExecutionService executionService;
    private final TaskService taskService;

    // Tab 分区名
    public static final String TAB_ANALYSIS = "analysis_rule";
    public static final String TAB_STOCK_POOL = "stock_pool";
    public static final String TAB_FEATURE_POOL = "feature_pool";
    public static final String TAB_LLM = "llm_params";
    public static final String TAB_BACKTEST = "backtest_rule";
    public static final String TAB_MARKET = "market_context";
    public static final String TAB_LABEL = "label";
    public static final String TAB_SELECTOR = "selector";
    public static final String TAB_PRESCREEN = "prescreen";
    public static final String TAB_SCORE = "score";
    public static final String TAB_TUSHARE = "tushare";

    public List<StructuredConfigResponse> getAllTabs(Long userId, Long taskId) {
        // 查询该任务已在数据库中保存过的 section
        Set<String> savedSections = taskConfigRepository.findByTaskId(taskId).stream()
                .map(TaskConfig::getSection)
                .collect(Collectors.toSet());

        List<StructuredConfigResponse> tabs = List.of(
                buildFeaturePoolTab(taskId),
                buildStockPoolTab(taskId),
                buildTushareTab(taskId),
                buildMarketTab(taskId),
                buildLabelTab(taskId),
                buildPrescreenScoreTab(taskId),
                buildBacktestTab(taskId),
                buildLlmTab(userId, taskId),
                buildSelectorTab(taskId)
        );

        for (StructuredConfigResponse tab : tabs) {
            tab.setSaved(savedSections.contains(tab.getSection()));
        }
        return tabs;
    }

    /**
     * 获取该任务在 task_config 表中已经存在的全部 section 名集合。
     * 前端用来判断"任务是否已完成配置"——只要必需的 section 都在这个列表里就视为已配置。
     */
    public List<String> getSavedSections(Long userId, Long taskId) {
        taskService.findTaskBelongsToUser(userId, taskId);
        return taskConfigRepository.findByTaskId(taskId).stream()
                .map(TaskConfig::getSection)
                .distinct()
                .collect(Collectors.toList());
    }

    public StructuredConfigResponse getTab(Long userId, Long taskId, String tab) {
        return switch (tab) {
            case TAB_STOCK_POOL -> buildStockPoolTab(taskId);
            case TAB_TUSHARE -> buildTushareTab(taskId);
            case TAB_FEATURE_POOL -> buildFeaturePoolTab(taskId);
            case TAB_LLM -> buildLlmTab(userId, taskId);
            case TAB_BACKTEST -> buildBacktestTab(taskId);
            case TAB_MARKET -> buildMarketTab(taskId);
            case TAB_LABEL -> buildLabelTab(taskId);
            case TAB_SELECTOR -> buildSelectorTab(taskId);
            case TAB_PRESCREEN -> buildPrescreenScoreTab(taskId);
            default -> throw new IllegalArgumentException("无效的配置分区: " + tab);
        };
    }

    @Transactional
    public void updateTab(Long taskId, String tab, Map<String, Object> values) {
        // 过滤掉只读的预览字段，避免大对象存入数据库
        Map<String, Object> filtered = new LinkedHashMap<>(values);
        filtered.remove("feature_pool_preview");

        // Tushare Tab：自动复权参考日处理
        if (TAB_TUSHARE.equals(tab)) {
            boolean auto = (boolean) filtered.getOrDefault("price_adjust_auto", true);
            if (auto) {
                filtered.put("price_adjust_reference_date", "auto");
            }
        }

        // 市场环境 Tab：参数约束校验
        if (TAB_MARKET.equals(tab)) {
            validateMarketContext(filtered);
        }

        // 窗口选择 Tab：test 日期与迭代轮数属于 analysis_rule，同步保存到 analysis_rule section
        if (TAB_SELECTOR.equals(tab)) {
            validateSelector(filtered);
            Map<String, Object> analysisValues = getSectionMap(taskId, TAB_ANALYSIS);
            boolean changed = false;
            if (filtered.containsKey("test_start_date")) {
                analysisValues.put("test_start_date", filtered.get("test_start_date"));
                changed = true;
            }
            if (filtered.containsKey("test_end_date")) {
                analysisValues.put("test_end_date", filtered.get("test_end_date"));
                changed = true;
            }
            if (filtered.containsKey("iteration_count")) {
                analysisValues.put("iteration_count", filtered.get("iteration_count"));
                changed = true;
            }
            if (filtered.containsKey("manual_train_start_date")) {
                analysisValues.put("train_start_date", filtered.get("manual_train_start_date"));
                changed = true;
            }
            if (filtered.containsKey("manual_train_end_date")) {
                analysisValues.put("train_end_date", filtered.get("manual_train_end_date"));
                changed = true;
            }
            // training_workflow 参数同步到 analysis_rule
            if (filtered.containsKey("training_workflow.mode")) {
                Object twRaw = analysisValues.get("training_workflow");
                Map<String, Object> tw = twRaw instanceof Map ? (Map<String, Object>) twRaw : new LinkedHashMap<>();
                tw.put("mode", filtered.get("training_workflow.mode"));
                // 同步 static_split / walk_forward 子参数
                for (Map.Entry<String, Object> entry : filtered.entrySet()) {
                    String key = entry.getKey();
                    if (key.startsWith("training_workflow.static_split.")) {
                        Object ssRaw = tw.get("static_split");
                        Map<String, Object> ss = ssRaw instanceof Map ? (Map<String, Object>) ssRaw : new LinkedHashMap<>();
                        ss.put(key.substring("training_workflow.static_split.".length()), entry.getValue());
                        tw.put("static_split", ss);
                    }
                    if (key.startsWith("training_workflow.walk_forward.")) {
                        Object wfRaw = tw.get("walk_forward");
                        Map<String, Object> wf = wfRaw instanceof Map ? (Map<String, Object>) wfRaw : new LinkedHashMap<>();
                        wf.put(key.substring("training_workflow.walk_forward.".length()), entry.getValue());
                        tw.put("walk_forward", wf);
                    }
                }
                analysisValues.put("training_workflow", tw);
                changed = true;
            }
            if (changed) {
                saveSection(taskId, TAB_ANALYSIS, analysisValues);
            }
            // 以下字段不属于 selector 算法配置，不应保存到 selector section
            filtered.remove("test_start_date");
            filtered.remove("test_end_date");
            filtered.remove("iteration_count");
            filtered.remove("train_window_source");
            filtered.remove("manual_train_start_date");
            filtered.remove("manual_train_end_date");
            // training_workflow 已经同步到 analysis_rule，不应保留在 selector
            filtered.entrySet().removeIf(entry -> entry.getKey().startsWith("training_workflow."));
        }

        // 因子初筛与打分规则 Tab：拆分保存到 prescreen 和 score 两个 section
        if (TAB_PRESCREEN.equals(tab)) {
            validateScoreWeights(filtered);
            Map<String, Object> prescreenValues = new LinkedHashMap<>();
            Map<String, Object> scoreValues = new LinkedHashMap<>();
            for (Map.Entry<String, Object> entry : filtered.entrySet()) {
                String key = entry.getKey();
                if (key.startsWith("weights.") || key.equals("negative_return_penalty")) {
                    scoreValues.put(key, entry.getValue());
                } else {
                    prescreenValues.put(key, entry.getValue());
                }
            }
            saveSection(taskId, TAB_PRESCREEN, prescreenValues);
            saveSection(taskId, TAB_SCORE, scoreValues);
            return;
        }

        TaskConfig config = taskConfigRepository.findByTaskIdAndSection(taskId, tab)
                .orElseGet(() -> TaskConfig.builder().taskId(taskId).section(tab).build());
        try {
            config.setValue(objectMapper.writeValueAsString(sanitizeDoubles(filtered)));
        } catch (JsonProcessingException e) {
            throw new RuntimeException("配置序列化失败", e);
        }
        taskConfigRepository.save(config);
    }

    private void saveSection(Long taskId, String section, Map<String, Object> values) {
        TaskConfig config = taskConfigRepository.findByTaskIdAndSection(taskId, section)
                .orElseGet(() -> TaskConfig.builder().taskId(taskId).section(section).build());
        try {
            config.setValue(objectMapper.writeValueAsString(sanitizeDoubles(values)));
        } catch (JsonProcessingException e) {
            throw new RuntimeException("配置序列化失败", e);
        }
        taskConfigRepository.save(config);
    }

    /**
     * 把 Map / List 中的 Double / Float 转换为 BigDecimal，避免 Jackson 序列化时输出科学计数法（如 5.0E-4）。
     */
    @SuppressWarnings("unchecked")
    private Object sanitizeDoubles(Object value) {
        if (value instanceof Double) {
            return new java.math.BigDecimal(value.toString());
        } else if (value instanceof Float) {
            return new java.math.BigDecimal(value.toString());
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

    // ========================================================================
    // Selector 相关方法
    // ========================================================================

    @Transactional
    public void runSelector(Long userId, Long taskId) {
        taskService.findTaskBelongsToUser(userId, taskId);
        prepareRunMode(userId, taskId, "train");
        executionService.runSelectorScript(taskId);
    }

    /**
     * 启动流水线脚本前的预处理：把 analysis_rule.run_mode 硬编码为指定值，
     * 然后把数据库中的最新配置全量刷新到 staging 目录的 YAML 副本中。
     * 由 ExecutionService.startTask 与 TaskConfigService.runSelector 共同调用。
     */
    @Transactional
    public void prepareRunMode(Long userId, Long taskId, String runMode) {
        Map<String, Object> analysisValues = getSectionMap(taskId, TAB_ANALYSIS);
        analysisValues.put("run_mode", runMode);
        saveSection(taskId, TAB_ANALYSIS, analysisValues);
        taskService.refreshConfigCopy(userId, taskId);
    }

    public Map<String, Object> getSelectorResult(Long userId, Long taskId) {
        var task = taskService.findTaskBelongsToUser(userId, taskId);
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("running", executionService.isSelectorRunning(taskId));
        response.put("progress_logs", executionService.getSelectorProgressLogs(taskId, 120));
        Path selectorDir = Paths.get(task.getStagingPath(), "outputs", "selector");
        Path resultPath = selectorDir.resolve("recommended_train_window.json");
        Path testContextPath = selectorDir.resolve("test_market_context.json");

        if (!Files.exists(resultPath)) {
            response.put("ready", false);
            return response;
        }

        try {
            String json = Files.readString(resultPath, StandardCharsets.UTF_8);
            Map<String, Object> result = objectMapper.readValue(json, new TypeReference<>() {});
            response.putAll(toSelectorUiResult(result));
            response.put("ready", true);

            if (Files.exists(testContextPath)) {
                Map<String, Object> testContext = objectMapper.readValue(
                        Files.readString(testContextPath, StandardCharsets.UTF_8), new TypeReference<>() {});
                Object context = testContext.get("test_context");
                if (context instanceof Map<?, ?> contextMap) {
                    response.put("test_summary", contextMap.get("summary_text"));
                    response.put("test_labels", contextMap.get("labels"));
                }
            }
            return response;
        } catch (IOException e) {
            log.error("读取选择器结果失败: taskId={}", taskId, e);
            response.put("ready", false);
            response.put("error", "读取结果失败");
            return response;
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> toSelectorUiResult(Map<String, Object> result) {
        Map<String, Object> ui = new LinkedHashMap<>(result);

        Object recommendedRaw = result.get("recommended_train_window");
        if (recommendedRaw instanceof Map<?, ?> recommended) {
            Map<String, Object> bestWindow = new LinkedHashMap<>();
            bestWindow.put("start", recommended.get("train_start_date"));
            bestWindow.put("end", recommended.get("train_end_date"));
            bestWindow.put("span", recommended.get("recommend_span_months"));
            bestWindow.put("score", recommended.get("total_score"));
            bestWindow.put("meanSimilarity", recommended.get("mean_similarity_score"));
            bestWindow.put("topHitCount", recommended.get("top_similar_hit_count"));
            bestWindow.put("topCoverage", recommended.get("top_similar_coverage_score"));
            ui.put("best_window", bestWindow);
        }

        Map<String, Object> spanWindow = new LinkedHashMap<>();
        Object spanReportsRaw = result.get("span_reports");
        if (spanReportsRaw instanceof List<?> spanReports) {
            for (Object reportRaw : spanReports) {
                if (!(reportRaw instanceof Map<?, ?> report)) {
                    continue;
                }
                Object span = report.get("recommend_span_months");
                Object topWindowsRaw = report.get("top_windows");
                List<Map<String, Object>> windows = new ArrayList<>();
                if (topWindowsRaw instanceof List<?> topWindows) {
                    for (Object windowRaw : topWindows) {
                        if (windowRaw instanceof Map<?, ?> window) {
                            Map<String, Object> item = new LinkedHashMap<>();
                            item.put("start", window.get("train_start_date"));
                            item.put("end", window.get("train_end_date"));
                            item.put("score", window.get("total_score"));
                            item.put("meanSimilarity", window.get("mean_similarity_score"));
                            item.put("topHitCount", window.get("top_similar_hit_count"));
                            item.put("topCoverage", window.get("top_similar_coverage_score"));
                            windows.add(item);
                        }
                    }
                }
                if (span != null) {
                    spanWindow.put(String.valueOf(span), windows);
                }
            }
        }
        ui.put("span_window", spanWindow);
        return ui;
    }

    @Transactional
    public void applySelectorResult(Long userId, Long taskId, SelectorApplyRequest request) {
        taskService.findTaskBelongsToUser(userId, taskId);
        // 1. 保存 selector section 的 training_workflow.mode 和 window_applied 标志
        Map<String, Object> selectorValues = getSectionMap(taskId, TAB_SELECTOR);
        selectorValues.put("training_workflow.mode", request.getMode());
        selectorValues.put("window_applied", true);
        saveSection(taskId, TAB_SELECTOR, selectorValues);

        // 2. 更新 analysis_rule section 的 training_workflow 和 train 时间
        Map<String, Object> analysisValues = getSectionMap(taskId, TAB_ANALYSIS);
        analysisValues.put("train_start_date", request.getTrainStartDate());
        analysisValues.put("train_end_date", request.getTrainEndDate());

        Map<String, Object> trainingWorkflow = new LinkedHashMap<>();
        trainingWorkflow.put("mode", request.getMode());

        if ("static_split".equals(request.getMode())) {
            LocalDate trainStart = LocalDate.parse(request.getTrainStartDate());
            LocalDate trainEnd = LocalDate.parse(request.getTrainEndDate());
            long totalDays = ChronoUnit.DAYS.between(trainStart, trainEnd) + 1;
            long discoveryDays = Math.round(totalDays * 0.7);
            LocalDate discoveryEnd = trainStart.plusDays(discoveryDays - 1);
            LocalDate validationStart = discoveryEnd.plusDays(1);

            Map<String, Object> staticSplit = new LinkedHashMap<>();
            staticSplit.put("discovery_start_date", request.getTrainStartDate());
            staticSplit.put("discovery_end_date", discoveryEnd.toString());
            staticSplit.put("validation_start_date", validationStart.toString());
            staticSplit.put("validation_end_date", request.getTrainEndDate());
            trainingWorkflow.put("static_split", staticSplit);
        } else {
            int spanMonths = request.getRecommendSpanMonths() != null ? request.getRecommendSpanMonths() : 24;
            int discoveryMonths = Math.max(1, (int) Math.round(spanMonths * 0.7));
            int validationMonths = Math.max(1, spanMonths - discoveryMonths);

            Map<String, Object> walkForward = new LinkedHashMap<>();
            walkForward.put("discovery_window_months", discoveryMonths);
            walkForward.put("validation_window_months", validationMonths);
            walkForward.put("step_months", validationMonths);
            walkForward.put("max_windows", 2);
            trainingWorkflow.put("walk_forward", walkForward);
        }

        analysisValues.put("training_workflow", trainingWorkflow);
        saveSection(taskId, TAB_ANALYSIS, analysisValues);
    }

    /**
     * 校验市场环境配置参数之间的约束关系。
     * 约束规则与 market_context.yaml 中的参数逻辑保持一致。
     */
    private void validateMarketContext(Map<String, Object> values) {
        double trendUp = nestedDouble(values, "thresholds", "trend_up");
        double trendDown = nestedDouble(values, "thresholds", "trend_down");
        if (trendDown >= 0) {
            throw new BusinessException("趋势下行阈值必须小于 0");
        }
        if (trendUp <= trendDown) {
            throw new BusinessException("趋势上行阈值必须大于趋势下行阈值");
        }

        double rankHigh = nestedDouble(values, "thresholds", "rank_high");
        double rankLow = nestedDouble(values, "thresholds", "rank_low");
        if (rankHigh <= rankLow) {
            throw new BusinessException("分位高阈值必须大于分位低阈值");
        }

        double breadthOn = nestedDouble(values, "thresholds", "breadth_risk_on");
        double breadthOff = nestedDouble(values, "thresholds", "breadth_risk_off");
        if (breadthOn <= breadthOff) {
            throw new BusinessException("广度偏强阈值必须大于广度偏弱阈值");
        }

        double northHigh = nestedDouble(values, "thresholds", "northbound_inflow_high");
        double northLow = nestedDouble(values, "thresholds", "northbound_outflow_low");
        if (northHigh <= northLow) {
            throw new BusinessException("北向偏流入阈值必须大于北向偏流出阈值");
        }

        double levHigh = nestedDouble(values, "thresholds", "leverage_hot_high");
        double levLow = nestedDouble(values, "thresholds", "leverage_cold_low");
        if (levHigh <= levLow) {
            throw new BusinessException("两融升温阈值必须大于两融降温阈值");
        }

        double rateEase = nestedDouble(values, "thresholds", "rate_easing_threshold");
        double rateTight = nestedDouble(values, "thresholds", "rate_tightening_threshold");
        if (rateTight <= rateEase) {
            throw new BusinessException("利率收紧阈值必须大于利率宽松阈值");
        }

        int warmup = nestedInt(values, "windows", "warmup_trading_days");
        int rankLookback = nestedInt(values, "windows", "rank_lookback_days");
        int flowLookback = nestedInt(values, "windows", "flow_rank_lookback_days");
        int northDays = nestedInt(values, "windows", "northbound_days");
        int marginDays = nestedInt(values, "windows", "margin_days");

        if (warmup < rankLookback) {
            throw new BusinessException("预热天数不能小于历史分位回看天数");
        }
        if (warmup < flowLookback) {
            throw new BusinessException("预热天数不能小于资金面分位回看天数");
        }
        if (warmup < northDays) {
            throw new BusinessException("预热天数不能小于北向资金窗口天数");
        }
        if (warmup < marginDays) {
            throw new BusinessException("预热天数不能小于两融情绪窗口天数");
        }

        int minRankPeriods = nestedInt(values, "windows", "min_rank_periods");
        if (minRankPeriods > rankLookback) {
            throw new BusinessException("分位计算最少天数不能大于历史分位回看天数");
        }
        if (minRankPeriods > flowLookback) {
            throw new BusinessException("分位计算最少天数不能大于资金面分位回看天数");
        }

        int minRolling = nestedInt(values, "windows", "min_rolling_periods");
        int trendDays = nestedInt(values, "windows", "trend_days");
        int volDays = nestedInt(values, "windows", "volatility_days");
        int dispDays = nestedInt(values, "windows", "dispersion_days");
        if (minRolling > trendDays) {
            throw new BusinessException("滚动计算最少天数不能大于趋势窗口天数");
        }
        if (minRolling > volDays) {
            throw new BusinessException("滚动计算最少天数不能大于波动窗口天数");
        }
        if (minRolling > dispDays) {
            throw new BusinessException("滚动计算最少天数不能大于分化窗口天数");
        }
    }

    /**
     * 校验因子评分权重之和是否为 1.0。
     */
    private void validateScoreWeights(Map<String, Object> values) {
        double icStability = toDouble(values.get("weights.ic_stability"), 0.35);
        double annualReturn = toDouble(values.get("weights.annual_return"), 0.50);
        double drawdown = toDouble(values.get("weights.drawdown"), 0.05);
        double turnover = toDouble(values.get("weights.turnover"), 0.05);
        double instability = toDouble(values.get("weights.instability"), 0.05);
        double sum = icStability + annualReturn + drawdown + turnover + instability;
        if (Math.abs(sum - 1.0) > 0.001) {
            throw new BusinessException("因子评分权重之和必须等于 1.0，当前为 " + String.format("%.3f", sum));
        }
    }

    /**
     * 校验窗口选择参数。
     */
    private void validateSelector(Map<String, Object> values) {
        String testEndDate = (String) values.get("test_end_date");
        if (testEndDate != null && !testEndDate.isBlank()) {
            LocalDate end = LocalDate.parse(testEndDate);
            if (end.isAfter(LocalDate.now())) {
                throw new BusinessException("Test 结束日期不能超过当天");
            }
        }

        // 自行设定训练窗口时校验训练窗口日期
        String trainWindowSource = (String) values.get("train_window_source");
        if ("manual".equals(trainWindowSource)) {
            String manualStart = (String) values.get("manual_train_start_date");
            String manualEnd = (String) values.get("manual_train_end_date");
            if (manualStart == null || manualStart.isBlank() || manualEnd == null || manualEnd.isBlank()) {
                throw new BusinessException("自行设定训练窗口时，训练开始日期和训练结束日期必须填写");
            }
            LocalDate start = LocalDate.parse(manualStart);
            LocalDate end = LocalDate.parse(manualEnd);
            if (end.isBefore(start)) {
                throw new BusinessException("训练结束日期不能早于训练开始日期");
            }
            if (end.isAfter(LocalDate.now())) {
                throw new BusinessException("训练结束日期不能超过当天");
            }
        }

        int lookbackYears = toInt(values.get("lookback_years"), 10);
        if (lookbackYears > 10) {
            throw new BusinessException("回看历史年数最多 10 年");
        }

        double simWeight = toDouble(values.get("score_similarity_weight"), 0.7);
        double covWeight = toDouble(values.get("score_coverage_weight"), 0.3);
        if (Math.abs(simWeight + covWeight - 1.0) > 0.001) {
            throw new BusinessException("平均相似度权重与 Top-K 覆盖度权重之和必须等于 1.0，当前为 "
                    + String.format("%.3f", simWeight + covWeight));
        }
    }

    private double nestedDouble(Map<String, Object> map, String... keys) {
        Object value = getNestedValue(map, keys);
        if (value instanceof Number) {
            return ((Number) value).doubleValue();
        }
        return 0.0;
    }

    private int nestedInt(Map<String, Object> map, String... keys) {
        Object value = getNestedValue(map, keys);
        if (value instanceof Number) {
            return ((Number) value).intValue();
        }
        return 0;
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
                .description("因子挖掘与回测的整体时间区间")
                .fields(List.of(
                        field("run_mode", "运行模式", "select",
                                values.getOrDefault("run_mode", "train"), "train",
                                "train=仅训练，test=仅测试，train_test=先训练后测试",
                                List.of(opt("仅训练", "train"), opt("仅测试", "test"), opt("训练+测试", "train_test")), "task"),
                        field("iteration_count", "迭代轮数", "select",
                                values.getOrDefault("iteration_count", 1), 1,
                                "多轮迭代会基于前一轮反馈继续挖掘",
                                List.of(opt("1轮", "1"), opt("2轮", "2"), opt("3轮", "3"), opt("5轮", "5")), "task"),
                        field("train_start_date", "训练开始日期", "date",
                                values.getOrDefault("train_start_date", ""), "",
                                "训练区间开始（含）", null, "task"),
                        field("train_end_date", "训练结束日期", "date",
                                values.getOrDefault("train_end_date", ""), "",
                                "训练区间结束（含）", null, "task"),
                        field("test_start_date", "测试开始日期", "date",
                                values.getOrDefault("test_start_date", ""), "",
                                "测试区间开始（含），仅在 test/train_test 模式下有效", null, "task"),
                        field("test_end_date", "测试结束日期", "date",
                                values.getOrDefault("test_end_date", ""), "",
                                "测试区间结束（含），仅在 test/train_test 模式下有效", null, "task")
                ))
                .build());

        // 组2: 训练切分配置
        groups.add(ConfigGroup.builder()
                .name("training_workflow").label("训练切分配置").icon("Scissor")
                .description("训练窗口内部如何切分为发现期和验证期")
                .fields(List.of(
                        field("training_workflow.mode", "切分模式", "select",
                                getNestedValue(values, "training_workflow", "mode"), "static_split",
                                "static_split=固定切分，walk_forward=滚动窗口",
                                List.of(opt("固定切分", "static_split"), opt("滚动窗口", "walk_forward")), "task"),
                        field("training_workflow.static_split.discovery_start_date", "发现期开始", "date",
                                getNestedValue(values, "training_workflow", "static_split.discovery_start_date"), "",
                                "静态切分：因子发现阶段开始", null, "task"),
                        field("training_workflow.static_split.discovery_end_date", "发现期结束", "date",
                                getNestedValue(values, "training_workflow", "static_split.discovery_end_date"), "",
                                "静态切分：因子发现阶段结束", null, "task"),
                        field("training_workflow.static_split.validation_start_date", "验证期开始", "date",
                                getNestedValue(values, "training_workflow", "static_split.validation_start_date"), "",
                                "静态切分：因子验证阶段开始", null, "task"),
                        field("training_workflow.static_split.validation_end_date", "验证期结束", "date",
                                getNestedValue(values, "training_workflow", "static_split.validation_end_date"), "",
                                "静态切分：因子验证阶段结束", null, "task"),
                        numField("training_workflow.walk_forward.discovery_window_months", "发现窗口（月）",
                                getNestedValue(values, "training_workflow", "walk_forward.discovery_window_months"), 24,
                                "滚动窗口：每次发现期长度", 1, 60, 1, 0, false),
                        numField("training_workflow.walk_forward.validation_window_months", "验证窗口（月）",
                                getNestedValue(values, "training_workflow", "walk_forward.validation_window_months"), 6,
                                "滚动窗口：每次验证期长度", 1, 24, 1, 0, false),
                        numField("training_workflow.walk_forward.step_months", "滚动步长（月）",
                                getNestedValue(values, "training_workflow", "walk_forward.step_months"), 6,
                                "滚动窗口：窗口滚动步长", 1, 24, 1, 0, false),
                        numField("training_workflow.walk_forward.max_windows", "最大窗口数",
                                getNestedValue(values, "training_workflow", "walk_forward.max_windows"), 0,
                                "0=不限制", 0, 50, 1, 0, false)
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_ANALYSIS)
                .sectionLabel("分析口径")
                .sectionDescription("配置因子分析的股票池、时间区间和预处理")
                .sectionIcon("DataAnalysis")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 2: 股票池
    // ========================================================================
    private StructuredConfigResponse buildStockPoolTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_STOCK_POOL);
        List<ConfigGroup> groups = new ArrayList<>();

        groups.add(ConfigGroup.builder()
                .name("stock_pool").label("股票池").icon("DataBoard")
                .description("参与因子挖掘和回测的股票范围")
                .fields(List.of(
                        field("stock_pool.type", "股票池类型", "select",
                                getNestedValue(values, "stock_pool", "type"), "all_market",
                                "选择股票池类型",
                                List.of(
                                        opt("全市场", "all_market"),
                                        opt("指数成分股", "index_components"),
                                        opt("行业", "industry"),
                                        opt("自定义列表", "custom_list")
                                ), "task"),
                        // index_components 时显示
                        ConfigField.builder()
                                .key("stock_pool.index_code").label("指数代码").type("select")
                                .value(getNestedValue(values, "stock_pool", "index_code")).defaultValue("000300.SH")
                                .description("选择基准指数")
                                .options(List.of(
                                        SelectOption.builder().label("沪深300").value("000300.SH").description("000300.SH").build(),
                                        SelectOption.builder().label("中证500").value("000905.SH").description("000905.SH").build(),
                                        SelectOption.builder().label("中证1000").value("000852.SH").description("000852.SH").build(),
                                        SelectOption.builder().label("上证50").value("000016.SH").description("000016.SH").build(),
                                        SelectOption.builder().label("深证成指").value("399001.SZ").description("399001.SZ").build(),
                                        SelectOption.builder().label("创业板指").value("399006.SZ").description("399006.SZ").build()
                                ))
                                .source("task")
                                .showWhenKey("stock_pool.type").showWhenValue("index_components")
                                .build(),
                        // industry 时显示
                        ConfigField.builder()
                                .key("stock_pool.industry_name").label("行业").type("select")
                                .value(getNestedValue(values, "stock_pool", "industry_name")).defaultValue("银行")
                                .description("选择申万一级行业")
                                .options(List.of(
                                        opt("银行", "银行"), opt("医药生物", "医药生物"), opt("电子", "电子"),
                                        opt("汽车", "汽车"), opt("食品饮料", "食品饮料"), opt("电力设备", "电力设备"),
                                        opt("计算机", "计算机"), opt("非银金融", "非银金融"), opt("房地产", "房地产"),
                                        opt("交通运输", "交通运输"), opt("基础化工", "基础化工"), opt("有色金属", "有色金属"),
                                        opt("传媒", "传媒"), opt("公用事业", "公用事业"), opt("家用电器", "家用电器"),
                                        opt("通信", "通信"), opt("国防军工", "国防军工"), opt("机械设备", "机械设备"),
                                        opt("农林牧渔", "农林牧渔"), opt("建筑材料", "建筑材料"), opt("建筑装饰", "建筑装饰"),
                                        opt("轻工制造", "轻工制造"), opt("钢铁", "钢铁"), opt("煤炭", "煤炭"),
                                        opt("石油石化", "石油石化"), opt("环保", "环保"), opt("纺织服饰", "纺织服饰"),
                                        opt("美容护理", "美容护理"), opt("商贸零售", "商贸零售"), opt("社会服务", "社会服务"),
                                        opt("综合", "综合")
                                ))
                                .source("task")
                                .showWhenKey("stock_pool.type").showWhenValue("industry")
                                .build(),
                        // custom_list 时显示
                        ConfigField.builder()
                                .key("stock_pool.instruments").label("股票列表").type("textarea")
                                .value(getNestedValue(values, "stock_pool", "instruments")).defaultValue("")
                                .description("每行一个股票代码，如 000001.XSHE")
                                .source("task")
                                .showWhenKey("stock_pool.type").showWhenValue("custom_list")
                                .build(),
                        ConfigField.builder()
                                .key("stock_pool.dynamic_membership").label("动态成分股").type("switch")
                                .value(getNestedValue(values, "stock_pool", "dynamic_membership")).defaultValue(true)
                                .description("true=按每日实际成分股；false=按区间结束日成分股固定")
                                .source("task")
                                .showWhenKey("stock_pool.type").showWhenValue("index_components")
                                .build(),
                        ConfigField.builder()
                                .key("stock_pool.index_component_search_max_open_days").label("成分股回溯最大开市日数").type("number")
                                .value(getNestedValue(values, "stock_pool", "index_component_search_max_open_days")).defaultValue(2000)
                                .description("Tushare取不到某日期成分股时，最多回溯多少个交易日")
                                .min(0).max(5000).step(100).precision(0)
                                .required(true).source("task")
                                .showWhenKey("stock_pool.type").showWhenValue("index_components")
                                .build(),
                        field("stock_pool.include_st", "包含 ST 股票", "switch",
                                getNestedValue(values, "stock_pool", "include_st"), false,
                                "是否包含被特别处理(ST/*ST)的股票", null, "task"),
                        field("stock_pool.include_new_stock", "包含新股", "switch",
                                getNestedValue(values, "stock_pool", "include_new_stock"), false,
                                "是否包含上市时间较短的新股", null, "task"),
                        numField("stock_pool.new_stock_days", "新股天数阈值",
                                getNestedValue(values, "stock_pool", "new_stock_days"), 60,
                                "上市天数小于此值视为新股，用于新股过滤", 0, 365, 1, 0, false)
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_STOCK_POOL)
                .sectionLabel("股票池")
                .sectionDescription("配置参与因子挖掘和回测的股票范围")
                .sectionIcon("DataBoard")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 3: 原始字段与特征值
    // ========================================================================
    private StructuredConfigResponse buildFeaturePoolTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_FEATURE_POOL);
        List<ConfigGroup> groups = new ArrayList<>();

        groups.add(ConfigGroup.builder()
                .name("feature_pool").label("").icon("")
                .description("")
                .fields(List.of(
                        field("enable_chip_features", "启用筹码特征值列表", "switch",
                                values.getOrDefault("enable_chip_features", false), false,
                                "关闭后会过滤掉 expr 以 chip. 开头的特征值，并跳过筹码分布分析师", null, "task")
                ))
                .build());

        groups.add(ConfigGroup.builder()
                .name("health_check").label("特征体检参数").icon("FirstAidKit")
                .description("控制特征健康检查摘要的生成")
                .fields(List.of(
                        numField("summary_top_k", "最强/最弱特征数",
                                values.getOrDefault("summary_top_k", 3), 3,
                                "摘要中挑选和收益标签相关性最强/最弱特征的数量", 1, 20, 1, 0, true),
                        numField("unstable_top_k", "最不稳定特征数",
                                values.getOrDefault("unstable_top_k", 3), 3,
                                "摘要中挑选和收益标签相关性最不稳定特征的数量", 1, 20, 1, 0, true),
                        numField("high_corr_threshold", "高相关阈值",
                                values.getOrDefault("high_corr_threshold", 0.5), 0.5,
                                "特征与收益标签的整体相关系数超过此值视为高度重合", 0, 1, 0.05, 2, true),
                        numField("max_missing_ratio", "最大缺失率阈值",
                                values.getOrDefault("max_missing_ratio", 0.2), 0.2,
                                "特征值缺失占比超过此值标记为数据质量低", 0, 1, 0.05, 2, true),
                        numField("fundamental_health_top_k", "基本面分析师专用体检中按多空收益差排名列出的特征数量",
                                values.getOrDefault("fundamental_health_top_k", 5), 5,
                                "基本面分析师专用体检中按多空收益差排名列出的特征数量", 1, 20, 1, 0, true)
                ))
                .build());

        // 读取 runtime/config/feature_pool.yaml，按 11 个分析师维度展示特征
        Map<String, Object> preview = buildFeaturePoolPreview();
        if (!preview.isEmpty()) {
            groups.add(ConfigGroup.builder()
                    .name("feature_pool_preview").label("基础特征池预览").icon("DataBoard")
                    .description("当前可用基础特征按 11 个分析师维度分类展示")
                    .fields(List.of(
                            field("feature_pool_preview", "", "feature_pool_preview",
                                    preview, preview,
                                    null, null, "system")
                    ))
                    .build());
        }

        return StructuredConfigResponse.builder()
                .section(TAB_FEATURE_POOL)
                .sectionLabel("原始字段与特征值")
                .sectionDescription("配置筹码特征开关、特征体检参数，预览当前基础特征池")
                .sectionIcon("Coin")
                .groups(groups)
                .build();
    }

    /**
     * 读取 runtime/config/feature_pool.yaml，将 base_features 按 11 个分析师维度分组。
     * 返回结构：{ dimensions: [...], rawFields: [...] }
     */
    private Map<String, Object> buildFeaturePoolPreview() {
        // 尝试多个路径查找 feature_pool.yaml
        Path path = resolveFeaturePoolPath();
        if (path == null || !Files.exists(path)) {
            log.warn("feature_pool.yaml 未找到，已尝试路径: configTemplateDir={}", stagingProperties.getConfigTemplateDir());
            return Map.of();
        }
        log.info("读取 feature_pool.yaml: {}", path);

        Map<String, Object> yaml;
        try {
            // 使用 SnakeYAML 解析（Jackson ObjectMapper 只能解析 JSON）
            org.yaml.snakeyaml.Yaml snakeYaml = new org.yaml.snakeyaml.Yaml();
            try (var is = Files.newInputStream(path)) {
                yaml = snakeYaml.load(is);
            }
        } catch (Exception e) {
            log.warn("解析 feature_pool.yaml 失败: {}", e.getMessage());
            return Map.of();
        }

        List<Map<String, Object>> rawFields = (List<Map<String, Object>>) yaml.getOrDefault("raw_fields", List.of());
        List<Map<String, Object>> baseFeatures = (List<Map<String, Object>>) yaml.getOrDefault("base_features", List.of());

        // 按特征名映射到维度（不依赖 YAML 注释格式）
        Map<String, List<Map<String, Object>>> dimensionGroups = new LinkedHashMap<>();
        for (DimensionDef dim : FEATURE_DIMENSIONS) {
            dimensionGroups.put(dim.key(), new ArrayList<>());
        }
        for (Map<String, Object> feature : baseFeatures) {
            String name = String.valueOf(feature.get("name"));
            String dimKey = mapFeatureToDimension(name);
            dimensionGroups.getOrDefault(dimKey, dimensionGroups.get("trend_momentum")).add(feature);
        }

        List<Map<String, Object>> dimensions = new ArrayList<>();
        for (DimensionDef dim : FEATURE_DIMENSIONS) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("key", dim.key());
            item.put("label", dim.label());
            item.put("icon", dim.icon());
            item.put("category", dim.category());
            item.put("features", dimensionGroups.getOrDefault(dim.key(), List.of()));
            dimensions.add(item);
        }

        return Map.of(
                "dimensions", dimensions,
                "rawFields", rawFields
        );
    }

    /**
     * 按特征名前缀/模式映射到分析师维度。
     * 不依赖 YAML 注释，而是通过特征名规则确定归属，更健壮。
     */
    private String mapFeatureToDimension(String name) {
        // 趋势动量：收益率、反弹、突破、vwap趋势
        if (name.startsWith("ret_") || name.startsWith("rebound_") || name.startsWith("breakout_")
                || name.startsWith("vwap_ret_")) {
            return "trend_momentum";
        }
        // 反转均值回复：价格位置、最大回撤
        if (name.startsWith("price_pos_") || name.startsWith("max_drawdown_")) {
            return "reversal_mean_reversion";
        }
        // 波动风险：振幅、实现波动率
        if (name.equals("high_low_range") || name.startsWith("realized_vol_")) {
            return "volatility_risk";
        }
        // 量价关系：成交量、成交额相关
        if (name.startsWith("volume_") || name.startsWith("amount_")) {
            return "volume_price";
        }
        // 微观结构：日内细节（跳空、日内收益、收盘位置等）
        if (name.startsWith("gap_") || name.startsWith("intraday_")
                || name.startsWith("close_to_") || name.startsWith("high_to_") || name.startsWith("low_to_")) {
            return "microstructure";
        }
        // 筹码分布：换手率、筹码、获利盘、成本、峰距
        if (name.startsWith("turnover_") || name.startsWith("chip_")
                || name.startsWith("profit_ratio_") || name.startsWith("avg_cost_")
                || name.startsWith("peak_distance_")) {
            return "chip_distribution";
        }
        // 基本面估值：PE/PB/PS/DV/盈利收益率/市值变化
        if (name.equals("pe_ttm") || name.equals("pb") || name.equals("ps_ttm") || name.equals("dv_ttm")
                || name.equals("earnings_yield") || name.equals("sales_yield")
                || name.startsWith("market_cap_")) {
            return "fundamental_value";
        }
        // 基本面盈利质量：EPS/ROE/增速/利润率/质量价值等
        if (name.equals("eps") || name.equals("roe") || name.equals("netprofit_yoy") || name.equals("or_yoy")
                || name.equals("quality_value") || name.equals("profit_revenue_gap")
                || name.equals("profit_quality") || name.equals("q_roe_acceleration")
                || name.equals("gross_margin") || name.equals("net_margin")
                || name.equals("real_growth") || name.equals("op_growth") || name.equals("equity_growth")) {
            return "fundamental_quality";
        }
        // 基本面投资因子：资产增长
        if (name.equals("asset_growth_inverse")) {
            return "fundamental_investment";
        }
        // 基本面财务健康：财务健康、流动性、偿债
        if (name.equals("financial_health") || name.equals("liquidity_strength")
                || name.equals("debt_service_ability")) {
            return "fundamental_health";
        }
        // 基本面现金流：自由现金流收益率
        if (name.equals("fcf_yield")) {
            return "fundamental_cashflow";
        }
        // 默认归入趋势动量
        return "trend_momentum";
    }

    /**
     * 多路径回退查找 feature_pool.yaml
     */
    private Path resolveFeaturePoolPath() {
        // 1. 从 stagingProperties.configTemplateDir 查找
        String configDir = stagingProperties.getConfigTemplateDir();
        if (configDir != null && !configDir.isBlank()) {
            Path p = Paths.get(configDir, "feature_pool.yaml");
            if (Files.exists(p)) return p;
        }
        // 2. 从 user.dir 推算
        String userDir = System.getProperty("user.dir");
        if (userDir != null) {
            // 可能从 backend/ 启动
            Path p = Paths.get(userDir, "..", "runtime", "config", "feature_pool.yaml").normalize();
            if (Files.exists(p)) return p;
            // 可能从项目根目录启动
            p = Paths.get(userDir, "runtime", "config", "feature_pool.yaml").normalize();
            if (Files.exists(p)) return p;
        }
        // 3. 硬编码回退
        Path fallback = Paths.get("D:/works/alpha-factory/runtime/config/feature_pool.yaml");
        if (Files.exists(fallback)) return fallback;
        return null;
    }

    /**
     * 诊断方法：返回 feature_pool.yaml 读取状态
     */
    public Map<String, Object> debugFeaturePoolPreview() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("configTemplateDir", stagingProperties.getConfigTemplateDir());
        result.put("userDir", System.getProperty("user.dir"));

        // 尝试路径1
        String configDir = stagingProperties.getConfigTemplateDir();
        if (configDir != null && !configDir.isBlank()) {
            Path p = Paths.get(configDir, "feature_pool.yaml");
            result.put("path1", p.toString());
            result.put("path1Exists", Files.exists(p));
        }

        // 尝试路径2
        String userDir = System.getProperty("user.dir");
        if (userDir != null) {
            Path p1 = Paths.get(userDir, "..", "runtime", "config", "feature_pool.yaml").normalize();
            result.put("path2", p1.toString());
            result.put("path2Exists", Files.exists(p1));

            Path p2 = Paths.get(userDir, "runtime", "config", "feature_pool.yaml").normalize();
            result.put("path3", p2.toString());
            result.put("path3Exists", Files.exists(p2));
        }

        // 尝试路径4
        Path fallback = Paths.get("D:/works/alpha-factory/runtime/config/feature_pool.yaml");
        result.put("path4", fallback.toString());
        result.put("path4Exists", Files.exists(fallback));

        // 最终解析结果
        Path resolved = resolveFeaturePoolPath();
        result.put("resolvedPath", resolved != null ? resolved.toString() : "null");

        // 尝试读取
        Map<String, Object> preview = buildFeaturePoolPreview();
        result.put("previewEmpty", preview.isEmpty());
        if (!preview.isEmpty()) {
            result.put("dimensionCount", ((List<?>) preview.getOrDefault("dimensions", List.of())).size());
            result.put("rawFieldCount", ((List<?>) preview.getOrDefault("rawFields", List.of())).size());
        }

        return result;
    }

    // ========================================================================
    // Tab 3: LLM 参数
    // ========================================================================

    /** Agent 定义：key=agent名称, label=显示名称, description=描述, defaultTemp=默认temperature, stage=阶段, analyst=是否分析师 */
    private static final List<AgentDef> AGENT_DEFS = List.of(
            // 阶段1: 专业分析师团队（11个并行）
            new AgentDef("trend_momentum", "趋势动量分析师", "识别趋势延续与动量反转信号", 0.6, 1, true),
            new AgentDef("reversal_mean_reversion", "反转均值回归分析师", "捕捉均值回归与超跌反弹机会", 0.6, 1, true),
            new AgentDef("volatility_risk", "波动风险分析师", "评估波动率结构与风险敞口", 0.6, 1, true),
            new AgentDef("volume_price", "量价配合分析师", "分析量价背离与资金流向", 0.6, 1, true),
            new AgentDef("microstructure", "微观结构分析师", "挖掘订单簿与交易微观特征", 0.6, 1, true),
            new AgentDef("chip_distribution", "筹码分布分析师", "分析筹码集中度与获利盘结构", 0.6, 1, true),
            new AgentDef("fundamental_value", "基本面估值分析师", "评估估值水平与价值偏离", 0.6, 1, true),
            new AgentDef("fundamental_quality", "基本面盈利质量分析师", "评估盈利质量与持续性", 0.6, 1, true),
            new AgentDef("fundamental_investment", "基本面投资因子分析师", "评估投资与资本结构因子", 0.6, 1, true),
            new AgentDef("fundamental_health", "基本面财务健康分析师", "评估财务健康与稳健性", 0.6, 1, true),
            new AgentDef("fundamental_cashflow", "基本面现金流分析师", "评估现金流质量与生成能力", 0.6, 1, true),
            // 阶段2: 首席分析师
            new AgentDef("chief_analyst", "首席分析师", "整合11个分析师输出，形成统一方向", 0.6, 2, false),
            // 阶段3: 因子生产专员
            new AgentDef("generator", "因子生产专员", "按设计方向生成因子公式", 0.2, 3, false),
            // 阶段4: 因子评审员
            new AgentDef("reviewer", "因子评审员", "PASS/REJECT 严格评审", 0.2, 4, false)
    );

    /** 特征池 11 个分析师维度定义 */
    private record DimensionDef(String key, String label, String icon, String category) {}

    private static final List<DimensionDef> FEATURE_DIMENSIONS = List.of(
            new DimensionDef("trend_momentum", "趋势动量", "TrendCharts", "技术面"),
            new DimensionDef("reversal_mean_reversion", "反转均值回复", "RefreshLeft", "技术面"),
            new DimensionDef("volatility_risk", "波动风险", "Lightning", "技术面"),
            new DimensionDef("volume_price", "量价关系", "Histogram", "技术面"),
            new DimensionDef("microstructure", "微观结构", "View", "技术面"),
            new DimensionDef("chip_distribution", "筹码分布", "Coin", "技术面"),
            new DimensionDef("fundamental_value", "基本面估值", "Discount", "基本面"),
            new DimensionDef("fundamental_quality", "基本面盈利质量", "Medal", "基本面"),
            new DimensionDef("fundamental_investment", "基本面投资因子", "Wallet", "基本面"),
            new DimensionDef("fundamental_health", "基本面财务健康", "FirstAidKit", "基本面"),
            new DimensionDef("fundamental_cashflow", "基本面现金流", "Money", "基本面")
    );

    private static final Map<Integer, String> STAGE_LABELS = Map.of(
            1, "阶段 1：专业分析师团队（11 个并行）",
            2, "阶段 2：首席分析师（整合输出）",
            3, "阶段 3：因子生产专员",
            4, "阶段 4：因子评审员"
    );

    private record AgentDef(String key, String label, String description, double defaultTemp, int stage, boolean analyst) {}

    @SuppressWarnings("unchecked")
    private StructuredConfigResponse buildLlmTab(Long userId, Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_LLM);
        List<ConfigGroup> groups = new ArrayList<>();

        // 读取 feature_pool 的筹码特征开关，用于控制筹码分布分析师的启用状态
        Map<String, Object> featurePoolValues = getSectionMap(taskId, TAB_FEATURE_POOL);
        boolean enableChipFeatures = (boolean) featurePoolValues.getOrDefault("enable_chip_features", false);

        // 合并供应商列表：默认(yml) + 自定义(DB)
        List<Map<String, String>> providers = systemConfigService.getMergedProviders(userId);
        List<SelectOption> providerOptions = providers.stream()
                .map(p -> SelectOption.builder().label(p.get("name")).value(p.get("name")).description(p.get("base_url")).build())
                .toList();

        // 读取已保存的 llm_agents 配置（扁平key格式: llm_agents.trend_momentum.model）
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

            String agentProvider = (String) agentConfig.getOrDefault("llm_provider", "");
            String agentUrl = (String) agentConfig.getOrDefault("llm_base_url", "");
            String agentModel = (String) agentConfig.getOrDefault("llm_model", "");
            String agentApiKey = (String) agentConfig.getOrDefault("llm_api_key", "");
            double agentTemp = toDouble(agentConfig.get("temperature"), agent.defaultTemp());
            int agentMaxTokens = toInt(agentConfig.get("max_tokens"), 8192);
            double agentTimeout = toDouble(agentConfig.get("timeout_seconds"), agent.stage() == 1 ? 60.0 : 120.0);
            int agentRetries = toInt(agentConfig.get("max_retries"), 2);
            boolean agentEnabled = (boolean) agentConfig.getOrDefault("enable", true);

            // 根据供应商自动带出URL
            if (agentUrl.isEmpty() && !agentProvider.isEmpty()) {
                agentUrl = providers.stream()
                        .filter(p -> p.get("name").equals(agentProvider))
                        .map(p -> p.get("base_url"))
                        .findFirst().orElse("");
            }

            String prefix = "llm_agents." + agent.key();
            List<ConfigField> fields = new ArrayList<>(List.of(
                    field(prefix + ".llm_provider", "供应商", "select",
                            agentProvider, "",
                            "选择LLM供应商",
                            providerOptions, "task", true, false),
                    field(prefix + ".llm_base_url", "API 地址", "text",
                            agentUrl, "",
                            "选择供应商后自动填入，也可手动修改", null, "task", true, false),
                    field(prefix + ".llm_model", "模型名称", "text",
                            agentModel, "",
                            "如 gpt-4o, deepseek-chat, qwen-max 等", null, "task", true, false),
                    field(prefix + ".llm_api_key", "API Key", "password",
                            agentApiKey, "",
                            "对应供应商的 API Key", null, "task", true, false),
                    numField(prefix + ".temperature", "Temperature",
                            agentTemp, agent.defaultTemp(),
                            "创意程度，0.2=严谨, 0.6=平衡, 0.9=创意", 0, 2, 0.1, 2, true),
                    numField(prefix + ".max_tokens", "Max Tokens",
                            agentMaxTokens, 8192,
                            "单次请求最大 token 数", 256, 32768, 256, 0, true),
                    numField(prefix + ".timeout_seconds", "超时时间(秒)",
                            agentTimeout, agent.stage() == 1 ? 60.0 : 120.0,
                            "单次请求超时时间", 10, 600, 10, 0, true),
                    numField(prefix + ".max_retries", "最大重试次数",
                            agentRetries, 2,
                            "请求失败后最大重试次数", 0, 10, 1, 0, true)
            ));
            if (agent.analyst()) {
                boolean chipDisabled = "chip_distribution".equals(agent.key()) && !enableChipFeatures;
                fields.add(field(prefix + ".enable", "启用", "switch",
                        chipDisabled ? false : agentEnabled, true,
                        "是否启用该分析师", null, "task", chipDisabled));
            }
            // 因子生成器额外显示候选因子数量
            if ("generator".equals(agent.key())) {
                fields.add(numField("llm_candidate_count", "候选因子数量",
                        values.getOrDefault("llm_candidate_count", 10), 10,
                        "因子生产专员产出的候选因子数", 1, 100, 1, 0, true));
            }

            groups.add(ConfigGroup.builder()
                    .name("agent_" + agent.key()).label(agent.label()).icon("UserFilled")
                    .description(agent.description())
                    .fields(fields)
                    .build());
        }

        return StructuredConfigResponse.builder()
                .section(TAB_LLM)
                .sectionLabel("Agent 参数")
                .sectionDescription("配置多Agent流水线中每个Agent的LLM供应商和参数")
                .sectionIcon("ChatDotRound")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 4: 回测策略
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
                                "因子得分排名前N的股票进入买入候选", 1, 500, 5, 0, true,
                                "strategy_type", "TopKDropout"),
                        numField("TopKDropout.sell_drop_to", "跌出前M名则卖出",
                                getNestedValue(values, "TopKDropout", "sell_drop_to"), 200,
                                "持仓股票跌出前M名则卖出（宽幅缓冲降低换手）", 1, 1000, 10, 0, true,
                                "strategy_type", "TopKDropout"),
                        numField("TopKDropout.holding_count", "目标持仓数",
                                getNestedValue(values, "TopKDropout", "holding_count"), 20,
                                "最终目标持仓股票数量", 1, 500, 5, 0, true,
                                "strategy_type", "TopKDropout"),
                        field("TopKDropout.weight_mode", "权重模式", "select",
                                getNestedValue(values, "TopKDropout", "weight_mode"), "equal_weight",
                                "持仓权重分配方式",
                                List.of(opt("等权持仓", "equal_weight")), "task",
                                "strategy_type", "TopKDropout"),
                        numField("TopKDropout.max_drop_per_day", "每日最大换出数",
                                getNestedValue(values, "TopKDropout", "max_drop_per_day"), 5,
                                "每次调仓最多换出数量，控制换手率", 0, 100, 1, 0, false,
                                "strategy_type", "TopKDropout"),
                        numField("TopKDropout.min_score_coverage", "调仓日因子分数保有率阈值",
                                getNestedValue(values, "TopKDropout", "min_score_coverage"), 0.90,
                                "调仓日因子分数保有率低于此值本调仓日不操作", 0, 1, 0.05, 2, false,
                                "strategy_type", "TopKDropout")
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
                                List.of(opt("Softmax", "softmax"), opt("Rank Power", "rank_power")), "task",
                                "strategy_type", "SoftTopK"),
                        numField("SoftTopK.holding_count", "持仓股票数",
                                getNestedValue(values, "SoftTopK", "holding_count"), 30,
                                "按因子分从高到低选前N只", 1, 500, 5, 0, true,
                                "strategy_type", "SoftTopK"),
                        numField("SoftTopK.softmax_temperature", "Softmax 温度",
                                getNestedValue(values, "SoftTopK", "softmax_temperature"), 0.7,
                                "T越小集中度越高，T越大越接近等权", 0.01, 10, 0.1, 2, true,
                                "strategy_type", "SoftTopK"),
                        numField("SoftTopK.rank_power_alpha", "Rank Power 衰减参数",
                                getNestedValue(values, "SoftTopK", "rank_power_alpha"), 1.0,
                                "α越大头部越集中，α越小越接近等权", 0.1, 10, 0.1, 2, true,
                                "strategy_type", "SoftTopK"),
                        numField("SoftTopK.min_score_coverage", "调仓日因子分数保有率阈值",
                                getNestedValue(values, "SoftTopK", "min_score_coverage"), 0.90,
                                "调仓日因子分数保有率低于此值本调仓日不操作", 0, 1, 0.05, 2, false,
                                "strategy_type", "SoftTopK")
                ))
                .build());

        // 组4: 择时过滤
        groups.add(ConfigGroup.builder()
                .name("market_timing").label("择时过滤").icon("Sunrise")
                .description("叠加层：根据市场状态调整仓位")
                .fields(List.of(
                        field("MarketTiming.enabled", "启用择时", "switch",
                                getNestedValue(values, "MarketTiming", "enabled"), false,
                                "关闭则按主策略满仓执行", null, "task"),
                        field("MarketTiming.market_indicator", "市场指标", "select",
                                getNestedValue(values, "MarketTiming", "market_indicator"), "EMA_60",
                                "当前仅支持 EMA_60",
                                List.of(opt("EMA 60日", "EMA_60")), "task",
                                "MarketTiming.enabled", true),
                        numField("MarketTiming.reduce_to", "风险状态目标仓位",
                                getNestedValue(values, "MarketTiming", "reduce_to"), 0.6,
                                "择时触发后的目标暴露比例(0~1)", 0, 1, 0.05, 2, true,
                                "MarketTiming.enabled", true),
                        field("MarketTiming.stock_open_filter", "个股开仓过滤", "select",
                                getNestedValue(values, "MarketTiming", "stock_open_filter"), "rsi",
                                "仅对新开仓生效，老持仓不受影响",
                                List.of(opt("不开启", "none"), opt("EMA过滤", "ema"), opt("RSI过滤", "rsi")), "task",
                                "MarketTiming.enabled", true),
                        numField("MarketTiming.stock_ema_period", "个股EMA周期",
                                getNestedValue(values, "MarketTiming", "stock_ema_period"), 60,
                                "仅 stock_open_filter=ema 时生效", 5, 250, 5, 0, false,
                                "MarketTiming.enabled", true,
                                "MarketTiming.stock_open_filter", "ema"),
                        numField("MarketTiming.rsi_period", "RSI计算窗口",
                                getNestedValue(values, "MarketTiming", "rsi_period"), 14,
                                "仅 stock_open_filter=rsi 时生效", 5, 100, 1, 0, false,
                                "MarketTiming.enabled", true,
                                "MarketTiming.stock_open_filter", "rsi"),
                        numField("MarketTiming.rsi_buy_max", "RSI开仓上限",
                                getNestedValue(values, "MarketTiming", "rsi_buy_max"), 70.0,
                                "RSI>此值禁止新开仓", 50, 100, 1, 0, false,
                                "MarketTiming.enabled", true,
                                "MarketTiming.stock_open_filter", "rsi")
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
                                List.of(opt("下一交易日开盘价", "next_open")), "task"),
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
                                "开启后输出逐因子详细交易日志", null, "task"),
                        field("Execution.suspend_action", "停牌处理", "select",
                                getNestedValue(values, "Execution", "suspend_action"), "skip",
                                "停牌股票的处理方式",
                                List.of(opt("跳过不交易", "skip")), "task"),
                        field("Execution.limit_up_action", "涨停买入处理", "select",
                                getNestedValue(values, "Execution", "limit_up_action"), "skip_buy",
                                "涨停时买入的处理方式",
                                List.of(opt("跳过不买入", "skip_buy")), "task"),
                        field("Execution.limit_down_action", "跌停卖出处理", "select",
                                getNestedValue(values, "Execution", "limit_down_action"), "delay_sell",
                                "跌停时卖出的处理方式",
                                List.of(opt("延迟卖出（每日重试）", "delay_sell")), "task")
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_BACKTEST)
                .sectionLabel("因子回测参数")
                .sectionDescription("配置因子回测策略类型、参数、择时过滤和执行参数")
                .sectionIcon("TrendCharts")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 5: 市场环境
    // ========================================================================
    private StructuredConfigResponse buildMarketTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_MARKET);
        List<ConfigGroup> groups = new ArrayList<>();

        // 组1: 字段映射（对应 market_context.yaml 中的 fields）
        groups.add(ConfigGroup.builder()
                .name("fields").label("字段映射").icon("Connection")
                .description("市场环境计算所依赖的原始字段名映射")
                .fields(List.of(
                        field("fields.close", "收盘价字段", "select",
                                getNestedValue(values, "fields", "close"), "close",
                                "用于计算市场日收益率的字段名",
                                List.of(opt("close", "close")), "task"),
                        field("fields.turnover", "换手字段", "select",
                                getNestedValue(values, "fields", "turnover"), "turnover",
                                "用于计算换手率历史分位的字段名",
                                List.of(opt("turnover", "turnover")), "task"),
                        field("fields.amount", "成交额字段", "select",
                                getNestedValue(values, "fields", "amount"), "amount",
                                "用于加总市场成交额的字段名",
                                List.of(opt("amount", "amount")), "task"),
                        field("fields.market_cap", "市值字段", "select",
                                getNestedValue(values, "fields", "market_cap"), "market_cap",
                                "用于区分大小盘风格的字段名",
                                List.of(opt("market_cap", "market_cap")), "task")
                ))
                .build());

        // 组2: 窗口参数（顺序与 market_context.yaml 保持一致）
        groups.add(ConfigGroup.builder()
                .name("windows").label("窗口参数").icon("Timer")
                .description("程序往前回头看多少个交易日来判断市场状态")
                .fields(List.of(
                        numField("windows.warmup_trading_days", "预热天数",
                                getNestedValue(values, "windows", "warmup_trading_days"), 260,
                                "程序在训练起点前多读的历史天数", 100, 500, 10, 0, true),
                        numField("windows.min_rolling_periods", "滚动计算最少天数",
                                getNestedValue(values, "windows", "min_rolling_periods"), 10,
                                "至少攒够多少天才开始算滚动指标", 5, 100, 5, 0, true),
                        numField("windows.trend_days", "趋势窗口（天）",
                                getNestedValue(values, "windows", "trend_days"), 20,
                                "看最近多少天的市场整体累计表现", 5, 250, 5, 0, true),
                        numField("windows.min_rank_periods", "分位计算最少天数",
                                getNestedValue(values, "windows", "min_rank_periods"), 50,
                                "至少攒够多少天才开始做分位比较", 20, 200, 10, 0, true),
                        numField("windows.volatility_days", "波动窗口（天）",
                                getNestedValue(values, "windows", "volatility_days"), 20,
                                "看最近多少天的日涨跌幅波动", 5, 250, 5, 0, true),
                        numField("windows.rank_lookback_days", "历史分位回看（天）",
                                getNestedValue(values, "windows", "rank_lookback_days"), 250,
                                "把当前指标放到过去多少天里算分位", 50, 500, 10, 0, true),
                        numField("windows.dispersion_days", "分化窗口（天）",
                                getNestedValue(values, "windows", "dispersion_days"), 20,
                                "看最近多少天的股票间差异", 5, 250, 5, 0, true),
                        numField("windows.min_size_style_sample", "风格判断最少股票数",
                                getNestedValue(values, "windows", "min_size_style_sample"), 20,
                                "少于这么多只股票时不做大小盘判断", 5, 200, 5, 0, true),
                        numField("windows.northbound_days", "北向资金窗口（天）",
                                getNestedValue(values, "windows", "northbound_days"), 5,
                                "看最近几天的北向净流入累计", 1, 30, 1, 0, true),
                        numField("windows.margin_days", "两融情绪窗口（天）",
                                getNestedValue(values, "windows", "margin_days"), 5,
                                "看最近几天的融资净流累计", 1, 30, 1, 0, true),
                        numField("windows.flow_rank_lookback_days", "资金面分位回看（天）",
                                getNestedValue(values, "windows", "flow_rank_lookback_days"), 250,
                                "北向/两融指标与过去多少天比较算分位", 50, 500, 10, 0, true),
                        numField("windows.shibor_trend_days", "Shibor趋势窗口（天）",
                                getNestedValue(values, "windows", "shibor_trend_days"), 20,
                                "Shibor利率看最近多少天的趋势变化", 5, 250, 5, 0, true),
                        numField("windows.m2_trend_months", "M2趋势窗口（月）",
                                getNestedValue(values, "windows", "m2_trend_months"), 3,
                                "M2同比看最近几个月的趋势", 1, 12, 1, 0, true),
                        numField("windows.pmi_trend_months", "PMI趋势窗口（月）",
                                getNestedValue(values, "windows", "pmi_trend_months"), 3,
                                "PMI看最近几个月的趋势", 1, 12, 1, 0, true),
                        numField("windows.inflation_trend_months", "通胀趋势窗口（月）",
                                getNestedValue(values, "windows", "inflation_trend_months"), 3,
                                "CPI/PPI同比看最近几个月的趋势", 1, 12, 1, 0, true)
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
                                "累计涨幅<=此值记为下行（必须小于 0）", -1, -0.01, 0.01, 2, true),
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
                                "融资分位<=此值记为降温", 0, 0.5, 0.01, 2, true),
                        numField("thresholds.rate_easing_threshold", "利率宽松阈值",
                                getNestedValue(values, "thresholds", "rate_easing_threshold"), -0.0025,
                                "Shibor下降超过此值记为宽松（如-0.0025=降25bp）", -0.1, 0, 0.0005, 4, true),
                        numField("thresholds.rate_tightening_threshold", "利率收紧阈值",
                                getNestedValue(values, "thresholds", "rate_tightening_threshold"), 0.0025,
                                "Shibor上升超过此值记为收紧（如0.0025=升25bp）", 0, 0.1, 0.0005, 4, true),
                        numField("thresholds.m2_yoy_change_threshold", "M2同比变化阈值",
                                getNestedValue(values, "thresholds", "m2_yoy_change_threshold"), 0.3,
                                "M2同比月度变化绝对值超过此值算有趋势", 0.1, 2, 0.1, 2, true),
                        numField("thresholds.pmi_expansion_threshold", "PMI扩张阈值",
                                getNestedValue(values, "thresholds", "pmi_expansion_threshold"), 50.0,
                                "PMI>此值表示制造业扩张", 40, 60, 1, 1, true),
                        numField("thresholds.inflation_yoy_change_threshold", "通胀同比变化阈值",
                                getNestedValue(values, "thresholds", "inflation_yoy_change_threshold"), 0.3,
                                "CPI/PPI同比月度变化绝对值超过此值算有趋势", 0.1, 2, 0.1, 2, true)
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
    // Tab 4: 收益标签
    // ========================================================================
    private StructuredConfigResponse buildLabelTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_ANALYSIS);
        List<ConfigGroup> groups = new ArrayList<>();

        groups.add(ConfigGroup.builder()
                .name("label").label("收益标签").icon("DataLine")
                .description("LLM挖掘阶段与因子研究阶段共用的收益标签口径")
                .fields(List.of(
                        field("label.name", "标签列名", "text",
                                getNestedValue(values, "label", "name"), "rebalance_period_return",
                                "标签列名与分析目标名称", null, "task"),
                        field("label.return_type", "收益类型", "select",
                                getNestedValue(values, "label", "return_type"), "period_return",
                                "当前仅支持 period_return",
                                List.of(opt("区间收益率", "period_return")), "task"),
                        field("label.price_field", "价格列", "select",
                                getNestedValue(values, "label", "price_field"), "close",
                                "使用哪个价格列计算收益标签",
                                List.of(
                                        opt("收盘价", "close"),
                                        opt("开盘价", "open"),
                                        opt("最高价", "high"),
                                        opt("最低价", "low"),
                                        opt("VWAP", "vwap")
                                ), "task")
                ))
                .build());

        // 组2: 调仓节奏
        groups.add(ConfigGroup.builder()
                .name("rebalance").label("调仓节奏").icon("Timer")
                .description("收益标签和回测的调仓频率")
                .fields(List.of(
                        field("rebalance", "调仓频率", "select",
                                values.getOrDefault("rebalance", "weekly"), "weekly",
                                "daily/weekly/monthly",
                                List.of(opt("日频", "daily"), opt("周频", "weekly"), opt("月频", "monthly")), "task"),
                        numField("rebalance_interval", "调仓间隔",
                                values.getOrDefault("rebalance_interval", 1), 1,
                                "每隔多少个 frequency 调仓一次", 1, 12, 1, 0, true),
                        field("rebalance_anchor", "调仓观察日", "select",
                                values.getOrDefault("rebalance_anchor", "first_trading_day_of_week"), "first_trading_day_of_week",
                                "按 frequency 选择每周或每月的第一个/最后一个交易日",
                                List.of(
                                        opt("每周第一个交易日", "first_trading_day_of_week"),
                                        opt("每周最后一个交易日", "last_trading_day_of_week"),
                                        opt("每月第一个交易日", "first_trading_day_of_month"),
                                        opt("每月最后一个交易日", "last_trading_day_of_month")
                                ), "task",
                                "rebalance", List.of("weekly", "monthly"),
                                "rebalance",
                                Map.of(
                                        "weekly", List.of("first_trading_day_of_week", "last_trading_day_of_week"),
                                        "monthly", List.of("first_trading_day_of_month", "last_trading_day_of_month")
                                ))
                ))
                .build());

        // 组3: 观测质量
        groups.add(ConfigGroup.builder()
                .name("quality").label("观测日数据质量").icon("Filter")
                .description("控制因子计算时允许的最低数据质量")
                .fields(List.of(
                        numField("min_valid_ratio_per_observation", "观测日有效数据占比最低比例",
                                values.getOrDefault("min_valid_ratio_per_observation", 0.8), 0.8,
                                "单个观测日中有效值数据占比低于此值则本观察日不参与因子IC/IR横截面计算", 0, 1, 0.05, 2, true)
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_LABEL)
                .sectionLabel("收益标签与调仓")
                .sectionDescription("配置收益标签的列名、收益类型、价格列和观察日参数")
                .sectionIcon("DataLine")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 6: 窗口选择
    // ========================================================================
    private StructuredConfigResponse buildSelectorTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_SELECTOR);
        Map<String, Object> analysisValues = getSectionMap(taskId, TAB_ANALYSIS);
        List<ConfigGroup> groups = new ArrayList<>();

        // 组1: Test 时间窗口与迭代轮数
        groups.add(ConfigGroup.builder()
                .name("test_window").label("回测时间窗口").icon("Calendar")
                .description("样本外测试时间段，修改后会同步更新到分析口径")
                .fields(List.of(
                        field("test_start_date", "回测开始日期", "date",
                                analysisValues.get("test_start_date"), "",
                                "样本外测试开始日期", null, "task"),
                        field("test_end_date", "回测结束日期", "date",
                                analysisValues.get("test_end_date"), "",
                                "样本外测试结束日期", null, "task")
                ))
                .build());

        // 组2: 训练窗口设定方式
        String trainWindowSource = String.valueOf(values.getOrDefault("train_window_source", "recommend"));
        groups.add(ConfigGroup.builder()
                .name("train_window_source").label("训练窗口设定方式").icon("Select")
                .description("选择手动填写训练窗口，或基于回测时间窗口自动推荐")
                .fields(List.of(
                        field("train_window_source", "设定方式", "select",
                                trainWindowSource, "recommend",
                                "选择自行设定时，不显示训练窗口选择器推荐参数",
                                List.of(opt("自行设定训练窗口", "manual"), opt("基于回测时间窗口推荐", "recommend")), "task")
                ))
                .build());

        // 组3: 选择器参数（推荐模式时显示，先于"训练窗口"，让选择器+启动推荐按钮在训练窗口之上）
        groups.add(ConfigGroup.builder()
                .name("selector").label("训练窗口选择器").icon("Aim")
                .description("为当前test窗口推荐最相似的历史训练窗口")
                .fields(List.of(
                        numField("lookback_years", "回看历史年数",
                                values.getOrDefault("lookback_years", 10), 10,
                                "以test_start_date为锚点向前回看多少年", 1, 10, 1, 0, true,
                                "train_window_source", "recommend"),
                        field("recommend_span_months", "推荐窗口长度（月）", "tag_select",
                                values.getOrDefault("recommend_span_months", List.of(12, 18, 24)),
                                List.of(12, 18, 24),
                                "候选训练窗口的长度，支持多种长度同时评估",
                                List.of(opt("12个月", "12"), opt("18个月", "18"), opt("24个月", "24"), opt("36个月", "36")), "task",
                                "train_window_source", "recommend"),
                        numField("top_k_similar_months", "Top-K 相似月份数",
                                values.getOrDefault("top_k_similar_months", 4), 4,
                                "找出与test状态最相似的前K个月", 1, 20, 1, 0, true,
                                "train_window_source", "recommend"),
                        numField("score_similarity_weight", "平均相似度权重",
                                values.getOrDefault("score_similarity_weight", 0.7), 0.7,
                                "与 score_coverage_weight 之和建议为1", 0, 1, 0.05, 2, true,
                                "train_window_source", "recommend"),
                        numField("score_coverage_weight", "Top-K覆盖度权重",
                                values.getOrDefault("score_coverage_weight", 0.3), 0.3,
                                "与 score_similarity_weight 之和建议为1", 0, 1, 0.05, 2, true,
                                "train_window_source", "recommend"),
                        field("disable_dynamic_membership", "临时关闭动态成分股", "switch",
                                values.getOrDefault("disable_dynamic_membership", true), true,
                                "selector内部关闭dynamic_membership可显著减少回溯请求", null, "task",
                                "train_window_source", "recommend")
                ))
                .build());

        groups.add(ConfigGroup.builder()
                .name("manual_train_window").label("训练窗口").icon("Calendar")
                .description("设定样本内训练区间。推荐模式下也可点击推荐结果的“应用此窗口”自动填入")
                .fields(List.of(
                        field("manual_train_start_date", "训练开始日期", "date",
                                analysisValues.get("train_start_date"), "",
                                "样本内训练开始日期", null, "task"),
                        field("manual_train_end_date", "训练结束日期", "date",
                                analysisValues.get("train_end_date"), "",
                                "样本内训练结束日期", null, "task")
                ))
                .build());

        // 组3: 训练工作流模式（始终显示，不受推荐窗口是否应用影响）
        {
            Object trainingWorkflowRaw = analysisValues.get("training_workflow");
            Map<String, Object> trainingWorkflow = trainingWorkflowRaw instanceof Map
                    ? (Map<String, Object>) trainingWorkflowRaw
                    : new LinkedHashMap<>();
            String mode = (String) trainingWorkflow.getOrDefault("mode", "static_split");

            groups.add(ConfigGroup.builder()
                    .name("training_workflow").label("训练工作流模式").icon("Operation")
                    .description("选择训练窗口切分方式与迭代轮数")
                    .fields(List.of(
                            field("training_workflow.mode", "模式", "select",
                                    mode, "static_split",
                                    "static_split=固定两段切分，walk_forward=滚动窗口",
                                    List.of(opt("固定两段切分", "static_split"), opt("滚动窗口", "walk_forward")), "task"),
                            field("training_workflow.static_split.discovery_start_date", "发现期开始", "date",
                                    getNestedValue(trainingWorkflow, "static_split", "discovery_start_date"), "",
                                    "静态切分：因子发现阶段开始", null, "task",
                                    "training_workflow.mode", "static_split"),
                            field("training_workflow.static_split.discovery_end_date", "发现期结束", "date",
                                    getNestedValue(trainingWorkflow, "static_split", "discovery_end_date"), "",
                                    "静态切分：因子发现阶段结束", null, "task",
                                    "training_workflow.mode", "static_split"),
                            field("training_workflow.static_split.validation_start_date", "验证期开始", "date",
                                    getNestedValue(trainingWorkflow, "static_split", "validation_start_date"), "",
                                    "静态切分：因子验证阶段开始", null, "task",
                                    "training_workflow.mode", "static_split"),
                            field("training_workflow.static_split.validation_end_date", "验证期结束", "date",
                                    getNestedValue(trainingWorkflow, "static_split", "validation_end_date"), "",
                                    "静态切分：因子验证阶段结束", null, "task",
                                    "training_workflow.mode", "static_split"),
                            numField("training_workflow.walk_forward.discovery_window_months", "发现窗口（月）",
                                    getNestedValue(trainingWorkflow, "walk_forward", "discovery_window_months"), 24,
                                    "滚动窗口：每次发现期长度", 1, 60, 1, 0, false,
                                    "training_workflow.mode", "walk_forward"),
                            numField("training_workflow.walk_forward.validation_window_months", "验证窗口（月）",
                                    getNestedValue(trainingWorkflow, "walk_forward", "validation_window_months"), 6,
                                    "滚动窗口：每次验证期长度", 1, 24, 1, 0, false,
                                    "training_workflow.mode", "walk_forward"),
                            numField("training_workflow.walk_forward.step_months", "滚动步长（月）",
                                    getNestedValue(trainingWorkflow, "walk_forward", "step_months"), 6,
                                    "滚动窗口：窗口滚动步长", 1, 24, 1, 0, false,
                                    "training_workflow.mode", "walk_forward"),
                            numField("training_workflow.walk_forward.max_windows", "最大窗口数",
                                    getNestedValue(trainingWorkflow, "walk_forward", "max_windows"), 0,
                                    "0=不限制", 0, 50, 1, 0, false,
                                    "training_workflow.mode", "walk_forward"),
                            field("iteration_count", "迭代轮数", "select",
                                    analysisValues.getOrDefault("iteration_count", 1), 1,
                                    "多轮迭代会基于前一轮反馈继续挖掘",
                                    List.of(opt("1轮", "1"), opt("2轮", "2"), opt("3轮", "3"), opt("5轮", "5")), "task")
                    ))
                    .build());
        }

        return StructuredConfigResponse.builder()
                .section(TAB_SELECTOR)
                .sectionLabel("训练窗口与回测窗口")
                .sectionDescription("配置回测窗口并推荐最相似的历史训练窗口")
                .sectionIcon("Aim")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 6: 因子初筛与打分规则
    // ========================================================================
    private StructuredConfigResponse buildPrescreenScoreTab(Long taskId) {
        Map<String, Object> prescreenValues = getSectionMap(taskId, TAB_PRESCREEN);
        Map<String, Object> scoreValues = getSectionMap(taskId, TAB_SCORE);
        List<ConfigGroup> groups = new ArrayList<>();

        // 组1: 基础预筛门槛
        groups.add(ConfigGroup.builder()
                .name("prescreen_basic").label("基础预筛门槛").icon("Filter")
                .description("不满足以下全部条件则跳过回测")
                .fields(List.of(
                        numField("min_rank_ic_to_backtest", "Rank IC 绝对值门槛",
                                prescreenValues.getOrDefault("min_rank_ic_to_backtest", 0.02), 0.02,
                                "Rank IC 绝对值低于此值跳过", 0, 0.2, 0.005, 3, true),
                        numField("min_rank_ic_ir_to_backtest", "Rank IC IR 门槛",
                                prescreenValues.getOrDefault("min_rank_ic_ir_to_backtest", 0.2), 0.2,
                                "Rank IC IR 低于此值跳过", 0, 2, 0.05, 2, true),
                        numField("min_positive_ic_ratio", "IC正方向胜率门槛",
                                prescreenValues.getOrDefault("min_positive_ic_ratio", 0.4), 0.4,
                                "IC为正（或负）的比例低于此值跳过", 0, 1, 0.05, 2, true),
                        field("enable_direction_filter", "启用方向过滤", "switch",
                                prescreenValues.getOrDefault("enable_direction_filter", false), false,
                                "要求LLM猜测方向与样本内经验方向一致", null, "task")
                ))
                .build());

        // 组2: 单调性与稳定性预筛
        groups.add(ConfigGroup.builder()
                .name("prescreen_advanced").label("单调性与稳定性预筛").icon("Histogram")
                .description("高级预筛门槛：因子结构质量与跨年稳健性")
                .fields(List.of(
                        numField("min_monotonicity_score_to_backtest", "单调性评分门槛",
                                prescreenValues.getOrDefault("min_monotonicity_score_to_backtest", 0.3), 0.3,
                                "单调性评分阈值（0~1，低于此值跳过）", 0, 1, 0.05, 2, true),
                        numField("max_monotonicity_violation_ratio_to_backtest", "单调性反向比例上限",
                                prescreenValues.getOrDefault("max_monotonicity_violation_ratio_to_backtest", 0.5), 0.5,
                                "单调性反向比例上限（0~1，高于此值跳过）", 0, 1, 0.05, 2, true),
                        numField("min_yearly_stability_score_to_backtest", "跨年稳定性评分门槛",
                                prescreenValues.getOrDefault("min_yearly_stability_score_to_backtest", 0.2), 0.2,
                                "跨年稳定性评分阈值（0~1，低于此值跳过）", 0, 1, 0.05, 2, true),
                        numField("min_neutralized_ic_retention_to_backtest", "中性化后IC保留比例门槛",
                                prescreenValues.getOrDefault("min_neutralized_ic_retention_to_backtest", 0.2), 0.2,
                                "中性化后IC保留比例门槛（0~1，低于此值跳过）", 0, 1, 0.05, 2, true)
                ))
                .build());

        // 组3: regime一致性预筛
        groups.add(ConfigGroup.builder()
                .name("prescreen_regime").label("regime一致性预筛").icon("ScaleToOriginal")
                .description("诊断性门槛：验证LLM声明的失效regime是否与实际数据一致")
                .fields(List.of(
                        field("regime_consistency_gate", "regime一致性拦截策略", "select",
                                prescreenValues.getOrDefault("regime_consistency_gate", "none"), "none",
                                "none=不拦截，inconsistent=只拦截不一致，strict=拦截不一致和中性",
                                List.of(opt("不拦截", "none"), opt("拦截不一致", "inconsistent"), opt("严格（仅放行一致）", "strict")), "task"),
                        ConfigField.builder()
                                .key("regime_analysis.regime_min_valid_ratio_per_observation").label("观测有效覆盖率门槛").type("number")
                                .value(getNestedValue(prescreenValues, "regime_analysis", "regime_min_valid_ratio_per_observation"))
                                .defaultValue(0.8)
                                .description("regime切片分析时单个观测日的有效数据比例门槛")
                                .min(0).max(1).step(0.05).precision(2).required(true).source("task")
                                .disableWhenKey("regime_consistency_gate").disableWhenValue("none")
                                .build(),
                        ConfigField.builder()
                                .key("regime_analysis.min_observation_count").label("最小观测日数").type("number")
                                .value(getNestedValue(prescreenValues, "regime_analysis", "min_observation_count"))
                                .defaultValue(4)
                                .description("regime子样本最少需要的观测日数，少于此值按中性分处理")
                                .min(1).max(100).step(1).precision(0).required(true).source("task")
                                .disableWhenKey("regime_consistency_gate").disableWhenValue("none")
                                .build(),
                        ConfigField.builder()
                                .key("regime_analysis.ic_tolerance").label("IC容差").type("number")
                                .value(getNestedValue(prescreenValues, "regime_analysis", "ic_tolerance"))
                                .defaultValue(0.002)
                                .description("regime一致性判断中IC差异的容差阈值")
                                .min(0).max(0.1).step(0.001).precision(3).required(true).source("task")
                                .disableWhenKey("regime_consistency_gate").disableWhenValue("none")
                                .build(),
                        ConfigField.builder()
                                .key("regime_analysis.win_rate_tolerance").label("胜率容差").type("number")
                                .value(getNestedValue(prescreenValues, "regime_analysis", "win_rate_tolerance"))
                                .defaultValue(0.05)
                                .description("regime一致性判断中胜率差异的容差阈值")
                                .min(0).max(0.5).step(0.01).precision(2).required(true).source("task")
                                .disableWhenKey("regime_consistency_gate").disableWhenValue("none")
                                .build(),
                        ConfigField.builder()
                                .key("regime_analysis.long_short_tolerance").label("多空收益容差").type("number")
                                .value(getNestedValue(prescreenValues, "regime_analysis", "long_short_tolerance"))
                                .defaultValue(0.001)
                                .description("regime一致性判断中多空收益差异的容差阈值")
                                .min(0).max(0.1).step(0.001).precision(3).required(true).source("task")
                                .disableWhenKey("regime_consistency_gate").disableWhenValue("none")
                                .build()
                ))
                .build());

        // 组2: 因子评分权重
        groups.add(ConfigGroup.builder()
                .name("score_weights").label("因子评分权重").icon("TrendCharts")
                .description("所有正权重之和 - 负权重之和 = 1.0")
                .fields(List.of(
                        numField("weights.ic_stability", "IC 稳定性权重",
                                getNestedValue(scoreValues, "weights", "ic_stability"), 0.35,
                                "Rank IC IR的归一化值，衡量因子预测能力的稳定性", 0, 1, 0.05, 2, true),
                        numField("weights.annual_return", "年化收益权重",
                                getNestedValue(scoreValues, "weights", "annual_return"), 0.50,
                                "年化收益率的归一化值，衡量因子实际盈利能力", 0, 1, 0.05, 2, true),
                        numField("weights.drawdown", "回撤惩罚权重",
                                getNestedValue(scoreValues, "weights", "drawdown"), 0.05,
                                "最大回撤绝对值的归一化值，惩罚高风险因子", 0, 1, 0.05, 2, true),
                        numField("weights.turnover", "换手率惩罚权重",
                                getNestedValue(scoreValues, "weights", "turnover"), 0.05,
                                "换手率的归一化值，惩罚过度交易", 0, 1, 0.05, 2, true),
                        numField("weights.instability", "IC方向不稳定惩罚权重",
                                getNestedValue(scoreValues, "weights", "instability"), 0.05,
                                "(1-positive_ic_ratio)的归一化值，惩罚IC方向不稳定", 0, 1, 0.05, 2, true),
                        numField("negative_return_penalty", "负收益惩罚",
                                scoreValues.getOrDefault("negative_return_penalty", 0.5), 0.5,
                                "当年化收益率<0时，total_score额外减去此值", 0, 2, 0.1, 2, true)
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_PRESCREEN)
                .sectionLabel("因子初筛与打分规则")
                .sectionDescription("配置因子回测前的预筛门槛和评分权重")
                .sectionIcon("Filter")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // Tab 2: Tushare 数据配置
    // ========================================================================
    private StructuredConfigResponse buildTushareTab(Long taskId) {
        Map<String, Object> values = getSectionMap(taskId, TAB_TUSHARE);
        List<ConfigGroup> groups = new ArrayList<>();

        groups.add(ConfigGroup.builder()
                .name("data_source").label("数据源参数").icon("DataLine")
                .description("Tushare 数据源的基础参数")
                .fields(List.of(
                        field("freq", "数据频率", "select",
                                values.getOrDefault("freq", "day"), "day",
                                "当前仅支持日线",
                                List.of(opt("日线", "day")), "task"),
                        numField("placeholder_expire_days", "占位符过期天数",
                                values.getOrDefault("placeholder_expire_days", 3), 3,
                                "Tushare暂时获取不到数据时插入占位符，超过此天数自动过期重新拉取", 1, 30, 1, 0, true)
                ))
                .build());

        groups.add(ConfigGroup.builder()
                .name("price_adjust").label("复权口径").icon("TrendCharts")
                .description("股票价格复权方式")
                .fields(List.of(
                        field("price_adjust", "复权方式", "select",
                                values.getOrDefault("price_adjust", "none"), "none",
                                "none=不复权，pre=前复权，post=后复权",
                                List.of(opt("不复权", "none"), opt("前复权", "pre"), opt("后复权", "post")), "task"),
                        field("price_adjust_auto", "自动取本次任务的 load_end", "switch",
                                values.getOrDefault("price_adjust_auto", true), true,
                                "开启后复权参考日自动为 auto", null, "task"),
                        ConfigField.builder()
                                .key("price_adjust_reference_date").label("复权参考日").type("date")
                                .value(values.getOrDefault("price_adjust_reference_date", "")).defaultValue("")
                                .description("选择固定复权参考日期")
                                .source("task")
                                .showWhenKey("price_adjust_auto").showWhenValue(false)
                                .build()
                ))
                .build());

        groups.add(ConfigGroup.builder()
                .name("preprocess").label("预处理设置").icon("Operation")
                .description("因子预处理：异常值、中性化")
                .fields(List.of(
                        field("preprocess.outlier_method", "异常值处理", "select",
                                getNestedValue(values, "preprocess", "outlier_method"), "none",
                                "none/mad/quantile/sigma",
                                List.of(opt("不处理", "none"), opt("MAD", "mad"), opt("分位数裁剪", "quantile"), opt("3-Sigma", "sigma")), "task"),
                        ConfigField.builder()
                                .key("preprocess.outlier_options.n").label("异常值参数 n").type("number")
                                .value(getNestedValue(values, "preprocess", "outlier_options.n")).defaultValue(3)
                                .description("MAD/Sigma 的倍数（正整数）")
                                .min(1).max(10).step(1).precision(0)
                                .source("task")
                                .showWhenKey("preprocess.outlier_method").showWhenValue(List.of("mad", "sigma"))
                                .build(),
                        ConfigField.builder()
                                .key("preprocess.outlier_options.lower_quantile").label("下分位数").type("number")
                                .value(getNestedValue(values, "preprocess", "outlier_options.lower_quantile")).defaultValue(0.01)
                                .description("quantile 方法下界")
                                .min(0).max(1).step(0.01).precision(2)
                                .source("task")
                                .showWhenKey("preprocess.outlier_method").showWhenValue("quantile")
                                .build(),
                        ConfigField.builder()
                                .key("preprocess.outlier_options.upper_quantile").label("上分位数").type("number")
                                .value(getNestedValue(values, "preprocess", "outlier_options.upper_quantile")).defaultValue(0.99)
                                .description("quantile 方法上界")
                                .min(0).max(1).step(0.01).precision(2)
                                .source("task")
                                .showWhenKey("preprocess.outlier_method").showWhenValue("quantile")
                                .build(),
                        field("preprocess.neutralization", "中性化", "select",
                                getNestedValue(values, "preprocess", "neutralization"), "none",
                                "none/industry/market_cap/industry_market_cap",
                                List.of(opt("不处理", "none"), opt("行业", "industry"), opt("市值", "market_cap"), opt("行业+市值", "industry_market_cap")), "task"),
                        field("preprocess.neutralization_options.industry_field", "行业字段", "select",
                                getNestedValue(values, "preprocess", "neutralization_options.industry_field"), "industry",
                                "行业分类字段名",
                                List.of(opt("industry", "industry")), "task"),
                        field("preprocess.neutralization_options.market_cap_field", "市值字段", "select",
                                getNestedValue(values, "preprocess", "neutralization_options.market_cap_field"), "market_cap",
                                "市值字段名",
                                List.of(opt("market_cap", "market_cap")), "task")
                ))
                .build());

        return StructuredConfigResponse.builder()
                .section(TAB_TUSHARE)
                .sectionLabel("数据配置")
                .sectionDescription("配置 Tushare 数据源参数、复权口径和预处理设置")
                .sectionIcon("DataLine")
                .groups(groups)
                .build();
    }

    // ========================================================================
    // ========================================================================
    // 工具方法
    // ========================================================================

    private ConfigField field(String key, String label, String type, Object value, Object defaultValue,
                              String description, List<SelectOption> options, String source) {
        return field(key, label, type, value, defaultValue, description, options, source, null, null, null, null);
    }

    private ConfigField field(String key, String label, String type, Object value, Object defaultValue,
                              String description, List<SelectOption> options, String source,
                              String showWhenKey, Object showWhenValue) {
        return field(key, label, type, value, defaultValue, description, options, source, showWhenKey, showWhenValue, null, null);
    }

    private ConfigField field(String key, String label, String type, Object value, Object defaultValue,
                              String description, List<SelectOption> options, String source,
                              String showWhenKey, Object showWhenValue,
                              String optionFilterKey, Map<String, List<String>> optionFilterMap) {
        return ConfigField.builder()
                .key(key).label(label).type(type)
                .value(value).defaultValue(defaultValue)
                .description(description)
                .options(options)
                .source(source)
                .showWhenKey(showWhenKey)
                .showWhenValue(showWhenValue)
                .optionFilterKey(optionFilterKey)
                .optionFilterMap(optionFilterMap)
                .build();
    }

    private ConfigField field(String key, String label, String type, Object value, Object defaultValue,
                              String description, List<SelectOption> options, String source,
                              boolean readonly) {
        return ConfigField.builder()
                .key(key).label(label).type(type)
                .value(value).defaultValue(defaultValue)
                .description(description)
                .options(options)
                .source(source)
                .readonly(readonly)
                .build();
    }

    private ConfigField field(String key, String label, String type, Object value, Object defaultValue,
                              String description, List<SelectOption> options, String source,
                              boolean required, boolean readonly) {
        return ConfigField.builder()
                .key(key).label(label).type(type)
                .value(value).defaultValue(defaultValue)
                .description(description)
                .options(options)
                .source(source)
                .required(required)
                .readonly(readonly)
                .build();
    }

    /** 带数值约束的字段构造 */
    private ConfigField numField(String key, String label, Object value, Object defaultValue,
                                 String description, Number min, Number max, Number step, Integer precision,
                                 boolean required) {
        return numField(key, label, value, defaultValue, description, min, max, step, precision, required, null, null);
    }

    private ConfigField numField(String key, String label, Object value, Object defaultValue,
                                 String description, Number min, Number max, Number step, Integer precision,
                                 boolean required, String showWhenKey, Object showWhenValue) {
        return numField(key, label, value, defaultValue, description, min, max, step, precision, required, showWhenKey, showWhenValue, null, null);
    }

    private ConfigField numField(String key, String label, Object value, Object defaultValue,
                                 String description, Number min, Number max, Number step, Integer precision,
                                 boolean required, String showWhenKey, Object showWhenValue,
                                 String showWhenKey2, Object showWhenValue2) {
        return ConfigField.builder()
                .key(key).label(label).type("number")
                .value(value).defaultValue(defaultValue)
                .description(description)
                .min(min).max(max).step(step).precision(precision)
                .required(required)
                .source("task")
                .showWhenKey(showWhenKey)
                .showWhenValue(showWhenValue)
                .showWhenKey2(showWhenKey2)
                .showWhenValue2(showWhenValue2)
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

    @SuppressWarnings("unchecked")
    private Map<String, Object> getSectionMap(Long taskId, String section) {
        return taskConfigRepository.findByTaskIdAndSection(taskId, section)
                .map(config -> {
                    try {
                        return (Map<String, Object>) objectMapper.readValue(config.getValue(), new TypeReference<Map<String, Object>>() {});
                    } catch (Exception e) {
                        log.warn("解析任务配置失败: taskId={}, section={}", taskId, section, e);
                        return new HashMap<String, Object>();
                    }
                })
                .orElseGet(HashMap::new);
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

    /**
     * 测试 LLM 连接
     * 使用 OpenAI 兼容格式发送最小请求，验证 model/base_url/api_key 是否可用。
     */
    public Map<String, Object> testLlmConnection(Long userId, Map<String, String> params) {
        String baseUrl = params.getOrDefault("base_url", "").trim();
        String model = params.getOrDefault("model", "").trim();
        String apiKey = params.getOrDefault("api_key", "").trim();
        double temperature = toDouble(params.get("temperature"), 0.2);
        int maxTokens = toInt(params.get("max_tokens"), 256);
        int timeoutSeconds = toInt(params.get("timeout_seconds"), 30);

        if (baseUrl.isEmpty() || model.isEmpty() || apiKey.isEmpty()) {
            return Map.of("success", false, "message", "请先填写 API 地址、模型名称和 API Key");
        }

        String chatUrl = baseUrl.endsWith("/") ? baseUrl + "chat/completions" : baseUrl + "/chat/completions";
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("model", model);
        body.put("messages", List.of(Map.of("role", "user", "content", "hello")));
        body.put("temperature", temperature);
        body.put("max_tokens", maxTokens);
        body.put("stream", false);

        String requestBody;
        try {
            requestBody = objectMapper.writeValueAsString(body);
        } catch (JsonProcessingException e) {
            return Map.of("success", false, "message", "构造请求失败: " + e.getMessage());
        }

        HttpClient client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(Math.min(timeoutSeconds, 60)))
                .build();
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(chatUrl))
                .header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + apiKey)
                .POST(HttpRequest.BodyPublishers.ofString(requestBody))
                .timeout(Duration.ofSeconds(Math.min(timeoutSeconds, 60)))
                .build();

        try {
            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
            int statusCode = response.statusCode();
            if (statusCode >= 200 && statusCode < 300) {
                return Map.of("success", true, "message", "连接成功（HTTP " + statusCode + ")");
            }
            String snippet = response.body();
            if (snippet.length() > 300) snippet = snippet.substring(0, 300);
            return Map.of("success", false, "message", "HTTP " + statusCode + ": " + snippet);
        } catch (Exception e) {
            return Map.of("success", false, "message", "请求异常: " + e.getMessage());
        }
    }
}
