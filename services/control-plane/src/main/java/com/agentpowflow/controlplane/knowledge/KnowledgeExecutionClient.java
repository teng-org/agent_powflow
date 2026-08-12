package com.agentpowflow.controlplane.knowledge;

import com.agentpowflow.controlplane.runs.InternalApiProperties;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.springframework.web.multipart.MultipartFile;

@Component
public class KnowledgeExecutionClient {
    private final KnowledgeExecutionProperties properties;
    private final InternalApiProperties internalApiProperties;
    private final RestClient restClient;
    private final RestClient uploadRestClient;

    public KnowledgeExecutionClient(
            KnowledgeExecutionProperties properties,
            InternalApiProperties internalApiProperties
    ) {
        this.properties = properties;
        this.internalApiProperties = internalApiProperties;
        Duration timeout = Duration.ofMillis(Math.max(1, properties.getRequestTimeoutMs()));
        HttpClient httpClient = HttpClient.newBuilder().connectTimeout(timeout).build();
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(httpClient);
        requestFactory.setReadTimeout(timeout);
        this.restClient = RestClient.builder().requestFactory(requestFactory).build();
        Duration uploadTimeout = Duration.ofMillis(Math.max(properties.getRequestTimeoutMs(), 120_000));
        HttpClient uploadHttpClient = HttpClient.newBuilder().connectTimeout(timeout).build();
        JdkClientHttpRequestFactory uploadRequestFactory = new JdkClientHttpRequestFactory(uploadHttpClient);
        uploadRequestFactory.setReadTimeout(uploadTimeout);
        this.uploadRestClient = RestClient.builder().requestFactory(uploadRequestFactory).build();
    }

