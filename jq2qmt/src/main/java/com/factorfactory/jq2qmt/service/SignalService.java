package com.factorfactory.jq2qmt.service;

import com.factorfactory.jq2qmt.config.AppProperties;
import com.factorfactory.jq2qmt.model.ExecutionResult;
import com.factorfactory.jq2qmt.model.TradeSignal;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

@Service
public class SignalService {

    private static final Logger log = LoggerFactory.getLogger(SignalService.class);
    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final RedisStreamService redisStreamService;
    private final AppProperties properties;

    public SignalService(RedisStreamService redisStreamService, AppProperties properties) {
        this.redisStreamService = redisStreamService;
        this.properties = properties;
    }

    public String sendSignal(TradeSignal signal) {
        signal.generateId();
        signal.fillSignalTimeIfEmpty();

        validateSignal(signal);

        String recordId = redisStreamService.addSignal(signal);

        log.info("Signal sent: strategy={}, action={}, code={}, pct={}, signalId={}, recordId={}",
                signal.getStrategy(), signal.getAction(), signal.getCode(),
                signal.getPct(), signal.getSignalId(), recordId);

        return recordId;
    }

    public List<String> sendSignals(List<TradeSignal> signals) {
        return signals.stream().map(this::sendSignal).toList();
    }

    public List<Map<String, String>> consumeSignals(String strategy, String consumerName, int count) {
        return redisStreamService.consumeSignals(strategy, consumerName, count);
    }

    public List<Map<String, String>> consumeSignalsBlocking(String strategy, String consumerName,
                                                             int count, Duration timeout) {
        return redisStreamService.consumeSignalsBlocking(strategy, consumerName, count, timeout);
    }

    public void ackSignal(String strategy, String recordId) {
        redisStreamService.ackSignal(strategy, recordId);
    }

    public String reportResult(ExecutionResult result) {
        return redisStreamService.addResult(result);
    }

    public boolean checkSignalExpiry(String signalTimeStr) {
        if (signalTimeStr == null || signalTimeStr.isEmpty()) {
            return false;
        }
        try {
            LocalDateTime signalTime = LocalDateTime.parse(signalTimeStr, FMT);
            Duration elapsed = Duration.between(signalTime, LocalDateTime.now());
            return elapsed.getSeconds() > properties.getSignal().getExpireSeconds();
        } catch (Exception e) {
            try {
                LocalDateTime signalTime = LocalDateTime.parse(signalTimeStr);
                Duration elapsed = Duration.between(signalTime, LocalDateTime.now());
                return elapsed.getSeconds() > properties.getSignal().getExpireSeconds();
            } catch (Exception ex) {
                log.warn("Cannot parse signal_time: {}", signalTimeStr);
                return false;
            }
        }
    }

    public boolean checkSlippage(double signalPrice, double currentPrice) {
        if (signalPrice <= 0) {
            return true;
        }
        double slippage = Math.abs(currentPrice - signalPrice) / signalPrice;
        return slippage <= properties.getSignal().getMaxSlippagePct();
    }

    private void validateSignal(TradeSignal signal) {
        String action = signal.getAction();
        if (!"BUY".equalsIgnoreCase(action) && !"SELL".equalsIgnoreCase(action) && !"ADJUST".equalsIgnoreCase(action)) {
            throw new IllegalArgumentException("Invalid action: " + action + ". Must be BUY, SELL, or ADJUST");
        }

        String code = signal.getCode();
        if (code == null || code.isEmpty()) {
            throw new IllegalArgumentException("Stock code is required");
        }

        if (!code.matches("\\d{6}\\.(SH|SZ)")) {
            log.warn("Stock code format may be non-standard: {}", code);
        }

        if (signal.getPct() != null && signal.getPct() > 1.0 && "BUY".equalsIgnoreCase(action)) {
            log.warn("Buy pct > 1.0 ({}), this means using more than 100% of available capital", signal.getPct());
        }
    }
}
