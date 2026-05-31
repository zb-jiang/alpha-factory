package com.factorfactory.webapp.controller;

import com.factorfactory.webapp.dto.*;
import com.factorfactory.webapp.entity.User;
import com.factorfactory.webapp.service.AuthService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/v1/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;

    @PostMapping("/login")
    public ApiResponse<LoginResponse> login(@Valid @RequestBody LoginRequest request) {
        return ApiResponse.ok(authService.login(request));
    }

    @PostMapping("/register")
    public ApiResponse<Void> register(@RequestBody Map<String, String> body) {
        String username = body.get("username");
        String password = body.get("password");
        if (username == null || username.isBlank() || password == null || password.isBlank()) {
            return ApiResponse.error(400, "用户名和密码不能为空");
        }
        authService.register(username, password);
        return ApiResponse.ok();
    }

    @GetMapping("/me")
    public ApiResponse<User> me(Authentication authentication) {
        Long userId = (Long) authentication.getPrincipal();
        return ApiResponse.ok(authService.getCurrentUser(userId));
    }
}
