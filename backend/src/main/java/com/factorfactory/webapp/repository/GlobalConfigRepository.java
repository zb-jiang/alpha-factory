package com.factorfactory.webapp.repository;

import com.factorfactory.webapp.entity.GlobalConfig;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface GlobalConfigRepository extends JpaRepository<GlobalConfig, Long> {
    Optional<GlobalConfig> findBySection(String section);
    void deleteBySection(String section);
}
