package com.factorfactory.webapp.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(name = "task")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class Task {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private Long userId;

    @Column(nullable = false, length = 128)
    private String taskName;

    @Column(length = 512)
    private String taskDesc;

    @Column(nullable = false, length = 512)
    private String stagingPath;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    @Builder.Default
    private TaskStatus status = TaskStatus.NEW;

    @Column(length = 16)
    private String currentStep;

    private Long pid;

    @Column(nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(nullable = false)
    private LocalDateTime updatedAt;

    public enum TaskStatus {
        /** 新建：刚创建未启动过 */
        NEW,
        /** 运行中：当前有 step 在执行 */
        RUNNING,
        /** 因子挖掘完成：step10 成功跑完 */
        TRAINING_FINISHED,
        /** 样本外回测完成：step11 成功跑完 */
        TESTING_FINISHED
    }

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
        updatedAt = LocalDateTime.now();
    }

    @PreUpdate
    protected void onUpdate() {
        updatedAt = LocalDateTime.now();
    }
}
