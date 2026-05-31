package com.factorfactory.webapp.dto;

import com.factorfactory.webapp.entity.Task;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class TaskResponse {
    private Long id;
    private String taskName;
    private String taskDesc;
    private String stagingPath;
    private Task.TaskStatus status;
    private String currentStep;
    private Long pid;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
