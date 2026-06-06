package com.factorfactory.webapp.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

@Data
@Component
@ConfigurationProperties(prefix = "llm")
public class LlmProperties {

    private List<Provider> defaultProviders = new ArrayList<>();

    @Data
    public static class Provider {
        private String name;
        private String baseUrl;
        private String description;
    }
}
