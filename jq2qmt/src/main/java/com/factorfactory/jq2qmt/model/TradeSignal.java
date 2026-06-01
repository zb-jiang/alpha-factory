package com.factorfactory.jq2qmt.model;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.PositiveOrZero;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.UUID;

public class TradeSignal implements Serializable {

    @NotBlank(message = "action is required")
    private String action;

    @NotBlank(message = "code is required")
    private String code;

    @NotNull(message = "pct is required")
    @PositiveOrZero(message = "pct must be positive or zero")
    private Double pct;

    private Double price;

    @NotBlank(message = "strategy is required")
    private String strategy;

    private String signalTime;

    private String signalId;

    public TradeSignal() {
        this.signalId = UUID.randomUUID().toString();
        this.signalTime = LocalDateTime.now().toString();
        this.price = 0.0;
    }

    public void generateId() {
        if (this.signalId == null || this.signalId.isEmpty()) {
            this.signalId = UUID.randomUUID().toString();
        }
    }

    public void fillSignalTimeIfEmpty() {
        if (this.signalTime == null || this.signalTime.isEmpty()) {
            this.signalTime = LocalDateTime.now().toString();
        }
    }

    public String getAction() { return action; }
    public void setAction(String action) { this.action = action; }

    public String getCode() { return code; }
    public void setCode(String code) { this.code = code; }

    public Double getPct() { return pct; }
    public void setPct(Double pct) { this.pct = pct; }

    public Double getPrice() { return price; }
    public void setPrice(Double price) { this.price = price; }

    public String getStrategy() { return strategy; }
    public void setStrategy(String strategy) { this.strategy = strategy; }

    public String getSignalTime() { return signalTime; }
    public void setSignalTime(String signalTime) { this.signalTime = signalTime; }

    public String getSignalId() { return signalId; }
    public void setSignalId(String signalId) { this.signalId = signalId; }

    @Override
    public String toString() {
        return "TradeSignal{" +
                "action='" + action + '\'' +
                ", code='" + code + '\'' +
                ", pct=" + pct +
                ", price=" + price +
                ", strategy='" + strategy + '\'' +
                ", signalTime='" + signalTime + '\'' +
                ", signalId='" + signalId + '\'' +
                '}';
    }
}
