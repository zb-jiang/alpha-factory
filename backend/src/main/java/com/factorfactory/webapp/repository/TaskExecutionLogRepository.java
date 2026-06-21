package com.factorfactory.webapp.repository;

import com.factorfactory.webapp.entity.TaskExecutionLog;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface TaskExecutionLogRepository extends JpaRepository<TaskExecutionLog, Long> {
    List<TaskExecutionLog> findByTaskIdOrderByStartTimeDesc(Long taskId);
    Optional<TaskExecutionLog> findTopByTaskIdOrderByStartTimeDesc(Long taskId);
    void deleteByTaskId(Long taskId);
}
