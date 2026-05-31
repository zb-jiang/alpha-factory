package com.factorfactory.jq2qmt.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.connection.stream.ReadOffset;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.serializer.StringRedisSerializer;

@Configuration
public class RedisConfig {

    private static final Logger log = LoggerFactory.getLogger(RedisConfig.class);

    private final AppProperties appProperties;

    public RedisConfig(AppProperties appProperties) {
        this.appProperties = appProperties;
    }

    @Bean
    public RedisTemplate<String, String> redisTemplate(RedisConnectionFactory connectionFactory) {
        RedisTemplate<String, String> template = new RedisTemplate<>();
        template.setConnectionFactory(connectionFactory);
        template.setKeySerializer(new StringRedisSerializer());
        template.setValueSerializer(new StringRedisSerializer());
        template.setHashKeySerializer(new StringRedisSerializer());
        template.setHashValueSerializer(new StringRedisSerializer());
        template.afterPropertiesSet();
        return template;
    }

    @Bean
    public StreamInitializer streamInitializer(RedisConnectionFactory connectionFactory) {
        return new StreamInitializer(connectionFactory, appProperties);
    }

    public static class StreamInitializer {

        private static final Logger initLog = LoggerFactory.getLogger(StreamInitializer.class);

        private final RedisConnectionFactory connectionFactory;
        private final AppProperties properties;

        public StreamInitializer(RedisConnectionFactory connectionFactory, AppProperties properties) {
            this.connectionFactory = connectionFactory;
            this.properties = properties;
            init();
        }

        private void init() {
            try {
                var connection = connectionFactory.getConnection();
                String group = properties.getStream().getConsumerGroup();

                createConsumerGroupIfNotExists(properties.getStream().signalStreamKey("_init"), group);
                createConsumerGroupIfNotExists(properties.getStream().resultStreamKey("_init"), group);

                initLog.info("Redis Stream consumer groups initialized. Group: {}", group);
            } catch (Exception e) {
                initLog.warn("Failed to initialize Redis Stream consumer groups (will retry on first use): {}", e.getMessage());
            }
        }

        private void createConsumerGroupIfNotExists(String streamKey, String group) {
            try {
                var connection = connectionFactory.getConnection();
                connection.xGroupCreate(streamKey.getBytes(), group, ReadOffset.from("0"), true);
                initLog.info("Created consumer group '{}' for stream '{}'", group, streamKey);
            } catch (Exception e) {
                String msg = e.getMessage();
                if (msg != null && (msg.contains("BUSYGROUP") || msg.contains("already exists"))) {
                    initLog.debug("Consumer group '{}' already exists for stream '{}'", group, streamKey);
                } else {
                    initLog.warn("Could not create consumer group for '{}': {}", streamKey, msg);
                }
            }
        }
    }
}
