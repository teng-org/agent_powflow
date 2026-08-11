package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.product.AssetInUseException;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.LinkedHashMap;
import java.util.Map;

@RestControllerAdvice
public class WorkspaceAssetExceptionHandler {
    @ExceptionHandler(AssetInUseException.class)
    public ResponseEntity<Map<String, Object>> assetInUse(
            AssetInUseException exception,
            HttpServletRequest request
    ) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "error");
        body.put("code", "asset_in_use");
        body.put("error_code", "ASSET_IN_USE");
        body.put("message", exception.getMessage());
        body.put("path", request.getRequestURI());
        return ResponseEntity.status(HttpStatus.CONFLICT).body(body);
    }
}
