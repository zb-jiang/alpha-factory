package com.factorfactory.webapp.dto;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class ScheduledTaskResponse {

    private Long id;
    private String name;
    private String description;
    private Long sourceTaskId;
    private String sourceTaskName;
    private String cronExpression;
    private boolean enabled;
    private LocalDateTime lastRunAt;
    private LocalDateTime nextRunAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
