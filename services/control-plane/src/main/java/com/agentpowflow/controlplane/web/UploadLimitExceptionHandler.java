package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.tasks.TaskDispatchProperties;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@RestControllerAdvice
public class UploadLimitExceptionHandler {
    public static final String ERROR_CODE = "UPLOAD_LIMIT_EXCEEDED";

    private final TaskDispatchProperties properties;

    public UploadLimitExceptionHandler(TaskDispatchProperties properties) {
        this.properties = properties;
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ResponseEntity<Map<String, Object>> uploadLimit(
            MaxUploadSizeExceededException ignored,
            HttpServletRequest request
    ) {
        long maxFileBytes = Math.max(1024, properties.getMaxUploadFileBytes());
        long maxBatchBytes = Math.max(maxFileBytes, properties.getMaxUploadBatchBytes());
        int maxFiles = Math.max(1, Math.min(properties.getMaxUploadFiles(), 200));
        String message = "上传内容超过限制：单个文件最大 " + displaySize(maxFileBytes)
                + "，单次最多 " + maxFiles + " 个文件、总计 " + displaySize(maxBatchBytes) + "。";

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "error");
        body.put("code", ERROR_CODE.toLowerCase(Locale.ROOT));
        body.put("error_code", ERROR_CODE);
        body.put("message", message);
        body.put("max_files_per_request", maxFiles);
        body.put("max_file_size_bytes", maxFileBytes);
        body.put("max_batch_size_bytes", maxBatchBytes);
        body.put("trace_id", traceId(request));
        return ResponseEntity.status(HttpStatus.PAYLOAD_TOO_LARGE).body(body);
    }

    @ExceptionHandler(MissingServletRequestPartException.class)
    public ResponseEntity<Map<String, Object>> missingPart(
            MissingServletRequestPartException exception,
            HttpServletRequest request
    ) {
        boolean missingFile = "file".equals(exception.getRequestPartName());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "error");
        body.put("code", missingFile ? "upload_file_required" : "request_part_required");
        body.put("error_code", missingFile ? "UPLOAD_FILE_REQUIRED" : "REQUEST_PART_REQUIRED");
        body.put("message", missingFile
                ? "没有收到待上传文件，请重新选择文件后提交。"
                : "请求缺少必要内容，请刷新页面后重试。");
        body.put("trace_id", traceId(request));
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
    }

    private static String traceId(HttpServletRequest request) {
        String value = request.getHeader("X-Trace-Id");
        if (value == null || value.isBlank() || value.length() > 128) return UUID.randomUUID().toString();
        return value.trim();
    }

    private static String displaySize(long bytes) {
        long mib = 1024L * 1024L;
        if (bytes % mib == 0) return (bytes / mib) + " MB";
        return String.format(Locale.ROOT, "%.1f MB", bytes / (double) mib);
    }
}
