package com.factorfactory.jq2qmt.controller;

import com.factorfactory.jq2qmt.common.ApiResponse;
import com.factorfactory.jq2qmt.model.TradeSignal;
import com.factorfactory.jq2qmt.service.SignalService;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.Duration;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/signals")
public class SignalController {

    private static final Logger log = LoggerFactory.getLogger(SignalController.class);

    private final SignalService signalService;

    public SignalController(SignalService signalService) {
        this.signalService = signalService;
    }

    @PostMapping("/send")
    public ResponseEntity<ApiResponse<Map<String, String>>> sendSignal(@Valid @RequestBody TradeSignal signal) {
        try {
            String recordId = signalService.sendSignal(signal);
            return ResponseEntity.ok(ApiResponse.success("Signal sent", Map.of(
                    "recordId", recordId,
                    "signalId", signal.getSignalId()
            )));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(ApiResponse.badRequest(e.getMessage()));
        } catch (Exception e) {
            log.error("Failed to send signal", e);
            return ResponseEntity.internalServerError().body(ApiResponse.error(e.getMessage()));
        }
    }

    @PostMapping("/batch")
    public ResponseEntity<ApiResponse<List<String>>> sendSignals(@Valid @RequestBody List<TradeSignal> signals) {
        try {
            List<String> recordIds = signalService.sendSignals(signals);
            return ResponseEntity.ok(ApiResponse.success("Batch signals sent", recordIds));
        } catch (Exception e) {
            log.error("Failed to send batch signals", e);
            return ResponseEntity.internalServerError().body(ApiResponse.error(e.getMessage()));
        }
    }

    @GetMapping("/consume")
    public ResponseEntity<ApiResponse<List<Map<String, String>>>> consumeSignals(
            @RequestParam String strategy,
            @RequestParam(defaultValue = "worker-1") String consumer,
            @RequestParam(defaultValue = "10") int count) {
        try {
            List<Map<String, String>> signals = signalService.consumeSignals(strategy, consumer, count);
            return ResponseEntity.ok(ApiResponse.success(signals));
        } catch (Exception e) {
            log.error("Failed to consume signals", e);
            return ResponseEntity.internalServerError().body(ApiResponse.error(e.getMessage()));
        }
    }

    @GetMapping("/consume/blocking")
    public ResponseEntity<ApiResponse<List<Map<String, String>>>> consumeSignalsBlocking(
            @RequestParam String strategy,
            @RequestParam(defaultValue = "worker-1") String consumer,
            @RequestParam(defaultValue = "1") int count,
            @RequestParam(defaultValue = "5") int timeoutSeconds) {
        try {
            List<Map<String, String>> signals = signalService.consumeSignalsBlocking(
                    strategy, consumer, count, Duration.ofSeconds(timeoutSeconds));
            return ResponseEntity.ok(ApiResponse.success(signals));
        } catch (Exception e) {
            log.error("Failed to consume signals (blocking)", e);
            return ResponseEntity.internalServerError().body(ApiResponse.error(e.getMessage()));
        }
    }

    @PostMapping("/ack")
    public ResponseEntity<ApiResponse<String>> ackSignal(
            @RequestParam String strategy,
            @RequestParam String recordId) {
        try {
            signalService.ackSignal(strategy, recordId);
            return ResponseEntity.ok(ApiResponse.success("Signal acknowledged", recordId));
        } catch (Exception e) {
            log.error("Failed to ack signal", e);
            return ResponseEntity.internalServerError().body(ApiResponse.error(e.getMessage()));
        }
    }
}
