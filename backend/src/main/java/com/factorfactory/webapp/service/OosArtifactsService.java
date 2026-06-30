package com.factorfactory.webapp.service;

import com.factorfactory.webapp.entity.Task;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;

/**
 * 读取 step11 样本外回测 (OOS) 的结构化产物，为前端"样本外回测"TAB 的展示区提供数据。
 *
 * 数据来源（相对于 task.stagingPath/outputs/，由 step11_oos_test.py 写入）：
 *   llm/factors_validated.json           ← OOS 测试因子清单（含 formula / llm_direction / empirical_direction / reason / risk）
 *   backtest/factor_metrics.csv          ← IC/IR/coverage/observation_count（step07）
 *   backtest/strategy_metrics.csv        ← annualized_return / sharpe / max_drawdown（step08）
 *   backtest/final_score.csv             ← step09 合并产出（包含上面两表 + total_score）
 *   backtest/top3_factors.json           ← OOS Top3
 *
 * 注意：step11 不执行 step02/step03，因此**没有** OOS 区间的特征体检 / 市场环境数据。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class OosArtifactsService {

    private final TaskService taskService;
    private final ObjectMapper objectMapper;

    public Map<String, Object> getArtifacts(Long userId, Long taskId) {
        Task task = taskService.findTaskBelongsToUser(userId, taskId);
        Path stagingRoot = Paths.get(task.getStagingPath());
        Path outputs = stagingRoot.resolve("outputs");

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", task.getStatus().name());

        // 只有 step11 成功完成后才展示 OOS 数据；运行中（RUNNING）或 step10 完成时均不展示，
        // 避免把 step10 validation 残留产物或不完整的中间结果当成 OOS 结果。
        if (task.getStatus() != Task.TaskStatus.TESTING_FINISHED) {
            result.put("factors", Collections.emptyList());
            result.put("top3", Collections.emptyList());
            result.put("input_factors", Collections.emptyList());
            result.put("input_factor_count", 0);
            result.put("period", Collections.emptyMap());
            result.put("readiness", Collections.emptyMap());
            return result;
        }

        // 1. OOS 测试因子清单（含 formula）
        Map<String, Map<String, Object>> validatedByName = readValidatedByName(
                outputs.resolve("llm").resolve("factors_validated.json"));

        // 2. 因子指标表（final_score.csv 优先；不存在时退到 factor_metrics.csv）
        Path finalScoreCsv = outputs.resolve("backtest").resolve("final_score.csv");
        Path metricsCsv = outputs.resolve("backtest").resolve("factor_metrics.csv");
        List<Map<String, Object>> rows;
        if (Files.exists(finalScoreCsv)) {
            rows = readCsv(finalScoreCsv);
        } else if (Files.exists(metricsCsv)) {
            rows = readCsv(metricsCsv);
        } else {
            rows = new ArrayList<>();
        }

        // 把因子清单里的 formula / logic / reason / risk / llm_direction / empirical_direction 合并到回测行
        for (Map<String, Object> row : rows) {
            Object name = row.get("factor_name");
            if (name == null) continue;
            Map<String, Object> detail = validatedByName.get(String.valueOf(name));
            if (detail != null) {
                if (detail.get("formula") != null) row.put("formula", detail.get("formula"));
                if (detail.get("logic") != null) row.put("logic", detail.get("logic"));
                if (detail.get("reason") != null) row.put("reason", detail.get("reason"));
                if (detail.get("risk") != null) row.put("risk", detail.get("risk"));
                // factor_metrics.csv 本身已经带 llm_direction / empirical_direction，但 validated.json 也带；
                // 后者一般更"权威"（直接来自 LLM 元数据），优先用前者，缺失时 fallback
                if (row.get("llm_direction") == null && detail.get("llm_direction") != null) {
                    row.put("llm_direction", detail.get("llm_direction"));
                }
                if (row.get("empirical_direction") == null && detail.get("empirical_direction") != null) {
                    row.put("empirical_direction", detail.get("empirical_direction"));
                }
            }
        }
        // 按 total_score 降序排
        rows.sort((a, b) -> Double.compare(toDouble(b.get("total_score")), toDouble(a.get("total_score"))));
        result.put("factors", rows);

        // 3. OOS Top3 强调卡
        Map<String, Object> top3Json = readJsonObject(outputs.resolve("backtest").resolve("top3_factors.json"));
        Object top3 = top3Json.get("top3");
        result.put("top3", top3 instanceof List<?> ? top3 : Collections.emptyList());

        // 4. 测试区间（直接从 staging/config/analysis_rule.yaml 副本中取，避免依赖 health/market_context.json）
        Map<String, Object> period = new LinkedHashMap<>();
        Map<String, Object> analysisYaml = readYaml(stagingRoot.resolve("config").resolve("analysis_rule.yaml"));
        period.put("test_start_date", analysisYaml.get("test_start_date"));
        period.put("test_end_date", analysisYaml.get("test_end_date"));
        result.put("period", period);

        // 5. 输入因子数（即使 step11 还在跑、还没写出 final_score.csv，至少能告诉用户"输入了 N 个因子"）
        result.put("input_factor_count", validatedByName.size());

        // 6. OOS 输入因子清单（factors_validated.json 的原始内容，按 factor_name 排序）
        //    即使所有因子在 OOS 区间都没通过回测，这份清单也应该展示，让用户知道送了哪些因子去测
        List<Map<String, Object>> inputFactors = new ArrayList<>(validatedByName.values());
        result.put("input_factors", inputFactors);

        // 7. 后续步骤前置数据就绪情况（供前端按钮 enable/disable 与提示）
        Map<String, Object> readiness = new LinkedHashMap<>();
        // step12 依赖 outputs/backtest/top3_factors.json 中 top3 数组非空
        boolean alphalensReportReady = top3 instanceof List<?> && !((List<?>) top3).isEmpty();
        readiness.put("alphalens_report", alphalensReportReady);
        // step13 依赖 step12 的输出目录 outputs/alphalens/ 下存在子目录（每个 Top3 因子一个目录）
        Path alphalensDir = outputs.resolve("alphalens");
        boolean alphalensDashboardReady = Files.isDirectory(alphalensDir) && hasChildDirectory(alphalensDir);
        readiness.put("alphalens_dashboard", alphalensDashboardReady);
        // step14 与 step12 相同的前置约束（需要 top3 或 factors_validated 中的因子）
        readiness.put("joinquant_export", alphalensReportReady || !inputFactors.isEmpty());
        result.put("readiness", readiness);

        return result;
    }

    /** 判断目录下是否至少有一个子目录（用于检测 step12 是否真正产出了 Top3 子文件夹）。 */
    private boolean hasChildDirectory(Path dir) {
        try (var stream = Files.list(dir)) {
            return stream.anyMatch(Files::isDirectory);
        } catch (IOException e) {
            return false;
        }
    }

    // ===== 工具 =====

    private Map<String, Map<String, Object>> readValidatedByName(Path file) {
        Map<String, Object> root = readJsonObject(file);
        Object list = root.get("factors");
        if (!(list instanceof List<?>)) {
            return Collections.emptyMap();
        }
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object item : (List<?>) list) {
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

    private Map<String, Object> readJsonObject(Path file) {
        if (!Files.exists(file)) return Collections.emptyMap();
        try {
            JsonNode node = objectMapper.readTree(file.toFile());
            if (node == null || !node.isObject()) return Collections.emptyMap();
            return objectMapper.convertValue(node, new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            log.warn("读取 JSON 失败: {}", file, e);
            return Collections.emptyMap();
        }
    }

    /** 读取 YAML 文件为 Map（顶层必须是 mapping）。 */
    private Map<String, Object> readYaml(Path file) {
        if (!Files.exists(file)) return Collections.emptyMap();
        try {
            ObjectMapper yamlMapper = new ObjectMapper(new YAMLFactory());
            return yamlMapper.readValue(file.toFile(), new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            log.warn("读取 YAML 失败: {}", file, e);
            return Collections.emptyMap();
        }
    }

    private List<Map<String, Object>> readCsv(Path file) {
        try {
            List<String> lines = Files.readAllLines(file);
            if (lines.size() < 2) return new ArrayList<>();
            String[] headers = splitCsvLine(lines.get(0));
            List<Map<String, Object>> rows = new ArrayList<>(lines.size() - 1);
            for (int i = 1; i < lines.size(); i++) {
                String line = lines.get(i);
                if (line.isEmpty()) continue;
                String[] cells = splitCsvLine(line);
                Map<String, Object> row = new LinkedHashMap<>();
                for (int j = 0; j < headers.length; j++) {
                    row.put(headers[j], parseCell(j < cells.length ? cells[j] : ""));
                }
                rows.add(row);
            }
            return rows;
        } catch (IOException e) {
            log.warn("读取 CSV 失败: {}", file, e);
            return new ArrayList<>();
        }
    }

    private String[] splitCsvLine(String line) {
        List<String> tokens = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        boolean inQuotes = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (c == '"') inQuotes = !inQuotes;
            else if (c == ',' && !inQuotes) {
                tokens.add(current.toString());
                current.setLength(0);
            } else current.append(c);
        }
        tokens.add(current.toString());
        return tokens.toArray(new String[0]);
    }

    private Object parseCell(String value) {
        if (value == null || value.isEmpty() || "nan".equalsIgnoreCase(value)) return null;
        boolean looksNumeric = true;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (!(Character.isDigit(c) || c == '.' || c == '-' || c == '+' || c == 'e' || c == 'E')) {
                looksNumeric = false;
                break;
            }
        }
        if (!looksNumeric) return value;
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
