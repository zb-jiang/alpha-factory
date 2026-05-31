package com.factorfactory.jq2qmt.controller;

import com.factorfactory.jq2qmt.common.ApiResponse;
import com.factorfactory.jq2qmt.model.ExecutionResult;
import com.factorfactory.jq2qmt.service.SignalService;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/v1/results")
public class ResultController {

    private static final Logger log = LoggerFactory.getLogger(ResultController.class);

    private final SignalService signalService;

    public ResultController(SignalService signalService) {
        this.signalService = signalService;
    }

    @PostMapping("/report")
    public ResponseEntity<ApiResponse<Map<String, String>>> reportResult(@Valid @RequestBody ExecutionResult result) {
        try {
            String recordId = signalService.reportResult(result);
            return ResponseEntity.ok(ApiResponse.success("Result reported", Map.of(
                    "recordId", recordId,
                    "signalId", result.getSignalId()
            )));
        } catch (Exception e) {
            log.error("Failed to report result", e);
            return ResponseEntity.internalServerError().body(ApiResponse.error(e.getMessage()));
        }
    }
}
