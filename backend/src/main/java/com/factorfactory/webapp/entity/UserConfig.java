package com.factorfactory.webapp.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 用户级配置表
 * 存储用户个人凭证和偏好（如 Tushare Key、LLM API Key）
 */
@Entity
@Table(name = "user_config", uniqueConstraints = {
        @UniqueConstraint(columnNames = {"user_id", "section"})
})
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class UserConfig {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private Long userId;

    /** 配置分区名（如 tushare_credentials, llm_credentials） */
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
