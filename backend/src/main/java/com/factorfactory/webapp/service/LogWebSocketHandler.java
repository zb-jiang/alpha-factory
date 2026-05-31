package com.factorfactory.webapp.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.io.IOException;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Slf4j
@Component
public class LogWebSocketHandler extends TextWebSocketHandler {

    private final Map<Long, Set<WebSocketSession>> taskSessions = new ConcurrentHashMap<>();

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        Long taskId = extractTaskId(session);
        if (taskId != null) {
            taskSessions.computeIfAbsent(taskId, k -> ConcurrentHashMap.newKeySet()).add(session);
            log.debug("WebSocket connected for task: {}", taskId);
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) throws Exception {
        Long taskId = extractTaskId(session);
        if (taskId != null) {
            Set<WebSocketSession> sessions = taskSessions.get(taskId);
            if (sessions != null) {
                sessions.remove(session);
                if (sessions.isEmpty()) {
                    taskSessions.remove(taskId);
                }
            }
        }
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
    }

    public void broadcastLog(Long taskId, String line) {
        Set<WebSocketSession> sessions = taskSessions.get(taskId);
        if (sessions == null || sessions.isEmpty()) {
            return;
        }

        TextMessage msg = new TextMessage(line);
        List<WebSocketSession> toRemove = new ArrayList<>();

        for (WebSocketSession session : sessions) {
            if (session.isOpen()) {
                try {
                    session.sendMessage(msg);
                } catch (IOException e) {
                    toRemove.add(session);
                }
            } else {
                toRemove.add(session);
            }
        }

        toRemove.forEach(sessions::remove);
    }

    private Long extractTaskId(WebSocketSession session) {
        String uri = Objects.toString(session.getUri(), "");
        String[] parts = uri.split("/");
        for (int i = 0; i < parts.length - 1; i++) {
            if ("tasks".equals(parts[i])) {
                try {
                    return Long.parseLong(parts[i + 1]);
                } catch (NumberFormatException e) {
                    return null;
                }
            }
        }
        return null;
    }
}
