package com.factorfactory.webapp.controller;

import com.factorfactory.webapp.dto.ApiResponse;
import com.factorfactory.webapp.service.TaskService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.nio.file.*;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/tasks/{taskId}/logs")
@RequiredArgsConstructor
public class LogController {

    private final TaskService taskService;

    @GetMapping
    public ApiResponse<List<String>> getLogs(Authentication authentication,
                                              @PathVariable Long taskId,
                                              @RequestParam(defaultValue = "200") int lines) throws IOException {
        Long userId = (Long) authentication.getPrincipal();
        var task = taskService.findTaskBelongsToUser(userId, taskId);

        Path runtimeDir = Paths.get(task.getStagingPath(), "outputs", "_runtime");
        if (!Files.exists(runtimeDir)) {
            return ApiResponse.ok(List.of());
        }

        List<String> allLines = new ArrayList<>();
        try (var stream = Files.list(runtimeDir)) {
            List<Path> logFiles = stream
                    .filter(p -> p.toString().endsWith(".log"))
                    .sorted()
                    .toList();

            for (Path logFile : logFiles) {
                List<String> fileLines = Files.readAllLines(logFile);
                allLines.addAll(fileLines);
            }
        }

        int start = Math.max(0, allLines.size() - lines);
        return ApiResponse.ok(allLines.subList(start, allLines.size()));
    }
}
