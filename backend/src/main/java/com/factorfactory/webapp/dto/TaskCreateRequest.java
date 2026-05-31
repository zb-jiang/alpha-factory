package com.factorfactory.webapp.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

@Data
public class TaskCreateRequest {
    @NotBlank(message = "任务名称不能为空")
    @Size(max = 128, message = "任务名称最长128字符")
    private String taskName;

    @Size(max = 512, message = "任务描述最长512字符")
    private String taskDesc;
}
