package com.factorfactory.jq2qmt.controller;

import com.factorfactory.jq2qmt.common.ApiResponse;
import com.factorfactory.jq2qmt.model.StreamInfo;
import com.factorfactory.jq2qmt.service.MonitorService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/monitor")
public class MonitorController {

    private static final Logger log = LoggerFactory.getLogger(MonitorController.class);

    private final MonitorService monitorService;

    public MonitorController(MonitorService monitorService) {
        this.monitorService = monitorService;
    }

    @GetMapping("/dashboard")
    public ResponseEntity<ApiResponse<Map<String, Object>>> getDashboard() {
        Map<String, Object> summary = monitorService.getDashboardSummary();
        return ResponseEntity.ok(ApiResponse.success(summary));
    }

    @GetMapping("/streams")
    public ResponseEntity<ApiResponse<List<StreamInfo>>> getAllStreams() {
        List<StreamInfo> streams = monitorService.getAllStreamInfo();
        return ResponseEntity.ok(ApiResponse.success(streams));
    }

    @GetMapping("/signals/{strategy}")
    public ResponseEntity<ApiResponse<List<Map<String, String>>>> getSignalHistory(
            @PathVariable String strategy,
            @RequestParam(defaultValue = "50") int count) {
        List<Map<String, String>> signals = monitorService.getSignalHistory(strategy, count);
        return ResponseEntity.ok(ApiResponse.success(signals));
    }

    @GetMapping("/results/{strategy}")
    public ResponseEntity<ApiResponse<List<Map<String, String>>>> getResultHistory(
            @PathVariable String strategy,
            @RequestParam(defaultValue = "50") int count) {
        List<Map<String, String>> results = monitorService.getResultHistory(strategy, count);
        return ResponseEntity.ok(ApiResponse.success(results));
    }

    @GetMapping("/pending/{strategy}")
    public ResponseEntity<ApiResponse<List<Map<String, String>>>> getPendingSignals(
            @PathVariable String strategy) {
        List<Map<String, String>> pending = monitorService.getPendingSignals(strategy);
        return ResponseEntity.ok(ApiResponse.success(pending));
    }
}
