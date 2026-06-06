package com.factorfactory.webapp.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

@Data
public class ScheduledTaskRequest {

    @NotBlank
    private String name;

    private String description;

    @NotNull
    private Long sourceTaskId;

    @NotBlank
    private String cronExpression;

    private boolean enabled = true;
}
