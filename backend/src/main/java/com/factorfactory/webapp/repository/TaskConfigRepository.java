package com.factorfactory.webapp.repository;

import com.factorfactory.webapp.entity.TaskConfig;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface TaskConfigRepository extends JpaRepository<TaskConfig, Long> {
    List<TaskConfig> findByTaskId(Long taskId);
    Optional<TaskConfig> findByTaskIdAndSection(Long taskId, String section);
    void deleteByTaskIdAndSection(Long taskId, String section);
}
