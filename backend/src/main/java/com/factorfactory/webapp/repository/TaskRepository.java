package com.factorfactory.webapp.repository;

import com.factorfactory.webapp.entity.Task;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface TaskRepository extends JpaRepository<Task, Long> {
    List<Task> findByUserIdOrderByCreatedAtDesc(Long userId);
    boolean existsByUserIdAndTaskName(Long userId, String taskName);
    long countByStatus(Task.TaskStatus status);
}
