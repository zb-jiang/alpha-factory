package com.factorfactory.jq2qmt.controller;

import com.factorfactory.jq2qmt.common.ApiResponse;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/v1/health")
public class HealthController {

    private final RedisConnectionFactory connectionFactory;

    public HealthController(RedisConnectionFactory connectionFactory) {
        this.connectionFactory = connectionFactory;
    }

    @GetMapping
    public ResponseEntity<ApiResponse<Map<String, Object>>> health() {
        boolean redisOk = false;
        String redisError = null;
        try {
            var connection = connectionFactory.getConnection();
            var pong = connection.ping();
            redisOk = "PONG".equalsIgnoreCase(pong);
        } catch (Exception e) {
            redisError = e.getMessage();
        }

        Map<String, Object> status = Map.of(
                "status", redisOk ? "UP" : "DOWN",
                "redis", redisOk ? "connected" : "disconnected",
                "redisError", redisError != null ? redisError : "none"
        );

        if (redisOk) {
            return ResponseEntity.ok(ApiResponse.success(status));
        } else {
            return ResponseEntity.status(503).body(ApiResponse.error(503, "Service unavailable: Redis not connected"));
        }
    }
}
