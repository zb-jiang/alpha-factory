package com.factorfactory.webapp.repository;

import com.factorfactory.webapp.entity.UserConfig;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface UserConfigRepository extends JpaRepository<UserConfig, Long> {
    List<UserConfig> findByUserId(Long userId);
    Optional<UserConfig> findByUserIdAndSection(Long userId, String section);
    void deleteByUserIdAndSection(Long userId, String section);
}
