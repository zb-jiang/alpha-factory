package com.factorfactory.webapp.service;

import com.factorfactory.webapp.dto.ConfigResponse;
import com.factorfactory.webapp.entity.Task;
import com.factorfactory.webapp.exception.BusinessException;
import com.factorfactory.webapp.config.StagingProperties;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class ConfigService {

    private final TaskService taskService;
    private final StagingProperties stagingProperties;

    private static final List<String> CONFIG_FILES = List.of(
            "env.yaml", "analysis_rule.yaml", "backtest_rule.yaml",
            "feature_pool.yaml", "market_context.yaml", "score.yaml", "selector.yaml"
    );

    private static final Map<String, String> CONFIG_NAME_MAP = Map.of(
            "env", "env.yaml",
            "analysis_rule", "analysis_rule.yaml",
            "backtest_rule", "backtest_rule.yaml",
            "feature_pool", "feature_pool.yaml",
            "market_context", "market_context.yaml",
            "score", "score.yaml",
            "selector", "selector.yaml"
    );

    public List<ConfigResponse> listConfigs(Long userId, Long taskId) {
        Task task = taskService.findTaskBelongsToUser(userId, taskId);
        Path configDir = Paths.get(task.getStagingPath(), "config");

        return CONFIG_FILES.stream()
                .map(fileName -> {
                    Path filePath = configDir.resolve(fileName);
                    String content = "";
                    if (Files.exists(filePath)) {
                        try {
                            content = Files.readString(filePath);
                        } catch (IOException e) {
                            log.warn("Failed to read config file: {}", filePath);
                        }
                    }
                    String name = fileName.replace(".yaml", "");
                    return ConfigResponse.builder().name(name).content(content).build();
                })
                .collect(Collectors.toList());
    }

    public ConfigResponse getConfig(Long userId, Long taskId, String configName) {
        String fileName = resolveFileName(configName);
        Task task = taskService.findTaskBelongsToUser(userId, taskId);
        Path filePath = Paths.get(task.getStagingPath(), "config", fileName);

        if (!Files.exists(filePath)) {
            throw new BusinessException(404, "配置文件不存在: " + fileName);
        }

        try {
            String content = Files.readString(filePath);
            return ConfigResponse.builder().name(configName).content(content).build();
        } catch (IOException e) {
            throw new BusinessException("读取配置文件失败: " + e.getMessage());
        }
    }

    public void updateConfig(Long userId, Long taskId, String configName, String content) {
        String fileName = resolveFileName(configName);
        Task task = taskService.findTaskBelongsToUser(userId, taskId);
        Path filePath = Paths.get(task.getStagingPath(), "config", fileName);

        if (!Files.exists(filePath)) {
            throw new BusinessException(404, "配置文件不存在: " + fileName);
        }

        try {
            Files.writeString(filePath, content);
            log.info("Config updated: task={}, config={}", taskId, configName);
        } catch (IOException e) {
            throw new BusinessException("写入配置文件失败: " + e.getMessage());
        }
    }

    public void resetConfig(Long userId, Long taskId, String configName) {
        String fileName = resolveFileName(configName);
        Task task = taskService.findTaskBelongsToUser(userId, taskId);
        Path templatePath = Paths.get(stagingProperties.getConfigTemplateDir(), fileName);
        Path targetPath = Paths.get(task.getStagingPath(), "config", fileName);

        if (!Files.exists(templatePath)) {
            throw new BusinessException(404, "模板配置文件不存在: " + fileName);
        }

        try {
            Files.copy(templatePath, targetPath, StandardCopyOption.REPLACE_EXISTING);
            log.info("Config reset: task={}, config={}", taskId, configName);
        } catch (IOException e) {
            throw new BusinessException("重置配置文件失败: " + e.getMessage());
        }
    }

    private String resolveFileName(String configName) {
        String fileName = CONFIG_NAME_MAP.get(configName);
        if (fileName == null) {
            throw new BusinessException("无效的配置名称: " + configName + "，可选值: " + CONFIG_NAME_MAP.keySet());
        }
        return fileName;
    }
}
