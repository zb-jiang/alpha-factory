package com.factorfactory.jq2qmt.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "jq2qmt")
public class AppProperties {

    private Stream stream = new Stream();
    private Signal signal = new Signal();
    private Auth auth = new Auth();
    private Cors cors = new Cors();

    public Stream getStream() { return stream; }
    public void setStream(Stream stream) { this.stream = stream; }

    public Signal getSignal() { return signal; }
    public void setSignal(Signal signal) { this.signal = signal; }

    public Auth getAuth() { return auth; }
    public void setAuth(Auth auth) { this.auth = auth; }

    public Cors getCors() { return cors; }
    public void setCors(Cors cors) { this.cors = cors; }

    public static class Stream {
        private String prefix = "factor_factory:";
        private int maxLength = 1000;
        private String consumerGroup = "qmt_workers";
        private String consumerName = "worker-1";

        public String getPrefix() { return prefix; }
        public void setPrefix(String prefix) { this.prefix = prefix; }

        public int getMaxLength() { return maxLength; }
        public void setMaxLength(int maxLength) { this.maxLength = maxLength; }

        public String getConsumerGroup() { return consumerGroup; }
        public void setConsumerGroup(String consumerGroup) { this.consumerGroup = consumerGroup; }

        public String getConsumerName() { return consumerName; }
        public void setConsumerName(String consumerName) { this.consumerName = consumerName; }

        public String signalStreamKey(String strategy) {
            return prefix + strategy;
        }

        public String resultStreamKey(String strategy) {
            return prefix + strategy + ":result";
        }

        public String deadStreamKey(String strategy) {
            return prefix + strategy + ":dead";
        }
    }

    public static class Signal {
        private int expireSeconds = 300;
        private double maxSlippagePct = 0.02;
        private double maxPositionPct = 0.15;

        public int getExpireSeconds() { return expireSeconds; }
        public void setExpireSeconds(int expireSeconds) { this.expireSeconds = expireSeconds; }

        public double getMaxSlippagePct() { return maxSlippagePct; }
        public void setMaxSlippagePct(double maxSlippagePct) { this.maxSlippagePct = maxSlippagePct; }

        public double getMaxPositionPct() { return maxPositionPct; }
        public void setMaxPositionPct(double maxPositionPct) { this.maxPositionPct = maxPositionPct; }
    }

    public static class Auth {
        private boolean enabled = true;
        private String apiKey = "change-me-in-production";

        public boolean isEnabled() { return enabled; }
        public void setEnabled(boolean enabled) { this.enabled = enabled; }

        public String getApiKey() { return apiKey; }
        public void setApiKey(String apiKey) { this.apiKey = apiKey; }
    }

    public static class Cors {
        private String allowedOrigins = "*";

        public String getAllowedOrigins() { return allowedOrigins; }
        public void setAllowedOrigins(String allowedOrigins) { this.allowedOrigins = allowedOrigins; }
    }
}
