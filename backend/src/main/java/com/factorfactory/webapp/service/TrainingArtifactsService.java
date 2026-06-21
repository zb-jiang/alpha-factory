package com.factorfactory.webapp.service;

import com.factorfactory.webapp.entity.Task;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * 读取 step10 因子挖掘流水线生成的结构化产物，为前端"因子挖掘"TAB 的展示区提供数据。
 *
 * 数据来源（相对于 task.stagingPath/outputs/）：
 * - backtest/training_windows.json                              — 窗口列表（起止时间）
 * - train_windows/<window>/window_summary.json                  — 窗口汇总
 * - train_windows/<window>/discovery/iter_<NN>/health/...       — 特征体检 / 市场环境
 * - train_windows/<window>/discovery/iter_<NN>/backtest/...     — 每个 iter 的回测指标 + top3
 * - train_windows/<window>/validation/backtest/...              — 验证阶段的回测指标 + top3
 * - backtest/cross_window_factor_ranking.csv                    — 跨窗口排名
 * - backtest/cross_window_summary.json                          — 跨窗口摘要
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class TrainingArtifactsService {

    private final TaskService taskService;
    private final ObjectMapper objectMapper;

    /** factor_metrics.csv / strategy_metrics.csv / final_score.csv 中我们关心的字段（取并集） */
    private static final List<String> FACTOR_FIELDS = List.of(
            "factor_name", "formula", "logic", "risk",
            "mean_ic", "mean_rank_ic", "ic_ir", "rank_ic_ir",
            "positive_ic_ratio", "coverage", "observation_count",
            "llm_direction", "empirical_direction",
            "annualized_return", "total_score",
            "max_drawdown", "sharpe_ratio"
    );

    public Map<String, Object> getArtifacts(Long userId, Long taskId) {
        Task task = taskService.findTaskBelongsToUser(userId, taskId);
        Path outputs = Paths.get(task.getStagingPath(), "outputs");

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", task.getStatus().name());
        List<Map<String, Object>> windows = readWindows(outputs);
        Map<String, Object> crossWindow = readCrossWindow(outputs);
        result.put("windows", windows);
        result.put("cross_window", crossWindow);

        // 训练阶段就绪情况：判断 step10 是否产出了任何可用于 OOS 回测的因子。
        // step11.collect_train_top_factors() 的判据：
        //   1) cross_window_top3_factors.json 的 top3 数组非空，或
        //   2) train_windows/*/validation/backtest/top3_factors.json 任一非空
        Map<String, Object> readiness = new LinkedHashMap<>();
        readiness.put("oos_factors_ready", hasAnyOosCapableFactor(windows, crossWindow));
        result.put("readiness", readiness);
        return result;
    }

    /** 判断是否存在任何可送 step11 的 top3 因子（与 step11.collect_train_top_factors 的查找规则一致）。 */
    private boolean hasAnyOosCapableFactor(List<Map<String, Object>> windows, Map<String, Object> crossWindow) {
        // 1) cross_window.top3 非空
        Object crossTop3 = crossWindow == null ? null : crossWindow.get("top3");
        if (crossTop3 instanceof List<?> && !((List<?>) crossTop3).isEmpty()) {
            return true;
        }
        // 2) 任一窗口的 validation.top3 非空
        if (windows != null) {
            for (Map<String, Object> w : windows) {
                Object validation = w.get("validation");
                if (validation instanceof Map<?, ?>) {
                    Object t3 = ((Map<?, ?>) validation).get("top3");
                    if (t3 instanceof List<?> && !((List<?>) t3).isEmpty()) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    // ===== 窗口级 =====

    private List<Map<String, Object>> readWindows(Path outputs) {
        // 1. 窗口定义来自 training_windows.json
        List<Map<String, Object>> windowDefs = readWindowDefinitions(outputs);

        // 2. 同时枚举磁盘上已存在的 train_windows/window_XX/ 目录（覆盖配置外的情况，且兼容运行中尚未写入 training_windows.json）
        Set<String> dirIds = listWindowDirs(outputs);
        Map<String, Map<String, Object>> byId = new LinkedHashMap<>();
        for (Map<String, Object> def : windowDefs) {
            String id = String.valueOf(def.get("window_id"));
            byId.put(id, def);
        }
        for (String id : dirIds) {
            byId.computeIfAbsent(id, key -> {
                Map<String, Object> bare = new LinkedHashMap<>();
                bare.put("window_id", key);
                return bare;
            });
        }

        // 3. 按 window_id 排序输出
        List<String> sortedIds = new ArrayList<>(byId.keySet());
        sortedIds.sort(Comparator.naturalOrder());
        List<Map<String, Object>> windows = new ArrayList<>(sortedIds.size());
        for (String id : sortedIds) {
            Map<String, Object> w = byId.get(id);
            enrichWindow(outputs, id, w);
            windows.add(w);
        }
        return windows;
    }

    private List<Map<String, Object>> readWindowDefinitions(Path outputs) {
        Path file = outputs.resolve("backtest").resolve("training_windows.json");
        Map<String, Object> root = readJsonObject(file);
        Object list = root.get("windows");
        if (list instanceof List<?>) {
            return ((List<?>) list).stream()
                    .filter(item -> item instanceof Map<?, ?>)
                    .map(item -> (Map<String, Object>) new LinkedHashMap<>((Map<String, Object>) item))
                    .collect(Collectors.toList());
        }
        return new ArrayList<>();
    }

    private Set<String> listWindowDirs(Path outputs) {
        Path trainWindowsDir = outputs.resolve("train_windows");
        if (!Files.isDirectory(trainWindowsDir)) {
            return Collections.emptySet();
        }
        try (Stream<Path> stream = Files.list(trainWindowsDir)) {
            return stream
                    .filter(Files::isDirectory)
                    .map(p -> p.getFileName().toString())
                    .filter(name -> name.startsWith("window_"))
                    .collect(Collectors.toCollection(TreeSet::new));
        } catch (IOException e) {
            return Collections.emptySet();
        }
    }

    private void enrichWindow(Path outputs, String windowId, Map<String, Object> w) {
        Path windowRoot = outputs.resolve("train_windows").resolve(windowId);

        // 窗口摘要
        Map<String, Object> windowSummary = readJsonObject(windowRoot.resolve("window_summary.json"));
        if (!windowSummary.isEmpty()) {
            w.put("window_summary", windowSummary);
        }

        // discovery 阶段
        Map<String, Object> discovery = new LinkedHashMap<>();
        discovery.put("iterations", readDiscoveryIterations(windowRoot));
        // 特征体检 + 市场环境取 iter_01 的归档（每个 iter 都有一份，但 train_context 在窗口内不会变）
        Path firstIterRoot = windowRoot.resolve("discovery").resolve("iter_01");
        discovery.put("health_summary", readJsonObject(firstIterRoot.resolve("health").resolve("health_summary.json")));
        discovery.put("market_context", readJsonObject(firstIterRoot.resolve("health").resolve("market_context.json")));
        // discovery 阶段汇总候选因子（按 formula 去重，保留各 iter 中最高 total_score）
        discovery.put("candidates", aggregateDiscoveryCandidates(windowRoot));
        w.put("discovery", discovery);

        // validation 阶段
        Map<String, Object> validation = new LinkedHashMap<>();
        Path validationDir = windowRoot.resolve("validation");
        List<Map<String, Object>> validationFactors = readFactorTable(validationDir);
        // 给 validation 因子表补充 formula / logic / risk / source_iteration（与 discovery 候选保持一致体验）
        Map<String, Map<String, Object>> validationDetails = readValidatedFactorsByName(
                validationDir.resolve("llm").resolve("factors_validated.json"));
        for (Map<String, Object> row : validationFactors) {
            Object name = row.get("factor_name");
            if (name == null) continue;
            Map<String, Object> detail = validationDetails.get(String.valueOf(name));
            if (detail != null) {
                if (detail.get("formula") != null) row.put("formula", detail.get("formula"));
                if (detail.get("logic") != null) row.put("logic", detail.get("logic"));
                if (detail.get("risk") != null) row.put("risk", detail.get("risk"));
                if (detail.get("discovery_iteration") != null) {
                    row.put("source_iteration", detail.get("discovery_iteration"));
                }
            }
        }
        validation.put("factors", validationFactors);
        validation.put("top3", readTop3Json(validationDir));
        w.put("validation", validation);
    }

    private List<Map<String, Object>> readDiscoveryIterations(Path windowRoot) {
        Path discoveryDir = windowRoot.resolve("discovery");
        if (!Files.isDirectory(discoveryDir)) {
            return Collections.emptyList();
        }
        List<Path> iterDirs;
        try (Stream<Path> stream = Files.list(discoveryDir)) {
            iterDirs = stream
                    .filter(Files::isDirectory)
                    .filter(p -> p.getFileName().toString().startsWith("iter_"))
                    .sorted(Comparator.comparing(p -> p.getFileName().toString()))
                    .collect(Collectors.toList());
        } catch (IOException e) {
            return Collections.emptyList();
        }
        List<Map<String, Object>> result = new ArrayList<>(iterDirs.size());
        for (Path iter : iterDirs) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("iteration", iter.getFileName().toString());
            entry.put("top3", readTop3Json(iter));
            entry.put("factors", readFactorTable(iter));
            result.add(entry);
        }
        return result;
    }

    /** 把所有 iter 的 final_score.csv 合并去重，每个 formula 保留 total_score 最高的版本 */
    private List<Map<String, Object>> aggregateDiscoveryCandidates(Path windowRoot) {
        Path discoveryDir = windowRoot.resolve("discovery");
        if (!Files.isDirectory(discoveryDir)) {
            return Collections.emptyList();
        }
        Map<String, Map<String, Object>> byFormula = new LinkedHashMap<>();
        try (Stream<Path> stream = Files.list(discoveryDir)) {
            stream
                    .filter(Files::isDirectory)
                    .filter(p -> p.getFileName().toString().startsWith("iter_"))
                    .sorted(Comparator.comparing(p -> p.getFileName().toString()))
                    .forEach(iter -> {
                        List<Map<String, Object>> rows = readFactorTable(iter);
                        // 关键：formula 的权威来源是 llm/factors_validated.json（包含全部通过验证的因子），
                        // 而不是 top3_factors.json（只含前 3 名）。step10 自身的 _collect_discovery_passed_factors
                        // 也是从 factors_validated.json 读取的，我们对齐它。
                        Map<String, Map<String, Object>> validatedByName = readValidatedFactorsByName(
                                iter.resolve("llm").resolve("factors_validated.json"));
                        for (Map<String, Object> row : rows) {
                            Object name = row.get("factor_name");
                            if (name == null) continue;
                            String factorName = String.valueOf(name);
                            Map<String, Object> detail = validatedByName.get(factorName);
                            // 把 formula / logic / risk 等元信息补到 row 上
                            if (detail != null) {
                                if (detail.get("formula") != null) row.put("formula", detail.get("formula"));
                                if (detail.get("logic") != null) row.put("logic", detail.get("logic"));
                                if (detail.get("risk") != null) row.put("risk", detail.get("risk"));
                            }
                            // 标记来源 iter（与 Python 端 discovery_iteration 一致）
                            row.put("source_iteration", iter.getFileName().toString());

                            // 用 formula（若无则用 factor_name）做去重 key
                            String formula = row.get("formula") == null ? "" : String.valueOf(row.get("formula"));
                            String key = formula.isEmpty() ? factorName : formula;
                            double score = toDouble(row.get("total_score"));
                            Map<String, Object> existed = byFormula.get(key);
                            if (existed == null || toDouble(existed.get("total_score")) < score) {
                                byFormula.put(key, row);
                            }
                        }
                    });
        } catch (IOException e) {
            return Collections.emptyList();
        }
        return byFormula.values().stream()
                .sorted((a, b) -> Double.compare(toDouble(b.get("total_score")), toDouble(a.get("total_score"))))
                .collect(Collectors.toList());
    }

    /** 读取一个阶段下的 factors_validated.json，返回 {factor_name -> 完整因子详情}。 */
    private Map<String, Map<String, Object>> readValidatedFactorsByName(Path validatedJsonPath) {
        Map<String, Object> root = readJsonObject(validatedJsonPath);
        Object factors = root.get("factors");
        if (!(factors instanceof List<?>)) {
            return Collections.emptyMap();
        }
        Map<String, Map<String, Object>> result = new HashMap<>();
        for (Object item : (List<?>) factors) {
            if (item instanceof Map<?, ?>) {
                Map<String, Object> m = (Map<String, Object>) item;
                Object name = m.get("factor_name");
                if (name != null) {
                    result.put(String.valueOf(name), m);
                }
            }
        }
        return result;
    }

    /** 读取一个 stage 目录下的 backtest/final_score.csv（若无则读 factor_metrics.csv） */
    private List<Map<String, Object>> readFactorTable(Path stageRoot) {
        Path finalScore = stageRoot.resolve("backtest").resolve("final_score.csv");
        if (Files.exists(finalScore)) {
            return readCsv(finalScore);
        }
        Path metrics = stageRoot.resolve("backtest").resolve("factor_metrics.csv");
        if (Files.exists(metrics)) {
            return readCsv(metrics);
        }
        return Collections.emptyList();
    }

    /** 读取一个 stage 目录下的 backtest/top3_factors.json */
    private List<Map<String, Object>> readTop3Json(Path stageRoot) {
        Path file = stageRoot.resolve("backtest").resolve("top3_factors.json");
        Map<String, Object> root = readJsonObject(file);
        Object top3 = root.get("top3");
        if (top3 instanceof List<?>) {
            return ((List<?>) top3).stream()
                    .filter(item -> item instanceof Map<?, ?>)
                    .map(item -> (Map<String, Object>) new LinkedHashMap<>((Map<String, Object>) item))
                    .collect(Collectors.toList());
        }
        return Collections.emptyList();
    }

    // ===== 跨窗口 =====

    private Map<String, Object> readCrossWindow(Path outputs) {
        Map<String, Object> cross = new LinkedHashMap<>();
        Path backtest = outputs.resolve("backtest");
        Map<String, Object> summary = readJsonObject(backtest.resolve("cross_window_summary.json"));
        if (!summary.isEmpty()) {
            cross.put("summary", summary);
        }
        Path ranking = backtest.resolve("cross_window_factor_ranking.csv");
        if (Files.exists(ranking)) {
            cross.put("ranking", readCsv(ranking));
        }
        Map<String, Object> top3 = readJsonObject(backtest.resolve("cross_window_top3_factors.json"));
        if (!top3.isEmpty()) {
            cross.put("top3", top3.get("top3"));
        }
        return cross;
    }

    // ===== 通用工具 =====

    private Map<String, Object> readJsonObject(Path file) {
        if (!Files.exists(file)) {
            return Collections.emptyMap();
        }
        try {
            JsonNode node = objectMapper.readTree(file.toFile());
            if (node == null || !node.isObject()) {
                return Collections.emptyMap();
            }
            return objectMapper.convertValue(node, new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            log.warn("读取 JSON 失败: {}", file, e);
            return Collections.emptyMap();
        }
    }

    /**
     * 读取 CSV 文件，按表头解析为 List<Map>。不依赖 Apache Commons CSV，
     * 仅在文件较小（< 几 MB，因子表实际只有十几行）时使用，简单实现足够。
     */
    private List<Map<String, Object>> readCsv(Path file) {
        try {
            List<String> lines = Files.readAllLines(file);
            if (lines.size() < 2) {
                return Collections.emptyList();
            }
            String[] headers = splitCsvLine(lines.get(0));
            List<Map<String, Object>> rows = new ArrayList<>(lines.size() - 1);
            for (int i = 1; i < lines.size(); i++) {
                String line = lines.get(i);
                if (line.isEmpty()) continue;
                String[] cells = splitCsvLine(line);
                Map<String, Object> row = new LinkedHashMap<>();
                for (int j = 0; j < headers.length; j++) {
                    String header = headers[j];
                    String value = j < cells.length ? cells[j] : "";
                    row.put(header, parseCell(value));
                }
                rows.add(row);
            }
            return rows;
        } catch (IOException e) {
            log.warn("读取 CSV 失败: {}", file, e);
            return Collections.emptyList();
        }
    }

    /** 简易 CSV 行解析：支持双引号包裹的字段（不处理转义引号——因子表里没有） */
    private String[] splitCsvLine(String line) {
        List<String> tokens = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        boolean inQuotes = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (c == '"') {
                inQuotes = !inQuotes;
            } else if (c == ',' && !inQuotes) {
                tokens.add(current.toString());
                current.setLength(0);
            } else {
                current.append(c);
            }
        }
        tokens.add(current.toString());
        return tokens.toArray(new String[0]);
    }

    /** 尽量把数值列解析成 Number；其余保持字符串 */
    private Object parseCell(String value) {
        if (value == null || value.isEmpty() || "nan".equalsIgnoreCase(value) || "NaN".equals(value)) {
            return null;
        }
        // 简单判断：能 parse 成 double 就当 number；带字母的当字符串
        boolean looksNumeric = true;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (!(Character.isDigit(c) || c == '.' || c == '-' || c == '+' || c == 'e' || c == 'E')) {
                looksNumeric = false;
                break;
            }
        }
        if (!looksNumeric) {
            return value;
        }
        try {
            double d = Double.parseDouble(value);
            if (!Double.isFinite(d)) return null;
            return d;
        } catch (NumberFormatException e) {
            return value;
        }
    }

    private double toDouble(Object value) {
        if (value instanceof Number) return ((Number) value).doubleValue();
        if (value == null) return 0.0;
        try {
            return Double.parseDouble(value.toString());
        } catch (NumberFormatException e) {
            return 0.0;
        }
    }
}
