package com.factorfactory.webapp.repository;

import com.factorfactory.webapp.entity.ScheduledTask;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface ScheduledTaskRepository extends JpaRepository<ScheduledTask, Long> {

    List<ScheduledTask> findByUserIdOrderByCreatedAtDesc(Long userId);

    List<ScheduledTask> findByEnabledTrue();
}
