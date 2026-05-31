package com.factorfactory.webapp.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "staging")
public class StagingProperties {
    private String rootDir = "D:/staging";
    private String configTemplateDir;
}
