package com.agentpowflow.controlplane.web;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@RestControllerAdvice(assignableTypes = PublishedDeploymentController.class)
public class PublishedApiExceptionHandler {
    private static final Logger LOG = LoggerFactory.getLogger(PublishedApiExceptionHandler.class);
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, Object>> status(ResponseStatusException error, HttpServletRequest request) {
        HttpStatus status = HttpStatus.resolve(error.getStatusCode().value());
        if (status == null) status = HttpStatus.INTERNAL_SERVER_ERROR;
        return response(status, safeMessage(status, error.getReason()), request);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, Object>> validation(MethodArgumentNotValidException error, HttpServletRequest request) {
        return response(HttpStatus.BAD_REQUEST, "request body is invalid", request);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> unexpected(Exception error, HttpServletRequest request) {
        LOG.error("Published API request failed: method={} path={}", request.getMethod(), request.getRequestURI(), error);
        return response(HttpStatus.INTERNAL_SERVER_ERROR, "service temporarily unavailable", request);
    }

    private ResponseEntity<Map<String, Object>> response(HttpStatus status, String message, HttpServletRequest request) {
        String traceId = request.getHeader("X-Trace-Id");
        if (traceId == null || traceId.isBlank() || traceId.length() > 128) traceId = UUID.randomUUID().toString();
        return ResponseEntity.status(status).body(Map.of(
                "status", "error",
                "code", status.name().toLowerCase(Locale.ROOT),
                "message", message,
                "trace_id", traceId
        ));
    }

    private String safeMessage(HttpStatus status, String reason) {
        if (status.is5xxServerError()) return "service temporarily unavailable";
        return reason == null || reason.isBlank() ? "request rejected" : reason;
    }
}
