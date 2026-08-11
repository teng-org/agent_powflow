package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.tasks.TaskDispatchProperties;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.multipart.MaxUploadSizeExceededException;

import static org.assertj.core.api.Assertions.assertThat;

class UploadLimitExceptionHandlerTest {
    @Test
    void returnsStructuredProductErrorWithConfiguredLimits() {
        TaskDispatchProperties properties = new TaskDispatchProperties();
        properties.setMaxUploadFiles(12);
        properties.setMaxUploadFileBytes(50L * 1024 * 1024);
        properties.setMaxUploadBatchBytes(180L * 1024 * 1024);
        UploadLimitExceptionHandler handler = new UploadLimitExceptionHandler(properties);
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Trace-Id", "upload-trace-1");

        var response = handler.uploadLimit(new MaxUploadSizeExceededException(50L * 1024 * 1024), request);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.PAYLOAD_TOO_LARGE);
        assertThat(response.getBody()).containsEntry("status", "error");
        assertThat(response.getBody()).containsEntry("code", "upload_limit_exceeded");
        assertThat(response.getBody()).containsEntry("error_code", "UPLOAD_LIMIT_EXCEEDED");
        assertThat(response.getBody()).containsEntry("max_files_per_request", 12);
        assertThat(response.getBody()).containsEntry("max_file_size_bytes", 50L * 1024 * 1024);
        assertThat(response.getBody()).containsEntry("max_batch_size_bytes", 180L * 1024 * 1024);
        assertThat(response.getBody()).containsEntry("trace_id", "upload-trace-1");
        assertThat(String.valueOf(response.getBody().get("message"))).contains("50 MB", "180 MB");
    }

    @Test
    void replacesUntrustedOversizedTraceId() {
        UploadLimitExceptionHandler handler = new UploadLimitExceptionHandler(new TaskDispatchProperties());
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Trace-Id", "x".repeat(129));

        var response = handler.uploadLimit(new MaxUploadSizeExceededException(1), request);

        assertThat(response.getBody().get("trace_id")).asString().hasSize(36).isNotEqualTo("x".repeat(129));
    }
}
