package com.factorfactory.webapp.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class TaskStartRequest {
    @NotBlank(message = "Step不能为空")
    private String step;
}
