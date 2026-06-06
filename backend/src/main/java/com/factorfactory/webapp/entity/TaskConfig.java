package com.factorfactory.webapp.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 任务级配置表
 * 存储每个任务的独立配置，任务启动时从三层配置 merge 生成 staging config copy
 */
@Entity
@Table(name = "task_config", uniqueConstraints = {
        @UniqueConstraint(columnNames = {"task_id", "section"})
})
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class TaskConfig {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private Long taskId;

    /** 配置分区名（如 analysis_rule, backtest_rule, llm_params 等） */
    @Column(nullable = false, length = 64)
    private String section;

    /** 配置内容（JSON格式） */
    @Column(nullable = false, columnDefinition = "TEXT")
    private String value;

    @Column(nullable = false)
    private LocalDateTime updatedAt;

    @PrePersist
    @PreUpdate
    protected void onUpdate() {
        updatedAt = LocalDateTime.now();
    }
}
