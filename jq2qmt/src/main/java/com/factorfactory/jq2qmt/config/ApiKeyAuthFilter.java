package com.factorfactory.jq2qmt.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Component
public class ApiKeyAuthFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(ApiKeyAuthFilter.class);

    private final AppProperties appProperties;

    public ApiKeyAuthFilter(AppProperties appProperties) {
        this.appProperties = appProperties;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {

        if (!appProperties.getAuth().isEnabled()) {
            filterChain.doFilter(request, response);
            return;
        }

        String path = request.getRequestURI();
        if (path.startsWith("/api/") || path.startsWith("/actuator/")) {
            String apiKey = request.getHeader("X-API-Key");
            if (apiKey == null) {
                apiKey = request.getParameter("apiKey");
            }

            if (apiKey == null || !apiKey.equals(appProperties.getAuth().getApiKey())) {
                log.warn("Unauthorized API access from {}: {}", request.getRemoteAddr(), path);
                response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
                response.setContentType("application/json;charset=UTF-8");
                response.getWriter().write("{\"code\":401,\"message\":\"Unauthorized: invalid or missing API key\"}");
                return;
            }
        }

        filterChain.doFilter(request, response);
    }
}
