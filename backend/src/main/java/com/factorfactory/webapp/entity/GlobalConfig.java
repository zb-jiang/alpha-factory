package com.factorfactory.webapp.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 全局配置表
 * 存储所有用户共享的系统级配置（如 LLM 供应商列表）
 */
@Entity
@Table(name = "global_config")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class GlobalConfig {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 配置分区名（如 llm_providers） */
    @Column(nullable = false, unique = true, length = 64)
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
