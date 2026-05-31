package com.factorfactory.webapp.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "execution")
public class ExecutionProperties {
    private int maxConcurrentTasks = 4;
    private String pythonExecutable = "python";
    private String srcDir;
}
