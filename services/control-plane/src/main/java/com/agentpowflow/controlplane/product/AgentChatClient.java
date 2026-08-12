package com.agentpowflow.controlplane.product;

import com.agentpowflow.controlplane.runs.InternalApiProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.time.Duration;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.function.Consumer;

import static org.springframework.http.HttpStatus.BAD_GATEWAY;

@Component
public class AgentChatClient {
    private final InternalApiProperties properties;
    private final RestClient restClient;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;

    public AgentChatClient(InternalApiProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
        Duration timeout = Duration.ofMillis(Math.max(1000, properties.getChatRequestTimeoutMs()));
        HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
        this.httpClient = client;
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(client);
        factory.setReadTimeout(timeout);
        this.restClient = RestClient.builder().requestFactory(factory).build();
    }

    public AgentResult invoke(
            String tenantId,
            String actorId,
            JsonNode snapshot,
            JsonNode input,
            String conversationId
    ) {
        try {
            JsonNode response = restClient.post()
                    .uri(properties.getChatBaseUrl() + "/api/v1/internal/agents/invoke")
                    .header("X-Internal-Token", properties.getToken())
                    .body(Map.of(
                            "tenant_id", tenantId,
                            "actor_id", actorId,
                            "agent_snapshot", snapshot,
                            "input", input,
                            "conversation_id", conversationId == null ? "" : conversationId
                    ))
                    .retrieve()
                    .body(JsonNode.class);
            if (response == null || response.path("run_id").asText("").isBlank()) {
                throw new ResponseStatusException(BAD_GATEWAY, "agent runtime returned an invalid response");
            }
            return new AgentResult(
                    response.path("run_id").asText(),
                    response.path("conversation_id").asText(""),
                    response.path("output").asText(""),
                    response.path("citations").isArray()
                            ? response.path("citations") : JsonNodeFactory.instance.arrayNode(),
                    response.path("metrics").isObject()
                            ? response.path("metrics") : JsonNodeFactory.instance.objectNode(),
                    response.path("memory").isObject()
                            ? response.path("memory") : JsonNodeFactory.instance.objectNode()
            );
        } catch (RestClientResponseException error) {
            throw new ResponseStatusException(BAD_GATEWAY, "agent runtime is temporarily unavailable", error);
        } catch (RestClientException error) {
            throw new ResponseStatusException(BAD_GATEWAY, "agent runtime is temporarily unavailable", error);
        }
    }

    public StreamResult stream(
            String tenantId,
            String actorId,
            JsonNode snapshot,
            JsonNode input,
            String conversationId,
            Consumer<StreamEvent> consumer
    ) {
        try {
            String body = objectMapper.writeValueAsString(Map.of(
                    "tenant_id", tenantId,
                    "actor_id", actorId,
                    "agent_snapshot", snapshot,
                    "input", input,
                    "conversation_id", conversationId == null ? "" : conversationId
            ));
            HttpRequest request = HttpRequest.newBuilder()
                    .uri(java.net.URI.create(properties.getChatBaseUrl() + "/api/v1/internal/agents/invoke/stream"))
                    .timeout(Duration.ofMillis(Math.max(1000, properties.getChatRequestTimeoutMs())))
                    .header("Content-Type", "application/json")
                    .header("Accept", "text/event-stream")
                    .header("X-Internal-Token", properties.getToken())
                    .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                    .build();
            HttpResponse<java.io.InputStream> response = httpClient.send(
                    request, HttpResponse.BodyHandlers.ofInputStream()
            );
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new ResponseStatusException(BAD_GATEWAY, "agent runtime is temporarily unavailable");
            }
            String eventName = "message";
            String runId = "";
            String resolvedConversationId = "";
            JsonNode citations = JsonNodeFactory.instance.arrayNode();
            JsonNode memory = JsonNodeFactory.instance.objectNode();
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(response.body(), StandardCharsets.UTF_8)
            )) {
                String line;
                while ((line = reader.readLine()) != null) {
                    if (line.startsWith("event:")) {
                        eventName = line.substring(6).trim();
                    } else if (line.startsWith("data:")) {
                        JsonNode data = objectMapper.readTree(line.substring(5).trim());
                        runId = data.path("run_id").asText(runId);
                        resolvedConversationId = data.path("conversation_id").asText(resolvedConversationId);
                        if (data.path("citations").isArray()) citations = data.path("citations");
                        if (data.path("memory").isObject()) memory = data.path("memory");
                        consumer.accept(new StreamEvent(eventName, data));
                        if ("error".equals(eventName)) {
                            throw new ResponseStatusException(BAD_GATEWAY, "agent runtime failed");
                        }
                    } else if (line.isBlank()) {
                        eventName = "message";
                    }
                }
            }
            if (runId.isBlank()) {
                throw new ResponseStatusException(BAD_GATEWAY, "agent runtime returned an invalid stream");
            }
            return new StreamResult(runId, resolvedConversationId, citations, memory);
        } catch (ResponseStatusException error) {
            throw error;
        } catch (Exception error) {
            throw new ResponseStatusException(BAD_GATEWAY, "agent runtime is temporarily unavailable", error);
        }
    }

    public record AgentResult(
            String runId,
            String conversationId,
            String output,
            JsonNode citations,
            JsonNode metrics,
            JsonNode memory
    ) {}

    public record StreamEvent(String event, JsonNode data) {}
    public record StreamResult(String runId, String conversationId, JsonNode citations, JsonNode memory) {}
}
