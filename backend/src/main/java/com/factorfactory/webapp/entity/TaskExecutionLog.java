package com.factorfactory.webapp.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(name = "task_execution_log")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class TaskExecutionLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private Long taskId;

    @Column(nullable = false, length = 16)
    private String step;

    @Column(nullable = false)
    private LocalDateTime startTime;

    private LocalDateTime endTime;

    private Integer exitCode;

    @Column(length = 512)
    private String logFilePath;

    @Column(length = 1024)
    private String errorMessage;
}