    public void deleteKnowledgeBase(String tenantId, String kbId) {
        try {
            restClient.delete()
                    .uri(properties.getExecutionBaseUrl() + "/api/v1/internal/kbs/{tenantId}/{kbId}", tenantId, kbId)
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .retrieve()
                    .toBodilessEntity();
        } catch (RestClientResponseException exception) {
            if (exception.getStatusCode().value() == 404) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "knowledge base not found", exception);
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution knowledge cleanup failed", exception);
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution knowledge cleanup unavailable", exception);
        }
    }

    public Map<String, Object> platformReadiness() {
        try {
            Map<String, Object> response = restClient.get()
                    .uri(properties.getExecutionBaseUrl() + "/api/v1/internal/platform-readiness")
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .retrieve()
                    .body(new ParameterizedTypeReference<>() {});
            return response == null ? Map.of("status", "unavailable", "provider_ready", false) : response;
        } catch (RuntimeException exception) {
            return Map.of("status", "unavailable", "provider_ready", false);
        }
    }

    public boolean revokeTasks(List<String> taskIds) {
        if (taskIds == null || taskIds.isEmpty()) return true;
        try {
            restClient.post()
                    .uri(properties.getExecutionBaseUrl() + "/api/v1/internal/tasks/revoke")
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .body(Map.of("task_ids", taskIds))
                    .retrieve()
                    .toBodilessEntity();
            return true;
        } catch (RuntimeException exception) {
            return false;
        }
    }

    public Map<String, Object> describeDocuments(String tenantId, String kbId, List<String> docIds) {
        try {
            Map<String, Object> response = restClient.post()
                    .uri(properties.getExecutionBaseUrl()
                            + "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/describe", tenantId, kbId)
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("doc_ids", docIds))
                    .retrieve()
                    .body(new ParameterizedTypeReference<>() {});
            if (response == null || !"ok".equals(String.valueOf(response.get("status")))) {
                throw new IllegalStateException("execution document descriptor returned an invalid response");
            }
            return response;
        } catch (RestClientResponseException exception) {
            if (exception.getStatusCode().value() == 404) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "document batch resource not found", exception);
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution document descriptor failed", exception);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution document descriptor unavailable", exception);
        }
    }

    public Map<String, Object> listDocuments(String tenantId, String kbId, int limit) {
        try {
            Map<String, Object> response = restClient.get()
                    .uri(properties.getExecutionBaseUrl()
                                    + "/api/v1/internal/kbs/{tenantId}/{kbId}/documents?limit={limit}",
                            tenantId, kbId, Math.min(Math.max(limit, 1), 500))
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .retrieve()
                    .body(new ParameterizedTypeReference<>() {});
            return response == null ? Map.of("status", "ok", "items", List.of()) : response;
        } catch (RestClientResponseException exception) {
            if (exception.getStatusCode().value() == 404) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "knowledge base documents not found", exception);
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution document list failed", exception);
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution document list unavailable", exception);
        }
    }

    public Map<String, Object> listDocumentChunks(String tenantId, String kbId, String docId, int limit) {
        try {
            Map<String, Object> response = restClient.get()
                    .uri(properties.getExecutionBaseUrl()
                                    + "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/{docId}/chunks?limit={limit}",
                            tenantId, kbId, docId, Math.min(Math.max(limit, 1), 500))
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .retrieve().body(new ParameterizedTypeReference<>() {});
            return response == null ? Map.of("status", "ok", "items", List.of()) : response;
        } catch (RestClientResponseException exception) {
            if (exception.getStatusCode().value() == 404) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "document chunks not found", exception);
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution chunk list failed", exception);
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution chunk list unavailable", exception);
        }
    }

    public Map<String, Object> listRagIndexes(String tenantId, String kbId) {
        try {
            Map<String, Object> response = restClient.get()
                    .uri(properties.getExecutionBaseUrl()
                            + "/api/v1/internal/kbs/{tenantId}/{kbId}/rag/indexes", tenantId, kbId)
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .retrieve().body(new ParameterizedTypeReference<>() {});
            return response == null ? Map.of("status", "ok", "items", List.of()) : response;
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "RAG index list unavailable", exception);
        }
    }

    public Map<String, Object> bootstrapRagIndex(String tenantId, String kbId) {
        return ragMutation(HttpMethod.POST, "/bootstrap", Map.of(), tenantId, kbId);
    }

    public Map<String, Object> buildRagIndex(String tenantId, String kbId, Map<String, Object> body) {
        return ragMutation(HttpMethod.POST, "/build", body, tenantId, kbId);
    }

    public Map<String, Object> evaluateRagIndex(
            String tenantId, String kbId, String revision, Map<String, Object> body
    ) {
        return ragMutation(HttpMethod.POST, "/" + revision + "/evaluate", body, tenantId, kbId);
    }

    public Map<String, Object> activateRagIndex(
            String tenantId, String kbId, String revision, String actorId, boolean rollback
    ) {
        return ragMutation(
                HttpMethod.POST,
                rollback ? "/rollback" : "/activate",
                Map.of("revision", revision, "actor_id", actorId),
                tenantId,
                kbId
        );
    }

    public Map<String, Object> cleanupFailedRagIndex(String tenantId, String kbId, String revision) {
        return ragMutation(HttpMethod.DELETE, "/" + revision, null, tenantId, kbId);
    }

    public void dispatchTask(String payloadJson) {
        try {
            restClient.post()
                    .uri(properties.getExecutionBaseUrl() + "/api/v1/internal/tasks/dispatch")
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(payloadJson)
                    .retrieve()
                    .toBodilessEntity();
        } catch (RuntimeException exception) {
            throw new IllegalStateException("execution task dispatch failed", exception);
        }
    }

    public Map<String, Object> stageDocument(
            String tenantId,
            String kbId,
            String docId,
            String taskId,
            String processMode,
            String chunkMethod,
            String chunkDelimiter,
            int chunkSize,
            int chunkOverlap,
            MultipartFile file
    ) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("doc_id", docId);
        parts.add("task_id", taskId == null ? "" : taskId);
        parts.add("process_mode", processMode);
        parts.add("chunk_method", chunkMethod);
        if (chunkDelimiter != null && !chunkDelimiter.isBlank()) parts.add("chunk_delimiter", chunkDelimiter);
        parts.add("chunk_size", String.valueOf(chunkSize));
        parts.add("chunk_overlap", String.valueOf(chunkOverlap));
        String filename = file.getOriginalFilename() == null ? "upload.bin" : file.getOriginalFilename();
        HttpHeaders fileHeaders = new HttpHeaders();
        fileHeaders.setContentDispositionFormData("file", filename);
        if (file.getContentType() != null && !file.getContentType().isBlank()) {
            fileHeaders.setContentType(MediaType.parseMediaType(file.getContentType()));
        }
        parts.add("file", new HttpEntity<>(file.getResource(), fileHeaders));
        try {
            Map<String, Object> response = uploadRestClient.post()
                    .uri(properties.getExecutionBaseUrl() + "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/stage", tenantId, kbId)
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(parts)
                    .retrieve()
                    .body(new ParameterizedTypeReference<>() {});
            if (response == null || !"staged".equals(String.valueOf(response.get("status")))) {
                throw new IllegalStateException("execution document staging returned an invalid response");
            }
            return response;
        } catch (RestClientResponseException exception) {
            String detail = exception.getResponseBodyAsString();
            throw new IllegalStateException(
                    "execution document staging rejected upload: " + truncate(detail, 1000), exception
            );
        } catch (RuntimeException exception) {
            if (exception instanceof IllegalStateException) throw exception;
            throw new IllegalStateException("execution document staging unavailable", exception);
        }
    }

    public boolean cleanupStagedDocument(String tenantId, String kbId, String docId, String taskId) {
        try {
            restClient.delete()
                    .uri(properties.getExecutionBaseUrl()
                                    + "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/{docId}/staging?task_id={taskId}",
                            tenantId, kbId, docId, taskId == null ? "" : taskId)
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .retrieve()
                    .toBodilessEntity();
            return true;
        } catch (RestClientResponseException exception) {
            return exception.getStatusCode().value() == 404;
        } catch (RuntimeException exception) {
            return false;
        }
    }

    public Map<String, Object> setDocumentFrozen(String tenantId, String kbId, String docId, boolean frozen) {
        return metadataMutation(
                HttpMethod.PATCH,
                "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/{docId}/freeze",
                Map.of("frozen", frozen), tenantId, kbId, docId
        );
    }

    public Map<String, Object> setChunkFrozen(
            String tenantId, String kbId, String docId, String chunkId, boolean frozen
    ) {
        return metadataMutation(
                HttpMethod.PATCH,
                "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/{docId}/chunks/{chunkId}/freeze",
                Map.of("frozen", frozen), tenantId, kbId, docId, chunkId
        );
    }

    public Map<String, Object> upsertChunkDraft(
            String tenantId, String kbId, String docId, String chunkId, String action, String text
    ) {
        Map<String, Object> body = "delete".equals(action)
                ? Map.of("action", "delete")
                : Map.of("action", "edit", "text", text == null ? "" : text);
        return metadataMutation(
                HttpMethod.PUT,
                "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/{docId}/chunks/{chunkId}/draft",
                body, tenantId, kbId, docId, chunkId
        );
    }

    public Map<String, Object> clearChunkDraft(String tenantId, String kbId, String docId, String chunkId) {
        return metadataMutation(
                HttpMethod.DELETE,
                "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/{docId}/chunks/{chunkId}/draft",
                null, tenantId, kbId, docId, chunkId
        );
    }

    public Map<String, Object> replaceChunkText(
            String tenantId, String kbId, String docId, String searchText, String replacementText,
            boolean caseSensitive, boolean apply
    ) {
        return metadataMutation(
                HttpMethod.POST,
                "/api/v1/internal/kbs/{tenantId}/{kbId}/documents/{docId}/chunks/replace",
                Map.of(
                        "search_text", searchText,
                        "replacement_text", replacementText == null ? "" : replacementText,
                        "case_sensitive", caseSensitive,
                        "apply", apply
                ),
                tenantId, kbId, docId
        );
    }

    private Map<String, Object> metadataMutation(
            HttpMethod method, String path, Object body, Object... uriVariables
    ) {
        try {
            RestClient.RequestBodySpec request = restClient.method(method)
                    .uri(properties.getExecutionBaseUrl() + path, uriVariables)
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .contentType(MediaType.APPLICATION_JSON);
            Map<String, Object> response = (body == null ? request : request.body(body))
                    .retrieve()
                    .body(new ParameterizedTypeReference<>() {});
            if (response == null || !"ok".equals(String.valueOf(response.get("status")))) {
                throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution metadata mutation returned an invalid response");
            }
            return response;
        } catch (RestClientResponseException exception) {
            if (exception.getStatusCode().value() == 404) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "document metadata resource not found", exception);
            }
            if (exception.getStatusCode().is4xxClientError()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "document metadata mutation rejected", exception);
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution metadata mutation failed", exception);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "execution metadata mutation unavailable", exception);
        }
    }

    private Map<String, Object> ragMutation(
            HttpMethod method, String suffix, Object body, String tenantId, String kbId
    ) {
        try {
            // Index builds and Golden Set evaluations can legitimately outlive the
            // short metadata-request timeout, so use the bounded long-running client.
            RestClient.RequestBodySpec request = uploadRestClient.method(method)
                    .uri(properties.getExecutionBaseUrl()
                            + "/api/v1/internal/kbs/{tenantId}/{kbId}/rag/indexes" + suffix, tenantId, kbId)
                    .header("X-Internal-Token", internalApiProperties.getToken())
                    .contentType(MediaType.APPLICATION_JSON);
            Map<String, Object> response = (body == null ? request : request.body(body))
                    .retrieve().body(new ParameterizedTypeReference<>() {});
            if (response == null || !"ok".equals(String.valueOf(response.get("status")))) {
                throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "RAG index operation returned an invalid response");
            }
            return response;
        } catch (RestClientResponseException exception) {
            if (exception.getStatusCode().is4xxClientError()) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "RAG index operation rejected", exception);
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "RAG index operation failed", exception);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RuntimeException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "RAG index operation unavailable", exception);
        }
    }

    private String truncate(String value, int max) {
        String safe = value == null ? "" : value;
        return safe.length() <= max ? safe : safe.substring(0, max);
    }
}
