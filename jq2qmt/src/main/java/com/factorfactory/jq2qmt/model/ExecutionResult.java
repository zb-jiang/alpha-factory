package com.factorfactory.jq2qmt.model;

import jakarta.validation.constraints.NotBlank;

import java.io.Serializable;
import java.time.LocalDateTime;

public class ExecutionResult implements Serializable {

    @NotBlank(message = "signalId is required")
    private String signalId;

    @NotBlank(message = "status is required")
    private String status;

    private Long orderId;

    private Double filledPrice;

    private Long filledVolume;

    private Double filledAmount;

    private String executeTime;

    private String remark;

    private String strategy;

    public ExecutionResult() {
        this.executeTime = LocalDateTime.now().toString();
    }

    public String getSignalId() { return signalId; }
    public void setSignalId(String signalId) { this.signalId = signalId; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public Long getOrderId() { return orderId; }
    public void setOrderId(Long orderId) { this.orderId = orderId; }

    public Double getFilledPrice() { return filledPrice; }
    public void setFilledPrice(Double filledPrice) { this.filledPrice = filledPrice; }

    public Long getFilledVolume() { return filledVolume; }
    public void setFilledVolume(Long filledVolume) { this.filledVolume = filledVolume; }

    public Double getFilledAmount() { return filledAmount; }
    public void setFilledAmount(Double filledAmount) { this.filledAmount = filledAmount; }

    public String getExecuteTime() { return executeTime; }
    public void setExecuteTime(String executeTime) { this.executeTime = executeTime; }

    public String getRemark() { return remark; }
    public void setRemark(String remark) { this.remark = remark; }

    public String getStrategy() { return strategy; }
    public void setStrategy(String strategy) { this.strategy = strategy; }

    @Override
    public String toString() {
        return "ExecutionResult{" +
                "signalId='" + signalId + '\'' +
                ", status='" + status + '\'' +
                ", orderId=" + orderId +
                ", filledPrice=" + filledPrice +
                ", filledVolume=" + filledVolume +
                ", filledAmount=" + filledAmount +
                ", executeTime='" + executeTime + '\'' +
                ", remark='" + remark + '\'' +
                ", strategy='" + strategy + '\'' +
                '}';
    }
}
